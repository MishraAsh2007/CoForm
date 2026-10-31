import pytest
from fastapi.testclient import TestClient

from scienceclaw.app import create_app
from scienceclaw.diagnosis import physics
from scienceclaw.domain import Geometry, ModelParameters, Protocol
from scienceclaw.mechanics import generate_stl, predict_deflection
from scienceclaw.service import ScienceClaw


@pytest.fixture
def service(tmp_path):
    s = ScienceClaw(tmp_path)
    yield s
    s.store.close()


def codes(diagnosis):
    return [i["code"] for i in diagnosis["issues"]]


def test_physics_matches_hand_calculation_for_a_solid_bar():
    g = Geometry(section_family="solid_bar", base_width_m=.020, wall_height_m=.005, thickness_m=.001)
    p = physics(g, ModelParameters(youngs_modulus_pa=2e9, rotational_compliance_rad_per_nm=.1), Protocol())
    b, h, force, a = .020, .006, .3, .12
    inertia = b * h**3 / 12
    assert p["second_moment_m4"] == pytest.approx(inertia)
    assert p["bending_m"] == pytest.approx(force * a**3 / (3 * 2e9 * inertia))
    assert p["mount_rotation_m"] == pytest.approx(force * .1 * a**2)
    assert p["predicted_m"] == pytest.approx(p["bending_m"] + p["mount_rotation_m"])
    assert p["root_stress_pa"] == pytest.approx(force * a * (h / 2) / inertia)
    # A deeper section is the efficient lever for bending.
    assert p["sensitivity"]["wall_height_m"]["deflection_change_m"] < p["sensitivity"]["base_width_m"]["deflection_change_m"] < 0


def test_agents_refuse_to_redesign_from_a_contradicted_model_then_repair_after_diagnosis(service):
    run = service.create_run(mode="simulated", protocol=Protocol(max_deflection_m=.0015))
    run_id, reference = run["run"]["id"], run["candidates"][0]["id"]
    before = service.diagnose(run_id, reference)
    assert {"model_discrepancy", "model_unresolved"} <= set(codes(before))
    assert "second, well-separated station" in before["issues"][0]["detail"]
    result = service.repair(run_id, reference)
    assert result["repair"]["status"] == "diagnose_first" and result["candidate"] is None
    assert service.state(run_id)["budget"]["designs_used"] == 0

    service.step(run_id)
    service.step(run_id)
    after = service.diagnose(run_id, reference)
    assert after["model"]["basis"] == "fitted" and "predicted_over_limit" in codes(after)
    issue = next(i for i in after["issues"] if i["code"] == "predicted_over_limit")
    assert "mount rotation" in issue["detail"]  # The simulator's compliant clamp dominates.
    assert {f["priority"] for f in issue["fixes"] if f["kind"] == "geometry"} == {"least_material", "smallest_change", "most_margin"}

    designs_before = service.state(run_id)["budget"]["designs_used"]
    result = service.repair(run_id, reference)
    repair = result["repair"]
    assert repair["status"] == "agreed" and len(repair["proposals"]) == 3
    assert all(p["valid"] for p in repair["proposals"])
    assert any(not r["support"] and r["agent"] == "measurement" for r in repair["reviews"])
    new = result["candidate"]
    assert new["parent_id"] == reference and new["proposed_by"].startswith("repair:")
    assert result["diagnosis_after"]["status"] == "ok"
    state = service.state(run_id)
    assert state["budget"]["designs_used"] == designs_before + 1 and state["repairs"][-1]["new_candidate_id"] == new["id"]
    # A repair cannot recreate an existing immutable geometry.
    assert service.repair(run_id, reference)["repair"]["selected"]["geometry"] != new["geometry"]


def _physical_cycles(service, protocol, truth):
    """Record the four pilot cycles and one diagnostic cycle from a declared truth."""
    run = service.create_run(mode="physical", protocol=protocol)
    run_id = run["run"]["id"]
    for _ in range(5):
        exp = service.step(run_id)["experiment"]
        if not any(f["specimen_id"] == exp["specimen_id"] for f in service.state(run_id)["fabrications"]):
            service.fabricate(run_id, exp["candidate_id"], exp["specimen_id"], notes="Printed, calipers")
        geometry = Geometry(**run["candidates"][0]["geometry"])
        delta = predict_deflection(geometry, truth, exp["force_n"], exp["load_position_m"])["displacement_m"]
        service.record(run_id, exp["id"], displacement_m=delta, uncertainty_m=1e-5, residual_m=0, fixture_motion_m=0,
                       dwell_s=3, recovery_s=3, human_notes="Dial gauge", actual_force_n=exp["force_n"],
                       actual_load_position_m=exp["load_position_m"])
    return run_id, run["candidates"][0]["id"]


