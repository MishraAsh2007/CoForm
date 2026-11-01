"""Function before strength: the part is checked against the desk, bottle and screw it works with."""
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from coform import freeform as ff
from coform import scene
from coform.fea import find_tools

needs_solver = pytest.mark.skipif(find_tools() is None, reason="Gmsh and CalculiX are not installed (bundled with FreeCAD 1.x)")
FIXTURES = Path(__file__).parent / "fixtures"

DESK = {"name": "desk", "role": "mount", "shape": "box", "at": [-500, -200, "-desk_thick"], "size": [500, 400, "desk_thick"],
        "touch": ["+z", "-z"], "insert": "+x", "note": "Desk top at z = 0, edge at x = 0"}
BOTTLE = {"name": "bottle", "role": "payload", "shape": "cylinder", "at": [48, 0, -60], "radius": "bottle_d / 2", "length": 200,
          "touch": ["-z"], "force_n": [0, 0, -15], "insert": "-z", "retain": ["+x", "-x", "+y", "-y"]}
SCREW = {"name": "screw", "role": "hardware", "shape": "cylinder", "at": [-20, 0, "-desk_thick - 40"], "radius": 3, "length": 40,
         "insert": "+z", "retain": ["+x", "-x", "+y", "-y"], "bears_on": "desk", "note": "M6 thumb screw tightened up against the desk"}


def bottle_holder():
    """A screw clamp on the desk edge with a cup beside it: what the live draft should have been."""
    low = "-desk_thick - gap - jaw_t"  # underside of the lower jaw, below the screw's travel gap
    return {
        "name": "Desk bottle holder", "summary": "Screw clamp on the desk edge; the bottle stands in a cup beside the desk.",
        "parameters": {"desk_thick": {"value": 25, "role": "fixed"}, "bottle_d": {"value": 75, "role": "fixed"},
                       "gap": {"value": 4, "role": "fixed", "note": "screw travel"},
                       "jaw_t": {"value": 5, "min": 3, "max": 8, "role": "thickness"},
                       "spine_t": {"value": 6, "min": 4, "max": 10, "role": "thickness"},
                       "floor_t": {"value": 3, "min": 2, "max": 6, "role": "thickness"},
                       "wall_t": {"value": 3, "min": 2, "max": 6, "role": "thickness"}},
        "features": [
            {"label": "upper jaw", "shape": "box", "at": [-40, -20, 0], "size": ["40 + spine_t", 40, "jaw_t"]},
            {"label": "spine", "shape": "box", "at": [0, -20, "-60 - floor_t"], "size": ["spine_t", 40, "60 + floor_t + jaw_t"]},
            {"label": "lower jaw", "shape": "box", "at": [-40, -20, low], "size": ["40 + spine_t", 40, "jaw_t"]},
            {"label": "cup floor", "shape": "cylinder", "at": [48, 0, "-60 - floor_t"], "radius": "bottle_d / 2 + 1 + wall_t", "length": "floor_t"},
            {"label": "cup wall", "shape": "cylinder", "at": [48, 0, -60], "radius": "bottle_d / 2 + 1 + wall_t", "length": 40},
            {"label": "web", "shape": "box", "at": [0, -10, "-60 - floor_t"], "size": ["48 - bottle_d / 2 - 1", 20, "40 + floor_t"]},
            {"label": "cup void", "op": "subtract", "shape": "cylinder", "at": [48, 0, -60], "radius": "bottle_d / 2 + 1", "length": 41},
            {"label": "drain", "op": "subtract", "shape": "cylinder", "at": [48, 0, "-61 - floor_t"], "radius": 4, "length": "floor_t + 2"},
            {"label": "screw hole", "op": "subtract", "shape": "cylinder", "at": [-20, 0, f"{low} - 1"], "radius": 3, "length": "jaw_t + 2"}],
        "environment": [deepcopy(DESK), deepcopy(BOTTLE), deepcopy(SCREW)],
        "requirements": {"max_displacement_mm": 2.0, "safety_factor": 2}}


