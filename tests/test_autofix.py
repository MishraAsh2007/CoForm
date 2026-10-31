from fastapi.testclient import TestClient

from scienceclaw.app import create_app
from scienceclaw.domain import Geometry, Protocol
from scienceclaw.mechanics import generate_stl


def test_one_button_fix_covers_each_kind_of_problem(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        protocol = {**Protocol().model_dump(mode="json"), "max_deflection_m": .0015}
        run = client.post("/api/runs", json={"protocol": protocol}).json()
        run_id, reference = run["run"]["id"], run["candidates"][0]["id"]
        fix = lambda cid: client.post(f"/api/runs/{run_id}/candidates/{cid}/fix", json={"live": False}).json()  # noqa: E731

        # 1. Model contradicted by the pilot data: agents test first, no redesign.
        first = fix(reference)
        assert [a["kind"] for a in first["actions"]] == ["diagnostic_test"] and first["repair"] is None
        client.post(f"/api/runs/{run_id}/step", json={"live": False})

        # 2. A CAD export that is not the analyzed part is rebuilt from the design's parameters.
        wrong = generate_stl(Geometry(base_width_m=.030)).encode()
        client.post(f"/api/runs/{run_id}/candidates/{reference}/cad-upload?filename=edited.stl", content=wrong)
        rebuilt = fix(reference)
        kinds = [a["kind"] for a in rebuilt["actions"]]
        assert kinds[0] == "cad_rebuilt" and "now matches" in rebuilt["actions"][0]["summary"]

        # 3. Requirement violations: agents agree on a repair and its CAD is built.
        assert "repaired" in kinds
        assert rebuilt["candidate_id"] != reference and rebuilt["diagnosis_after"]["status"] == "ok"
        state = client.get(f"/api/runs/{run_id}").json()
        assert any(r["candidate_id"] == rebuilt["candidate_id"] for r in state["cad_revisions"])
        assert not any(i["code"] == "cad_mismatch" for i in client.get(f"/api/runs/{run_id}/candidates/{reference}/diagnosis").json()["issues"])

        # 4. Nothing left to fix on the repaired design.
        assert fix(rebuilt["candidate_id"])["actions"][0]["kind"] == "nothing"
    app.state.service.store.close()


def test_problems_only_a_person_can_fix_are_listed(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        run = client.post("/api/runs", json={"mode": "physical"}).json()
        run_id, reference = run["run"]["id"], run["candidates"][0]["id"]
        exp = client.post(f"/api/runs/{run_id}/step", json={"live": False}).json()["experiment"]
        client.post(f"/api/runs/{run_id}/fabrications", json={"candidate_id": reference, "specimen_id": exp["specimen_id"], "notes": "Printed"})
        client.post(f"/api/runs/{run_id}/experiments/{exp['id']}/observations", json={
            "actual_force_n": exp["force_n"], "actual_load_position_m": exp["load_position_m"], "displacement_m": .001,
            "uncertainty_m": 1e-5, "residual_m": 0, "fixture_motion_m": .001, "dwell_s": 3, "recovery_s": 3, "human_notes": "Clamp slipped"})
        result = client.post(f"/api/runs/{run_id}/candidates/{reference}/fix", json={"live": False}).json()
        needs = {n["title"]: n["action"] for n in result["needs_you"]}
        assert "A load cycle cannot be used" in needs and "clamp" in needs["A load cycle cannot be used"]
    app.state.service.store.close()
