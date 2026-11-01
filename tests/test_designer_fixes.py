"""Fixes for the ways live drafts actually failed: rotated gussets through the wall, shifted pieces,
knife edges from invented drain slots, and an agent stuck resending the same design."""
import json
import time
from pathlib import Path

import pytest

from coform import freeform as ff
from coform.designer import PROGRESS, Designer, _example
from coform.fea import find_tools
from coform.providers import ProviderResponse

needs_solver = pytest.mark.skipif(find_tools() is None, reason="Gmsh and CalculiX are not installed (bundled with FreeCAD 1.x)")
FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    spec = ff.DesignSpec.model_validate(data["spec"])
    return spec, data.get("values") or ff.values_of(spec)


def hook_in_its_world():
    """The live backpack hook with the wall it screws to and the backpack strap it must hold."""
    data = json.loads((FIXTURES / "live_backpack_hook.json").read_text(encoding="utf-8"))
    raw = {**data["spec"], "parameters": {k: {**p, "value": data["values"][k]} for k, p in data["spec"]["parameters"].items()}}
    raw["environment"] = [
        {"name": "wall", "role": "mount", "shape": "box", "at": [-100, -200, -100], "size": [100, 400, 300], "touch": ["+x"], "insert": "+x"},
        {"name": "strap", "role": "payload", "shape": "box", "at": [30, -15, 60], "size": [30, 30, 10], "touch": ["-z"],
         "force_n": [0, 0, -98.1], "insert": "-z", "retain": ["+x"]}]
    return ff.DesignSpec.model_validate(raw)


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
    names = ("aligned", "sealed_cavity", "watertight", "fit", "contact", "insertion", "retention")
    geometry = {c["name"]: c["passed"] for c in result["checks"] if c["name"] in names}
    assert all(geometry.values()) and len(geometry) == len(names) and result["passed"]


@needs_solver
def test_code_fixes_make_the_real_backpack_hook_pass_every_check(tmp_path):
    spec, values = fixture("live_backpack_hook")
    fixed, notes = ff.geometry_fixes(spec, values)
    assert any("Moved 'gusset' 28.3 mm along x" in n for n in notes)
    assert any("Lined 'tip_curl_quarter' up with 'arm_main'" in n for n in notes)
    repaired = ff.DesignSpec.model_validate(fixed)
    result = ff.analyze(repaired, ff.values_of(repaired, values), tmp_path)
    failed = [c for c in result["checks"] if not c["passed"]]
    # Every geometric and strength check passes, but without a wall or backpack modelled nothing shows the
    # hook works, so it stays unverified instead of passing.
    assert [(c["name"], c["status"]) for c in failed] == [("function", "unverified")] and not result["passed"]


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
            return ProviderResponse({"spec": hook_in_its_world().model_dump(mode="json"), "assumptions": []}, 500, 900)
        if "part" in payload:
            return ProviderResponse({"verdict": "approve", "issues": []}, 100, 20)
        if "proposals" in payload:
            return ProviderResponse({"reviews": []}, 100, 20)
        return ProviderResponse({"changes": None, "reason": "fine"}, 100, 20)


@needs_solver
def test_a_stuck_agent_gets_the_code_placement_fix_instead_of_failing(tmp_path, monkeypatch):
    from coform.service import CoForm
    monkeypatch.setenv("COFORM_DESIGN_MAX_DRAFT_ATTEMPTS", "4")
    service = CoForm(tmp_path)
    agent = StuckDrafter()
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: agent for r in ("mechanics", "fabrication", "measurement")} if live else None)
    try:
        designer = Designer(service)
        pid = designer.create("A wall mounted backpack hook.", {}, live=True)["project"]["id"]
        for _ in range(600):
            if not PROGRESS[pid]["running"]:
                break
            time.sleep(0.5)
        attempts = designer.state(pid)["attempts"]
        fix = next(a for a in attempts if a.get("repair_source") == "code_placement_fix")
        assert fix["attempt"] <= 3  # fixed in code within a couple of attempts, not after 12 AI retries
        notes = " ".join(fix["placement_fixes"])
        assert "'gusset' 28.3 mm along x" in notes and "into 'wall'" in notes and "'tip_curl_quarter'" in notes
        # With the strap modelled, the fixed hook is still held back: its gusset rises through the hanging space.
        assert "[insertion] 'strap' cannot move into place" in fix["errors"][0] and "'gusset'" in fix["errors"][0]
    finally:
        service.store.close()