# ---- geometry of the checks, without the solver ------------------------------------------------------
def _solid(body, values=None):
    world = [b for b in (deepcopy(DESK), deepcopy(BOTTLE)) if b["name"] != body["name"]] + [body]
    spec = ff.DesignSpec.model_validate({**bottle_holder(), "environment": world})
    return next(s for s in scene.resolve(spec, values or ff.values_of(spec)) if s.name == body["name"])


def test_a_box_sweeps_only_along_its_own_footprint():
    box = _solid({"name": "crate", "role": "keep_clear", "shape": "box", "at": [0, 0, 0], "size": [10, 10, 10]})
    p = np.array([[5, 5, 50], [5, 5, -50], [15, 5, 50], [5, 5, 5]])
    assert scene.swept(box, "+z", p).tolist() == [True, False, False, True]
    assert scene.swept(box, "-z", p).tolist() == [False, True, False, True]


def test_a_cylinder_sweeps_sideways_as_a_stadium_and_along_its_axis_as_a_rod():
    can = _solid({"name": "can", "role": "keep_clear", "shape": "cylinder", "at": [0, 0, 0], "radius": 10, "length": 20})
    sideways = np.array([[100, 0, 10], [100, 9, 10], [100, 11, 10], [-12, 0, 10], [100, 0, 25]])
    assert scene.swept(can, "+x", sideways).tolist() == [True, True, False, False, False]
    along = np.array([[0, 0, 100], [9, 0, 100], [11, 0, 100], [0, 0, -5]])
    assert scene.swept(can, "+z", along).tolist() == [True, True, False, False]


def test_contact_faces_are_named_from_the_body_side():
    desk = _solid(deepcopy(DESK))
    assert scene.faces(desk, np.array([[-10, 0, 0.0], [-10, 0, -25.0], [0.0, 0, -10]])).tolist() == ["+z", "-z", "+x"]


def test_the_world_cannot_be_resized_by_a_design_parameter():
    raw = bottle_holder()
    raw["environment"][0]["size"] = [500, 400, "jaw_t"]
    with pytest.raises(ValueError, match="comes from the request"):
        ff.DesignSpec.model_validate(raw)
    with pytest.raises(ValueError, match="outside its allowed range"):  # nor can the optimiser move a fixed dimension
        ff.values_of(ff.DesignSpec.model_validate(bottle_holder()), {"desk_thick": 30})


# ---- with the solver: a working holder passes; each way of breaking it is named -------------------------
@needs_solver
def test_a_working_clamp_and_cup_pass_with_supports_only_where_they_touch(tmp_path):
    spec = ff.DesignSpec.model_validate(bottle_holder())
    result = ff.analyze(spec, ff.values_of(spec), tmp_path)
    assert result["passed"], [(c["name"], c.get("detail")) for c in result["checks"] if not c["passed"]]
    assert [c["name"] for c in result["checks"][:4]] == ["fit", "contact", "insertion", "retention"]
    bodies = {b["name"]: b for b in result["scene"]}
    assert bodies["desk"]["touched_faces"].get("+z") and bodies["bottle"]["touched_faces"].get("-z")
    assert set(bodies["bottle"]["retained_by"]) == {"+x", "-x", "+y", "-y"} and all(bodies["bottle"]["retained_by"].values())
    # Held where the upper jaw rests on the desk and where the screw grips the jaw, nowhere declared by hand.
    supports = result["regions"]["supports"]
    assert {s["name"].split(" ")[0] for s in supports} == {"desk", "screw"}
    desk = next(s for s in supports if s["name"].startswith("desk"))
    assert desk["min"][2] >= -25 - 0.2 and desk["max"][0] <= 0.2  # on the desk's top and edge, never inside it
    assert result["loads"][0]["name"] == "bottle" and result["loads"][0]["force_n"] == [0, 0, -15]


