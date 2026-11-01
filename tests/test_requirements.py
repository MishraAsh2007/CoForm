import pytest

from coform.domain import Geometry, ModelParameters, Protocol
from coform.mechanics import optimize_geometry, requirement_checks, root_stress, section_properties
from coform.service import CoForm


@pytest.fixture
def service(tmp_path):
    s = CoForm(tmp_path)
    yield s
    s.store.close()


def test_optional_requirements_appear_only_when_set():
    names = {r["name"] for r in requirement_checks(Geometry(), ModelParameters(), Protocol())}
    assert names == {"deflection", "small_deflection", "base_width", "wall_height"}
    rows = {r["name"]: r for r in requirement_checks(Geometry(), ModelParameters(), Protocol(max_volume_m3=5e-6, max_root_stress_pa=1e5))}
    assert rows["material"]["value"] == pytest.approx(section_properties(Geometry())["area_m2"] * .16)
    assert rows["material"]["passed"] is False  # 8.64 cm³ > 5 cm³
    assert rows["root_stress"]["value"] == pytest.approx(root_stress(Geometry(), .3, .12)) and not rows["root_stress"]["passed"]


def test_optimizer_respects_material_and_stress_requirements():
    params = ModelParameters(youngs_modulus_pa=.75e9, rotational_compliance_rad_per_nm=.2)
    free = optimize_geometry(Geometry(), params, Protocol(max_deflection_m=.0015))
    stress_limit = root_stress(Geometry(**free["geometry"]), .3, .12) * .8
    stressed = optimize_geometry(Geometry(), params, Protocol(max_deflection_m=.0015, max_root_stress_pa=stress_limit))
    assert stressed["geometry"] != free["geometry"]
    assert root_stress(Geometry(**stressed["geometry"]), .3, .12) <= stress_limit
    impossible = optimize_geometry(Geometry(), params, Protocol(max_deflection_m=.0015, max_volume_m3=1e-6))
    assert impossible["geometry"] is None


def test_agents_repair_a_design_that_breaks_the_material_budget(service):
    budget = 8e-6  # Reference is 8.64 cm³.
    run = service.create_run(mode="simulated", protocol=Protocol(max_volume_m3=budget))
    run_id, reference = run["run"]["id"], run["candidates"][0]["id"]
    service.step(run_id)
    service.step(run_id)
    diagnosis = service.diagnose(run_id, reference)
    assert "over_material_budget" in [i["code"] for i in diagnosis["issues"]]
    assert next(r for r in diagnosis["requirements"] if r["name"] == "material")["passed"] is False
    result = service.repair(run_id, reference)
    assert result["repair"]["status"] == "agreed"
    after = {r["name"]: r for r in result["diagnosis_after"]["requirements"]}
    assert all(r["passed"] for r in after.values()) and after["material"]["value"] <= budget


def test_conflicting_requirements_name_the_binding_one(service):
    run = service.create_run(mode="simulated", protocol=Protocol(max_deflection_m=.0015, max_volume_m3=4e-6))
    run_id, reference = run["run"]["id"], run["candidates"][0]["id"]
    service.step(run_id)
    service.step(run_id)
    diagnosis = service.diagnose(run_id, reference)
    fixes = diagnosis["geometry_fixes"]
    assert fixes["options"] == {} and "relaxing material alone" in fixes["note"]
    assert service.repair(run_id, reference)["repair"]["status"] in ("no_valid_proposals", "no_consensus")


def test_agent_design_proposals_must_meet_requirements():
    from coform.agents import Proposal, validate_proposal
    protocol = Protocol(max_root_stress_pa=1e4)
    context = {"protocol": protocol.model_dump(mode="json"), "candidates": [{"id": "c1", "geometry": Geometry().model_dump()}],
               "evidence_ids": ["o1"], "budget": {"cycles_remaining": 5, "designs_remaining": 2},
               "model": {"fit": {"status": "identifiable"}, "parameters": ModelParameters().model_dump()}}
    proposal = Proposal(action="design", candidate_id="c1", geometry=Geometry(base_width_m=.030), force_n=.3,
                        load_position_m=.12, reason="r", evidence_ids=("o1",), cost={"cycles": 1, "designs": 1})
    assert "Predicted to violate the root stress requirement" in validate_proposal(proposal, context)