def test_a_must_fix_citing_only_bend_or_stress_is_left_to_the_optimisation_rounds():
    from coform.designer import _triage_reviews
    reviews = _triage_reviews([
        {"agent": "mechanics", "verdict": "revise", "issues": [{"severity": "must_fix", "problem": "Arm bends too much",
                                                                 "evidence_ids": ["check:deflection", "check:stress"]}]},
        {"agent": "fabrication", "verdict": "revise", "issues": [{"severity": "must_fix", "problem": "Hole blocks access",
                                                                   "evidence_ids": ["features:3"]}]}])
    assert reviews[0]["issues"][0]["severity"] == "should_fix" and reviews[0]["verdict"] == "approve"
    assert reviews[1]["issues"][0]["severity"] == "must_fix" and reviews[1]["verdict"] == "revise"  # geometry stays a must-fix


# ---- desk clamps, separate pieces and sub-assembly moves (a live water-bottle holder that clamps a desk)
def _clamp(web_x="-4", **extra):
    """Jaws above z = 0 (desk top) and below z = -25 (desk bottom), joined by a web beyond the desk edge."""
    return {"name": "clamp", "parameters": {"t": {"value": 4, "min": 2, "max": 8, "role": "thickness"}},
            "features": [{"label": "upper_jaw", "shape": "box", "at": [0, 0, 0], "size": [40, 30, "t"]},
                         {"label": "lower_jaw", "shape": "box", "at": [0, 0, "-25 - t"], "size": [40, 30, "t"]},
                         {"label": "web", "shape": "box", "at": [web_x, 0, "-25 - t"], "size": [4, 30, "25 + 2 * t"]},
                         *extra.get("features", [])],
            "supports": [{"name": "desk_top", "min": [0, 0, -0.01], "max": [40, 30, 0.01]},
                         {"name": "desk_bottom", "min": [0, 0, -25.01], "max": [40, 30, -24.99]}],
            "loads": [{"name": "weight", "min": [30, 0, "t - 0.01"], "max": [40, 30, "t + 0.01"], "force_n": [0, 0, -15]}],
            "requirements": {"max_displacement_mm": 1}}


def _mount(raw):
    spec = ff.DesignSpec.model_validate(raw)
    values = ff.values_of(spec)
    added = [ff.feature_bounds(f, values) for f in spec.features if f.op == "add"]
    lo, hi = [min(a[0][k] for a in added) for k in range(3)], [max(a[1][k] for a in added) for k in range(3)]
    supports = [(r.name, ff._vec(r.min, values), ff._vec(r.max, values)) for r in spec.supports]
    return ff.mount_problems(spec, values, lo, hi, supports), ff.geometry_fixes(spec, values)[1]


def test_a_desk_clamp_wrapping_the_edge_is_not_flagged_or_moved_by_code():
    # The old wall rule saw the lower jaw "inside the desk" and code lifted it 29 mm onto the upper jaw.
    problems, fixes = _mount(_clamp())
    assert problems == [] and fixes == []


def test_a_clamp_web_inside_the_contact_patch_is_named_because_the_desk_cannot_fit():
    problems, fixes = _mount(_clamp(web_x="0"))
    assert len(problems) == 1 and "'web'" in problems[0] and "'lower_jaw'" not in problems[0]
    assert "between z = -25.0 and 0.0 mm" in problems[0] and fixes == []  # left to the drafter, with the numbers


def test_separate_pieces_are_named_with_the_gap_to_close():
    cup = {"label": "cup_base", "shape": "box", "at": [0, 0, "t + 5"], "size": [40, 30, 5]}
    detail = ff.separation_detail(ff.DesignSpec.model_validate(_clamp(features=[cup])), {"t": 4.0})
    assert "Piece 2: 'cup_base'" in detail
    assert "'upper_jaw' (z 0.0 to 4.0 mm) and 'cup_base' (z 9.0 to 14.0 mm) are 5.0 mm apart along z" in detail