@needs_solver
@pytest.mark.parametrize("breakage, expected", [
    ("no_screw_hole", r"\[fit\] 'lower jaw' reach up to .* into 'screw' \(hardware\)"),
    ("screw_too_short", r"'screw' does not reach 'desk' \(gap 5\.0 mm\)"),
    ("jaw_fills_the_desk", r"\[fit\] 'lower jaw' reach up to .* into 'desk' \(mount\)"),
    ("cup_too_shallow", r"Nothing stops 'bottle' moving \+x"),
    ("lid_over_the_cup", r"'bottle' cannot move into place \(moving -z\): 'lid' block its path"),
])
def test_each_way_the_mechanism_fails_is_named_before_strength_is_tested(tmp_path, breakage, expected):
    raw = bottle_holder()
    if breakage == "no_screw_hole":
        raw["features"] = [f for f in raw["features"] if f["label"] != "screw hole"]
    elif breakage == "screw_too_short":
        raw["environment"][2]["length"] = 35
    elif breakage == "jaw_fills_the_desk":
        raw["features"][2]["size"] = ["40 + spine_t", 40, "jaw_t + 20"]
    elif breakage == "cup_too_shallow":  # only the floor is left: the bottle stands on it with nothing around it
        raw["features"] = [f for f in raw["features"] if f["label"] not in ("cup wall", "cup void")]
    elif breakage == "lid_over_the_cup":
        raw["features"].insert(5, {"label": "lid", "shape": "box", "at": [5, -20, 150], "size": [81, 40, 3]})
        raw["features"].insert(6, {"label": "lid post", "shape": "box", "at": [0, -20, -60], "size": [6, 40, 213]})
    spec = ff.DesignSpec.model_validate(raw)
    with pytest.raises(ff.BuildError, match="so its strength was not tested") as failure:
        ff.analyze(spec, ff.values_of(spec), tmp_path)
    assert __import__("re").search(expected, str(failure.value)), str(failure.value)[:600]


@needs_solver
def test_the_live_bottle_holder_that_passed_every_check_is_now_rejected_for_what_it_got_wrong(tmp_path):
    data = json.loads((FIXTURES / "live_bottle_holder.json").read_text(encoding="utf-8"))
    raw = data["spec"]
    raw["parameters"]["desk_thick"] = {"value": 25, "role": "fixed"}
    # The desk and bottle its own design intent describes: a 25 mm desk gripped above and below, a bottle in the cradle.
    raw["environment"] = [
        {"name": "desk", "role": "mount", "shape": "box", "at": [-600, -300, 0], "size": [600, 675, "desk_thick"], "touch": ["+z", "-z"], "insert": "+x"},
        {"name": "bottle", "role": "payload", "shape": "cylinder", "at": [39, 37.5, 1.5], "radius": 36, "length": 200,
         "touch": ["-z"], "force_n": [0, 0, -15], "insert": "-z", "retain": ["+x", "-x", "+y", "-y"]}]
    spec = ff.DesignSpec.model_validate(raw)
    with pytest.raises(ff.BuildError) as failure:
        ff.analyze(spec, ff.values_of(spec), tmp_path)
    text = str(failure.value)
    assert "[fit] 'lower_jaw' reach up to 12.5 mm into 'desk' (mount)" in text  # the jaw fills the desk gap
    assert "'gusset' reach up to" in text and "into 'bottle'" in text           # the gusset stands where the bottle goes
    assert "Nothing stops 'bottle' moving +x" in text                            # and the bottle can slide off the front


