import base64
import re
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

from scienceclaw.app import create_app
from scienceclaw.cad import _triangles
from scienceclaw.domain import Geometry, Protocol
from scienceclaw.formlabs import Manufacturing
from scienceclaw.fusion import FusionLink, _stl_binary
from scienceclaw.mechanics import generate_stl, section_properties
from scienceclaw.service import ScienceClaw


def export(geometry, *, cm=False, extra_volume=0.0, values=None):
    """What the add-in sends: binary STL (optionally in Fusion's cm), parameters, volume."""
    tris = _triangles(generate_stl(geometry).encode())
    if extra_volume:
        tris = tris * np.array([1 + extra_volume, 1, 1])  # e.g. a teammate lengthened a feature
    stl = _stl_binary(tris / 10 if cm else tris)
    mm = values or {"sc_length": geometry.length_m * 1000, "sc_base_width": geometry.base_width_m * 1000,
                    "sc_wall_height": geometry.wall_height_m * 1000, "sc_thickness": geometry.thickness_m * 1000}
    return {"stl_b64": base64.b64encode(stl).decode(), "step_b64": base64.b64encode(b"ISO-10303-21;").decode(),
            "values_mm": mm, "parameters": {k: f"{v:g} mm" for k, v in mm.items()},
            "volume_mm3": section_properties(geometry)["area_m2"] * geometry.length_m * 1e9 * (1 + extra_volume),
            "bodies": 1, "document": "ScienceClaw arm", "version": 3}


class FakeAddin(threading.Thread):
    """Polls like the real add-in and 'rebuilds' from the job's parameters."""

    def __init__(self, link, base):
        super().__init__(daemon=True)
        self.link, self.base, self.stop, self.jobs = link, base, threading.Event(), []

    def run(self):
        while not self.stop.wait(0.02):
            job = self.link.next_job({"addin_version": "test"})
            if job:
                self.jobs.append(job)
                mm = {k: float(re.match(r"[\d.]+", v).group()) for k, v in job["parameters"].items()}
                geometry = Geometry(**{**self.base.model_dump(), "base_width_m": mm["sc_base_width"] / 1000,
                                       "wall_height_m": mm["sc_wall_height"] / 1000})
                self.link.submit_result(job["id"], export(geometry, cm=True))


@pytest.fixture
def lab(tmp_path):
    service = ScienceClaw(tmp_path)
    link = FusionLink(service, Manufacturing(service.store))
    yield service, link
    service.store.close()


def test_agent_build_round_trip_rescales_fusion_cm_export(lab):
    service, link = lab
    run = service.create_run(mode="physical")
    run_id, reference = run["run"]["id"], run["candidates"][0]["id"]
    with pytest.raises(ValueError, match="add-in is not connected"):
        link.build_and_wait(run_id, reference)
    addin = FakeAddin(link, Geometry())
    addin.start()
    try:
        for _ in range(500):
            if link.status()["connected"]:
                break
            threading.Event().wait(0.01)
        candidate = service.add_candidate(run_id, Geometry(base_width_m=.020, wall_height_m=.012))
        revision = link.build_and_wait(run_id, candidate["id"], timeout_s=10)
        assert revision["source"] == "fusion" and revision["fusion"]["units"] == "cm_rescaled_to_mm"
        assert revision["check"]["status"] == "pass"
        assert {c["name"] for c in revision["check"]["checks"]} >= {"fusion_physical_properties_volume", "volume_matches_section"}
        assert addin.jobs[0]["parameters"]["sc_base_width"] == "20 mm" and link.status()["connected"]
        assert link.build_and_wait(run_id, candidate["id"])["id"] == revision["id"]  # reused, no new job
        assert len(addin.jobs) == 1 and link.step_file(run_id, revision["id"]).read_bytes() == b"ISO-10303-21;"
    finally:
        addin.stop.set()


def test_failed_fusion_build_reports_the_reason(lab):
    service, link = lab
    run = service.create_run(mode="physical")
    link.last_poll = __import__("time").monotonic()

    def failing_addin():
        for _ in range(200):
            job = link.next_job()
            if job:
                link.submit_result(job["id"], {"error": "Design lacks parameters sc_length"})
                return
            threading.Event().wait(0.01)
    threading.Thread(target=failing_addin, daemon=True).start()
    with pytest.raises(ValueError, match="lacks parameters sc_length"):
        link.build_and_wait(run["run"]["id"], run["candidates"][0]["id"], timeout_s=5)


def test_user_edits_in_fusion_become_checked_candidates(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        run = client.post("/api/runs", json={"mode": "physical", "protocol": {**Protocol().model_dump(mode="json"),
                                                                             "max_volume_m3": 9e-6}}).json()
        run_id, reference = run["run"]["id"], run["candidates"][0]["id"]
        link = {"run_id": run_id, "candidate_id": reference}

        # Unlinked designs are refused with guidance.
        assert "not linked" in client.post("/api/fusion/designs", json=export(Geometry())).json()["detail"]

        # A wider base in Fusion becomes a new human candidate with a requirements verdict.
        wider = Geometry(base_width_m=.030)
        result = client.post("/api/fusion/designs", json={**export(wider), **link}).json()
        assert result["new_candidate"] and result["check"] == "pass"
        material = next(r for r in result["requirements"] if "Material" in r["label"])
        assert material["passed"] is False  # 30 mm base exceeds the 9 cm³ budget
        state = client.get(f"/api/runs/{run_id}").json()
        new = next(c for c in state["candidates"] if c["id"] == result["candidate_id"])
        assert new["proposed_by"] == "human:fusion" and new["parent_id"] == reference

        # A teammate's extra feature: dimensions unchanged but the solid differs.
        edited = client.post("/api/fusion/designs", json={**export(Geometry(), extra_volume=.05), **link}).json()
        assert not edited["new_candidate"] and edited["check"] == "fail"
        assert any("CAD export is not the analyzed part" in i["title"] for i in edited["issues"])

        # Frozen and off-grid dimensions are explained, not silently accepted.
        thick = export(Geometry(thickness_m=.0015))
        assert "frozen" in client.post("/api/fusion/designs", json={**thick, **link}).json()["detail"]
        off_grid = export(Geometry(base_width_m=.0255))
        assert "1 mm grid" in client.post("/api/fusion/designs", json={**off_grid, **link}).json()["detail"]
    app.state.service.store.close()
