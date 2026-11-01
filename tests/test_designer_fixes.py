"""Fixes for the ways live drafts actually failed: rotated gussets through the wall, shifted pieces,
knife edges from invented drain slots, and an agent stuck resending the same design."""
import json
import time
from pathlib import Path

import pytest

from scienceclaw import freeform as ff
from scienceclaw.designer import PROGRESS, Designer, _example
from scienceclaw.fea import find_tools
from scienceclaw.providers import ProviderResponse

needs_solver = pytest.mark.skipif(find_tools() is None, reason="Gmsh and CalculiX are not installed (bundled with FreeCAD 1.x)")
FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    spec = ff.DesignSpec.model_validate(data["spec"])
    return spec, data.get("values") or ff.values_of(spec)


def test_wedge_is_a_triangle_whose_bounds_never_pass_its_corner():
    spec = ff.DesignSpec.model_validate(_example())
    gusset = next(f for f in spec.features if f.label == "gusset")
    assert gusset.shape == "wedge" and gusset.rotate is None  # the example the drafter copies
    lo, hi = ff.feature_bounds(gusset, ff.values_of(spec))
    assert lo[0] == pytest.approx(3.5) and hi[2] == pytest.approx(76.5)  # starts at the plate, below the arm
    with pytest.raises(ValueError):
        ff.Feature(shape="wedge", at=[0, 0, 0], legs=[{"axis": "x", "length": 5}, {"axis": "x", "length": 5}], thickness=2)


@needs_solver
def test_template_with_a_wedge_gusset_builds_and_passes_every_geometry_check(tmp_path):
    spec = ff.DesignSpec.model_validate(_example())
    result = ff.analyze(spec, ff.values_of(spec), tmp_path)
    geometry = {c["name"]: c["passed"] for c in result["checks"] if c["name"] in ("aligned", "mount_clear", "sealed_cavity", "watertight")}
    assert all(geometry.values()) and len(geometry) == 4


@needs_solver
def test_code_fixes_make_the_real_backpack_hook_pass_every_check(tmp_path):
    spec, values = fixture("live_backpack_hook")
    fixed, notes = ff.geometry_fixes(spec, values)
    assert any("Moved 'gusset' 28.3 mm along x" in n for n in notes)
    assert any("Lined 'tip_curl_quarter' up with 'arm_main'" in n for n in notes)
    repaired = ff.DesignSpec.model_validate(fixed)
    result = ff.analyze(repaired, ff.values_of(repaired, values), tmp_path)
    assert result["passed"], [c["label"] for c in result["checks"] if not c["passed"]]


def test_code_fixes_change_nothing_on_a_correct_part():
    spec = ff.DesignSpec.model_validate(_example())
    fixed, notes = ff.geometry_fixes(spec, ff.values_of(spec))
    assert notes == [] and fixed == spec.model_dump(mode="json")


@needs_solver
def test_knife_edge_from_an_invented_drain_slot_is_located_and_named(tmp_path):
    spec, values = fixture("live_backpack_holder_draft")
    result = ff.analyze(spec, values, tmp_path)
    detail = next(c for c in result["checks"] if c["name"] == "watertight")["detail"]
    assert "x 4.0-4.0, y 0.0-10.0, z 4.0-4.0" in detail and "'drain_slot' (subtract)" in detail


class StuckDrafter:
    """Drafts the real backpack hook, then keeps resending it unchanged, as Nemotron did."""
    name, model = "fake", "stuck"

    def __init__(self):
        self.drafts = 0

    def complete_json(self, system, payload, max_output_tokens):
        if "request" in payload:
            self.drafts += 1
            spec, _ = fixture("live_backpack_hook")
            return ProviderResponse({"spec": spec.model_dump(mode="json"), "assumptions": []}, 500, 900)
        if "part" in payload:
            return ProviderResponse({"verdict": "approve", "issues": []}, 100, 20)
        if "proposals" in payload:
            return ProviderResponse({"reviews": []}, 100, 20)
        return ProviderResponse({"changes": None, "reason": "fine"}, 100, 20)


@needs_solver
def test_a_stuck_agent_gets_the_code_placement_fix_instead_of_failing(tmp_path, monkeypatch):
    from scienceclaw.service import ScienceClaw
    service = ScienceClaw(tmp_path)
    agent = StuckDrafter()
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: agent for r in ("mechanics", "fabrication", "measurement")} if live else None)
    try:
        designer = Designer(service)
        pid = designer.create("A wall mounted backpack hook.", {}, live=True)["project"]["id"]
        for _ in range(600):
            if not PROGRESS[pid]["running"]:
                break
            time.sleep(0.5)
        state = designer.state(pid)
        assert state["tests"], state["progress"]["log"][-3:]
        draft = state["tests"][0]["results"]
        assert all(c["passed"] for c in draft["checks"] if c["name"] in ("mount_clear", "aligned"))
        assert agent.drafts <= 3  # fixed within a couple of attempts, not 12
    finally:
        service.store.close()


def test_a_must_fix_citing_only_bend_or_stress_is_left_to_the_optimisation_rounds():
    from scienceclaw.designer import _triage_reviews
    reviews = _triage_reviews([
        {"agent": "mechanics", "verdict": "revise", "issues": [{"severity": "must_fix", "problem": "Arm bends too much",
                                                                 "evidence_ids": ["check:deflection", "check:stress"]}]},
        {"agent": "fabrication", "verdict": "revise", "issues": [{"severity": "must_fix", "problem": "Hole blocks access",
                                                                   "evidence_ids": ["features:3"]}]}])
    assert reviews[0]["issues"][0]["severity"] == "should_fix" and reviews[0]["verdict"] == "approve"
    assert reviews[1]["issues"][0]["severity"] == "must_fix" and reviews[1]["verdict"] == "revise"  # geometry stays a must-fix