@needs_solver
def test_without_reference_bodies_function_is_unverified_and_the_part_does_not_pass(tmp_path):
    from coform.designer import _check_spec
    data = json.loads((FIXTURES / "live_bottle_holder.json").read_text(encoding="utf-8"))
    raw = deepcopy(data["spec"])
    raw["parameters"] = {k: {**p, "value": max(p["value"], 3) if p.get("role") == "thickness" else p["value"]} for k, p in raw["parameters"].items()}
    spec = ff.DesignSpec.model_validate(raw)
    result = ff.analyze(spec, ff.values_of(spec), tmp_path / "a")
    function = next(c for c in result["checks"] if c["name"] == "function")
    assert function["status"] == "unverified" and not result["passed"]
    _, _, errors = _check_spec(raw, tmp_path / "b")  # and a draft cannot go on to optimisation like this
    assert any("No reference bodies" in e for e in errors)


# ---- replacing a wrong mechanism ------------------------------------------------------------------------
def _live_holder_in_the_world():
    """The live draft, placed in the same world (desk, bottle, screw, fixed sizes) as the working holder."""
    data = json.loads((FIXTURES / "live_bottle_holder.json").read_text(encoding="utf-8"))
    good = bottle_holder()
    raw = deepcopy(data["spec"])
    raw["parameters"].update({n: p for n, p in good["parameters"].items() if p.get("role") == "fixed"})
    raw["environment"] = deepcopy(good["environment"])
    raw["requirements"] = deepcopy(good["requirements"])  # same job: only the mechanism differs
    return raw


@pytest.mark.parametrize("change, message", [("environment", "keeps every payload's name, size"), ("desk", "keeps every mount"),
                                             ("fixed", "keeps every fixed dimension"),
                                             ("requirements", "keeps the requirements"), ("nothing", "makes no change")])
def test_a_redesign_may_change_the_mechanism_but_never_the_job(change, message):
    from coform.designer_repair import redesign_from
    baseline = ff.DesignSpec.model_validate(_live_holder_in_the_world())
    assert redesign_from(baseline, bottle_holder()).features[0].label == "upper jaw"  # a different mechanism is fine
    raw = bottle_holder() if change != "nothing" else baseline.model_dump(mode="json")
    if change == "environment":
        raw["environment"][1]["radius"] = 30  # a thinner bottle would make it easier
    elif change == "desk":
        raw["environment"][0]["size"] = [500, 400, 20]
    elif change == "fixed":
        raw["parameters"]["desk_thick"]["value"] = 20
    elif change == "requirements":
        raw["requirements"]["max_displacement_mm"] = 20
    with pytest.raises(ValueError, match=message):
        redesign_from(baseline, raw)


class Redesigner:
    """Drafts the broken live holder; shown the fit evidence, replaces the mechanism with a working clamp and cup."""
    name, model = "fake", "redesigner"

    def __init__(self):
        self.drafts = []

    def complete_json(self, system, payload, max_output_tokens):
        from coform.providers import ProviderResponse
        if "request" in payload:
            self.drafts.append(payload)
            if "previous_spec" not in payload:
                return ProviderResponse({"spec": _live_holder_in_the_world(), "assumptions": []}, 500, 900)
            return ProviderResponse({"redesign": bottle_holder(), "reason": "The jaw fills the desk gap and the cradle cannot hold "
                                     "the bottle; a screw clamp with a cup beside the desk does both.",
                                     "design_intent": {"function": "Hold a bottle beside a desk edge"}}, 500, 900)
        if "part" in payload:
            return ProviderResponse({"verdict": "approve", "issues": []}, 100, 20)
        if "proposals" in payload:
            return ProviderResponse({"reviews": []}, 100, 20)
        return ProviderResponse({"changes": None, "reason": "fine"}, 100, 20)