@pytest.mark.parametrize("truth,expected", [
    (ModelParameters(youngs_modulus_pa=1.0e9), "similar at every station"),
    (ModelParameters(rotational_compliance_rad_per_nm=.5), "larger near the clamp"),
])
def test_station_pattern_names_the_missing_physics(service, truth, expected):
    run_id, reference = _physical_cycles(service, Protocol(), truth)
    discrepancy = next(i for i in service.diagnose(run_id, reference)["issues"] if i["code"] == "model_discrepancy")
    assert expected in discrepancy["detail"]
    assert len({r["load_position_m"] for r in discrepancy["evidence"]["comparisons"]}) == 2


def test_invalid_measurement_and_permanent_set_are_explained(service):
    run = service.create_run(mode="physical")
    run_id = run["run"]["id"]
    exp = service.step(run_id)["experiment"]
    service.fabricate(run_id, exp["candidate_id"], exp["specimen_id"], notes="Printed")
    service.record(run_id, exp["id"], displacement_m=.001, uncertainty_m=1e-5, residual_m=0, fixture_motion_m=.001,
                   dwell_s=3, recovery_s=3, human_notes="Clamp moved", actual_force_n=exp["force_n"], actual_load_position_m=exp["load_position_m"])
    exp = service.step(run_id)["experiment"]
    service.record(run_id, exp["id"], displacement_m=.001, uncertainty_m=1e-5, residual_m=.001, fixture_motion_m=0,
                   dwell_s=3, recovery_s=3, human_notes="Did not return", actual_force_n=exp["force_n"], actual_load_position_m=exp["load_position_m"])
    found = {i["code"]: i for i in service.diagnose(run_id, run["candidates"][0]["id"])["issues"]}
    assert "clamp" in found["invalid_measurement"]["fixes"][0]["description"]
    assert "Retire it" in found["permanent_set"]["detail"]


def test_api_upload_diagnose_and_repair(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        run = client.post("/api/runs", json={"protocol": {**Protocol().model_dump(mode="json"), "max_deflection_m": .0015}}).json()
        run_id, reference = run["run"]["id"], run["candidates"][0]["id"]
        wrong = generate_stl(Geometry(base_width_m=.030)).encode()
        cad = client.post(f"/api/runs/{run_id}/candidates/{reference}/cad-upload?filename=onshape-export.stl", content=wrong).json()
        assert cad["source"] == "manual_export" and cad["check"]["status"] == "fail"
        diagnosis = client.get(f"/api/runs/{run_id}/candidates/{reference}/diagnosis").json()
        mismatch = next(i for i in diagnosis["issues"] if i["code"] == "cad_mismatch")
        assert "25 mm expected, 30 mm exported" in mismatch["detail"]
        assert "sc_base_width = 25 mm" in mismatch["fixes"][0]["description"]
        stopped = client.post(f"/api/runs/{run_id}/print-pipeline", json={"candidate_id": reference, "cad_source": "uploaded",
            "profile": {"machine_type": "FORM-4-0", "material_code": "X", "layer_thickness_mm": .1, "minimum_wall_mm": .5}}).json()
        assert stopped["status"] == "stopped" and stopped["stage"] == "cad" and stopped["diagnosis"]["status"] == "problems"
        for _ in range(2):
            client.post(f"/api/runs/{run_id}/step", json={"live": False})
        result = client.post(f"/api/runs/{run_id}/candidates/{reference}/repair", json={"live": False}).json()
        assert result["repair"]["status"] == "agreed" and result["candidate"]["parent_id"] == reference
        assert client.post(f"/api/runs/{run_id}/candidates/{reference}/cad-upload?filename=x.exe", content=wrong).status_code == 400
    app.state.service.store.close()