def test_moving_a_sub_assembly_by_one_offset_counts_as_one_repair_change():
    from coform.designer_repair import apply_repair
    parts = [{"label": f"cup_{k}", "shape": "box", "at": [0, 0, 9], "size": [5, 5, 5]} for k in range(7)]
    spec = ff.DesignSpec.model_validate(_clamp(features=parts))
    down = {"features": [{"index": 3 + k, "changes": {"at": [0, 0, 4]}} for k in range(7)],
            "loads": [{"index": 0, "changes": {"min": [30, 0, "t - 5.01"], "max": [40, 30, "t - 4.99"]}}]}
    moved = apply_repair(spec, down)  # 8 items, one rigid 5 mm move
    assert all(ff.feature_bounds(f, {"t": 4.0})[0][2] == 4 for f in moved.features[3:])
    scattered = {"features": [{"index": 3 + k, "changes": {"at": [0, 0, 4 - k]}} for k in range(7)]}
    with pytest.raises(ValueError, match=r"at most six.*changed 7: feature 3 'cup_0'"):
        apply_repair(spec, scattered)


def test_running_out_of_drafting_tokens_says_so_plainly(tmp_path, monkeypatch):
    from coform import designer as module
    from coform.service import CoForm

    class Never:
        name, model = "fake", "never"

        def complete_json(self, system, payload, max_output_tokens):
            raise AssertionError("no call fits a 3,000-token budget")
    monkeypatch.setattr(module, "DRAFT_TOKEN_BUDGET", 3000)
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: Never() for r in ("mechanics", "fabrication", "measurement")})
    service = CoForm(tmp_path)
    try:
        designer = Designer(service)
        pid = designer.create("A water bottle holder that clamps to a desk.", {}, live=True)["project"]["id"]
        for _ in range(100):
            if not PROGRESS[pid]["running"]:
                break
            time.sleep(0.1)
        assert "Drafting used its 3,000-token budget after 0 attempts this run" in PROGRESS[pid]["log"][-1]["message"]
    finally:
        service.store.close()


class ApartDrafter:
    """Drafts the worked example with its cup 15 mm too far out and no join; the provider is overloaded once."""
    name, model = "fake", "apart"

    def __init__(self):
        self.calls = 0

    def complete_json(self, system, payload, max_output_tokens):
        from coform.providers import ProviderError
        if "request" in payload:
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("nvidia returned HTTP 503; check credentials, model and account limits")
            import json
            raw = json.loads((Path(__file__).parents[1] / "coform" / "templates" / "desk_bottle_holder.json").read_text(encoding="utf-8"))
            raw["components"] = raw["components"][:2]                       # forgot the join
            raw["environment"][1]["at"] = ["35 + bottle_d / 2", 0, -60]     # and stood the bottle 15 mm out
            return ProviderResponse({"spec": raw, "assumptions": []}, 500, 900)
        if "part" in payload:
            return ProviderResponse({"verdict": "approve", "issues": []}, 100, 20)
        if "proposals" in payload:
            return ProviderResponse({"reviews": []}, 100, 20)
        return ProviderResponse({"changes": None, "reason": "fine"}, 100, 20)


@needs_solver
def test_separate_pieces_are_joined_in_code_on_the_first_try_and_an_overload_waits(tmp_path, monkeypatch):
    from coform import designer as module
    from coform.service import CoForm
    monkeypatch.setattr(module, "OVERLOAD_WAIT_S", 0)
    monkeypatch.setenv("COFORM_DESIGN_MAX_ROUNDS", "1")
    service = CoForm(tmp_path)
    agent = ApartDrafter()
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: agent for r in ("mechanics", "fabrication", "measurement")} if live else None)
    try:
        designer = Designer(service)
        pid = designer.create("A water bottle holder that clamps to a desk.", {}, live=True)["project"]["id"]
        for _ in range(600):
            if not PROGRESS[pid]["running"]:
                break
            time.sleep(0.5)
        state = designer.state(pid)
        log = " ".join(e["message"] for e in state["progress"]["log"])
        assert "provider is overloaded; waiting" in log
        bridged = next(a for a in state["attempts"] if a.get("repair_source") == "code_bridge")
        assert bridged["attempt"] == 2 and bridged["status"] == "accepted" and agent.calls == 2  # no AI call spent on the gap
        assert any("Joined 'cup" in n for n in bridged["placement_fixes"]) and state["tests"][0]["results"]["passed"]
    finally:
        service.store.close()