@needs_solver
def test_a_wrong_mechanism_is_replaced_instead_of_patched_forever(tmp_path, monkeypatch):
    import time
    from coform.designer import PROGRESS, Designer
    from coform.service import CoForm
    monkeypatch.setenv("COFORM_DESIGN_MAX_ROUNDS", "1")
    service = CoForm(tmp_path)
    agent = Redesigner()
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: agent for r in ("mechanics", "fabrication", "measurement")} if live else None)
    try:
        designer = Designer(service)
        pid = designer.create("A water bottle holder that clamps to a desk.", {}, live=True)["project"]["id"]
        for _ in range(600):
            if not PROGRESS[pid]["running"]:
                break
            time.sleep(0.5)
        state = designer.state(pid)
        first, second = state["attempts"][:2]
        assert first["status"] == "rejected" and "[fit] 'lower_jaw'" in first["errors"][0]
        assert second["repair_source"] == "redesign" and second["status"] == "accepted"
        assert "jaw fills the desk gap" in second["redesign_reason"] and second["design_intent"]["function"].startswith("Hold a bottle")
        assert "[fit] 'lower_jaw'" in json.dumps(agent.drafts[1]["previous_attempt_errors"])  # it was shown why
        assert state["specs"][-1]["spec"]["features"][0]["label"] == "upper jaw" and state["tests"][0]["results"]["passed"]
    finally:
        service.store.close()


def test_a_payload_stands_where_the_design_puts_it_but_a_desk_never_moves():
    # Live drafts placed the bottle relative to the cup: legitimate, since only its size and weight are given.
    raw = bottle_holder()
    raw["parameters"]["bottle_kg"] = {"value": 1.53, "role": "fixed"}
    raw["environment"][1]["at"] = ["48 + 0 * wall_t", 0, -60]
    raw["environment"][1]["force_n"] = [0, 0, "-bottle_kg * 9.81"]
    spec = ff.DesignSpec.model_validate(raw)
    bottle = next(s for s in scene.resolve(spec, ff.values_of(spec)) if s.name == "bottle")
    assert bottle.force == pytest.approx((0, 0, -15.01), abs=0.01)
    raw["environment"][0]["at"] = [-500, -200, "-desk_thick - jaw_t"]
    with pytest.raises(ValueError, match="a mount or keep-clear space does not move"):
        ff.DesignSpec.model_validate(raw)
    raw = bottle_holder()
    raw["environment"][2]["length"] = "40 + jaw_t"  # a screw that grows with the jaw is not a stock part
    with pytest.raises(ValueError, match="pick a stock size and declare it fixed"):
        ff.DesignSpec.model_validate(raw)


def test_rejected_drafts_say_where_and_what_without_echoing_the_whole_spec():
    from coform.designer import _check_spec
    raw = bottle_holder()
    raw["environment"][2]["length"] = "40 + jaw_t"
    _, _, errors = _check_spec(raw, Path("unused"))
    assert errors[0].startswith("spec: Reference body 'screw' length") and "input_value" not in errors[0]


def test_a_redesign_may_stand_the_bottle_elsewhere_and_bring_its_own_screws():
    from coform.designer_repair import redesign_from
    baseline = ff.DesignSpec.model_validate(_live_holder_in_the_world())
    raw = bottle_holder()
    raw["environment"][1]["at"] = [60, 0, -60]           # the new cup puts the bottle elsewhere
    raw["environment"][2]["radius"] = 4                  # and the new clamp uses an M8 screw
    raw["features"][-1]["radius"] = 4
    assert redesign_from(baseline, raw).environment[1].at == (60, 0, -60)


def test_a_screw_driven_into_or_through_a_face_is_credited_to_that_face():
    # A screw pushed 5 mm into the desk crashed the analysis (no face found); a wall screw passes 20 mm in.
    raw = bottle_holder()
    spec = ff.DesignSpec.model_validate(raw)
    values = ff.values_of(spec)
    solids = {s.name: s for s in scene.resolve(spec, values)}
    assert scene._gap(solids["screw"], solids["desk"]) == (0.0, "-z")
    raw["environment"][2]["at"] = [-20, 0, "-desk_thick - 35"]  # 5 mm into the desk
    spec = ff.DesignSpec.model_validate(raw)
    solids = {s.name: s for s in scene.resolve(spec, ff.values_of(spec))}
    assert scene._gap(solids["screw"], solids["desk"]) == (0.0, "-z")
