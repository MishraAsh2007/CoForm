from pathlib import Path
import sys

import pytest

from scienceclaw.cad import FreeCADLink, find_freecad_cmd, print_pipeline
from scienceclaw.domain import Geometry
from scienceclaw.formlabs import Manufacturing
from scienceclaw.service import ScienceClaw

ROOT = Path(__file__).resolve().parents[1]
FAKE_WORKER = f'''
import json, os, sys
sys.path.insert(0, {str(ROOT)!r})
from scienceclaw.domain import Geometry
from scienceclaw.mechanics import generate_stl, section_properties
job = json.load(open(os.environ["SCIENCECLAW_FREECAD_JOB"], encoding="utf-8"))
result = {{"ok": True, "freecad_version": "fake-1.1"}}
if job["action"] == "build":
    if os.getenv("FAKE_FREECAD_FAIL"):
        result = {{"ok": False, "freecad_version": "fake-1.1", "error": "FreeCAD model failed to rebuild: WallB"}}
    else:
        v = job["values_mm"]
        g = Geometry(section_family=job["family"], length_m=v["sc_length"] / 1000, base_width_m=v["sc_base_width"] / 1000,
                     wall_height_m=v["sc_wall_height"] / 1000, thickness_m=v["sc_thickness"] / 1000)
        props = section_properties(g)
        error = float(os.getenv("FAKE_FREECAD_INERTIA_ERROR", "0"))
        open(job["stl"], "w").write(generate_stl(g))
        open(job["fcstd"], "wb").write(b"fcstd-" + json.dumps(v).encode())
        open(job["step"], "wb").write(b"step-" + json.dumps(v).encode())
        if not os.path.exists(job["master"]):
            open(job["master"], "wb").write(b"master")
        result.update(volume_mm3=props["area_m2"] * g.length_m * 1e9, area_mm2=props["area_m2"] * 1e6,
                      second_moment_mm4=props["second_moment_m4"] * 1e12 * (1 + error), extents_mm=[1, 2, 3],
                      solids=1, is_valid=True)
json.dump(result, open(job["result"], "w"))
'''


@pytest.fixture
def lab(tmp_path):
    worker = tmp_path / "fake_worker.py"
    worker.write_text(FAKE_WORKER, encoding="utf-8")
    service = ScienceClaw(tmp_path / "data")
    manufacturing = Manufacturing(service.store)
    yield service, manufacturing, FreeCADLink(service.store, manufacturing, command=[sys.executable, str(worker)])
    service.store.close()


def test_freecad_snapshot_checks_physics_and_is_reused(lab):
    service, _, freecad = lab
    assert freecad.status()["available"] is True
    run = service.create_run(mode="physical")
    run_id, reference = run["run"]["id"], run["candidates"][0]["id"]
    revision = freecad.sync_candidate(run_id, reference)
    assert revision["source"] == "freecad" and revision["check"]["status"] == "pass"
    names = {c["name"] for c in revision["check"]["checks"]}
    assert {"freecad_kernel_volume", "freecad_kernel_second_moment", "volume_matches_section"} <= names
    assert revision["variables"]["sc_base_width"] == "25 mm"
    assert freecad._file(revision["freecad"]["fcstd"], revision["freecad"]["fcstd_sha256"]).is_file()
    assert service.store.get(revision["asset_id"], "print_asset")["source"] == "freecad_export"
    assert freecad.sync_candidate(run_id, reference)["id"] == revision["id"]
    # A modified snapshot is detected rather than silently served.
    freecad._file(revision["freecad"]["step"]).write_bytes(b"edited")
    with pytest.raises(ValueError, match="modified"):
        freecad._file(revision["freecad"]["step"], revision["freecad"]["step_sha256"])


def test_kernel_disagreement_stops_pipeline_with_diagnosis(lab, monkeypatch):
    service, manufacturing, freecad = lab
    monkeypatch.setenv("FAKE_FREECAD_INERTIA_ERROR", "0.05")
    run = service.create_run(mode="physical")
    run_id, reference = run["run"]["id"], run["candidates"][0]["id"]
    result = print_pipeline(service, None, manufacturing, run_id, reference, None, cad_source="freecad", freecad=freecad)
    assert result["status"] == "stopped" and result["stage"] == "cad"
    mismatch = next(i for i in result["diagnosis"]["issues"] if i["code"] == "cad_mismatch")
    assert "freecad_kernel_second_moment" in mismatch["detail"]
    assert "FreeCAD" in mismatch["fixes"][0]["description"]


def test_freecad_rebuild_failure_is_reported(lab, monkeypatch):
    service, manufacturing, freecad = lab
    monkeypatch.setenv("FAKE_FREECAD_FAIL", "1")
    run = service.create_run(mode="physical")
    result = print_pipeline(service, None, manufacturing, run["run"]["id"], run["candidates"][0]["id"], None,
                            cad_source="freecad", freecad=freecad)
    assert result["status"] == "stopped" and "WallB" in result["detail"]


def test_missing_freecad_is_explained(tmp_path, monkeypatch):
    monkeypatch.setenv("FREECAD_CMD", str(tmp_path / "missing.exe"))
    assert find_freecad_cmd() is None
    service = ScienceClaw(tmp_path / "data")
    status = FreeCADLink(service.store, Manufacturing(service.store)).status()
    assert status["available"] is False and "FreeCAD not found" in status["detail"]
    service.store.close()


@pytest.mark.skipif(find_freecad_cmd() is None, reason="FreeCAD is not installed")
@pytest.mark.parametrize("family,height", [("u_channel", .010), ("solid_bar", .005), ("rectangular_tube", .010)])
def test_real_freecad_matches_the_physics(tmp_path, family, height):
    service = ScienceClaw(tmp_path)
    try:
        freecad = FreeCADLink(service.store, Manufacturing(service.store))
        run = service.create_run(mode="physical", geometry=Geometry(section_family=family, wall_height_m=height))
        revision = freecad.sync_candidate(run["run"]["id"], run["candidates"][0]["id"])
        assert revision["check"]["status"] == "pass", revision["check"]
        assert revision["kernel"]["solids"] == 1 and revision["kernel"]["is_valid"]
        candidate = service.add_candidate(run["run"]["id"], Geometry(section_family=family, base_width_m=.030, wall_height_m=height + .002))
        assert freecad.sync_candidate(run["run"]["id"], candidate["id"])["check"]["status"] == "pass"
    finally:
        service.store.close()
