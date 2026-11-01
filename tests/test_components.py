"""Tested mechanical building blocks: each is built from the body it serves, so it fits by construction."""
from copy import deepcopy

import pytest

from coform import freeform as ff
from coform.fea import find_tools

needs_solver = pytest.mark.skipif(find_tools() is None, reason="Gmsh and CalculiX are not installed (bundled with FreeCAD 1.x)")
FUNCTION = ("fit", "contact", "insertion", "retention")
DESK = {"name": "desk", "role": "mount", "shape": "box", "at": [-500, -200, "-desk_thick"], "size": [500, 400, "desk_thick"],
        "touch": ["+z", "-z"], "insert": "+x"}
WALL = {"name": "wall", "role": "mount", "shape": "box", "at": [-100, -200, -100], "size": [100, 400, 300], "touch": ["+x"], "insert": "+x"}


def bottle_holder(desk_thick=25):
    return {"name": "Desk bottle holder", "parameters": {"desk_thick": {"value": desk_thick, "role": "fixed"}, "bottle_d": {"value": 75, "role": "fixed"}},
            "environment": [deepcopy(DESK), {"name": "bottle", "role": "payload", "shape": "cylinder", "at": [55, 0, -60], "radius": "bottle_d / 2",
                                             "length": 200, "touch": ["-z"], "force_n": [0, 0, -15], "insert": "-z", "retain": ["+x", "-x", "+y", "-y"]}],
            "components": [{"type": "desk_clamp", "name": "clamp", "grips": "desk", "edge": "+x", "along": 0, "drop": 30},
                           {"type": "cup", "name": "cup", "holds": "bottle"}],
            # The one hand-made piece: a web from the clamp's spine to the cup (the cup's void is cut after it).
            "features": [{"label": "web", "shape": "box", "at": [1, -10, -66], "size": [17, 20, 46]}],
            "requirements": {"max_displacement_mm": 2.0, "safety_factor": 2}}


def wall_backpack_hook():
    return {"name": "Wall backpack hook", "parameters": {"strap_w": {"value": 25, "role": "fixed"}},
            "environment": [deepcopy(WALL), {"name": "strap", "role": "payload", "shape": "box", "at": [15, -12.5, 70], "size": [30, "strap_w", 10],
                                             "touch": ["-z"], "force_n": [0, 0, -98.1], "insert": "-z", "retain": ["+x"]}],
            "components": [{"type": "wall_plate", "name": "plate", "mounts": "wall", "face": "+x", "center": [0, 60], "width": 40, "height": 80},
                           {"type": "hook", "name": "hook", "holds": "strap", "direction": "+x", "start": 1}],
            "features": [], "requirements": {"max_displacement_mm": 2.0, "safety_factor": 2}}


def desk_backpack_hook():
    return {"name": "Desk backpack hook", "parameters": {"desk_thick": {"value": 25, "role": "fixed"}},
            "environment": [deepcopy(DESK), {"name": "strap", "role": "payload", "shape": "box", "at": [15, -12.5, -80], "size": [30, 25, 10],
                                             "touch": ["-z"], "force_n": [0, 0, -98.1], "insert": "-z", "retain": ["+x"]}],
            "components": [{"type": "desk_clamp", "name": "clamp", "grips": "desk", "edge": "+x", "along": 0, "drop": 56},
                           {"type": "hook", "name": "hook", "holds": "strap", "direction": "+x", "start": 1}],
            "features": [], "requirements": {"max_displacement_mm": 5.0, "safety_factor": 2}}


def wall_tube_cradle():
    return {"name": "Wall tube cradle", "parameters": {"tube_r": {"value": 15, "role": "fixed"}},
            "environment": [deepcopy(WALL), {"name": "tube", "role": "payload", "shape": "cylinder", "at": [21, -40, 50], "axis": "y",
                                             "radius": "tube_r", "length": 80, "touch": ["-z"], "force_n": [0, 0, -10], "insert": "-z", "retain": ["+x", "-x"]}],
            "components": [{"type": "wall_plate", "name": "plate", "mounts": "wall", "face": "+x", "center": [0, 50], "width": 40, "height": 80},
                           {"type": "cradle", "name": "saddle", "holds": "tube"}],
            "features": [], "requirements": {"max_displacement_mm": 2.0, "safety_factor": 2}}


def test_components_expand_into_ordinary_features_parameters_and_hardware():
    spec = ff.DesignSpec.model_validate(bottle_holder())
    labels = [f.label for f in spec.features]
    assert labels[:5] == ["clamp upper jaw", "clamp spine", "clamp lower jaw", "cup floor", "cup wall"] and labels[5] == "web"
    assert labels[6:] == ["clamp screw hole", "clamp nut seat", "cup void", "cup drain"]  # functional voids are cut last
    assert {"clamp_jaw_t", "clamp_spine_t", "cup_wall_t", "cup_floor_t"} <= set(spec.parameters)
    hardware = {b.name: b for b in spec.environment if b.role == "hardware"}
    assert hardware["clamp_screw"].bears_on == "desk" and hardware["clamp_screw"].insert == "+z"
    assert hardware["clamp_nut"].insert == "+x" and "components" not in spec.model_dump()


@pytest.mark.parametrize("use, message", [({"type": "teleporter", "name": "x"}, "Unknown component 'teleporter'"),
                                          ({"type": "cup", "name": "cup", "holds": "mug"}, "needs a payload body named 'mug'"),
                                          ({"type": "cup", "name": "cup"}, "is missing a setting"),
                                          ({"type": "desk_clamp", "name": "clamp", "grips": "desk", "edge": "+z"}, "edge must be")])
def test_a_misused_component_says_what_is_wrong(use, message):
    raw = bottle_holder()
    raw["components"] = [use]
    with pytest.raises(ValueError, match=message):
        ff.DesignSpec.model_validate(raw)


@needs_solver
@pytest.mark.parametrize("make", [bottle_holder, wall_backpack_hook, desk_backpack_hook, wall_tube_cradle],
                         ids=["desk bottle holder", "wall backpack hook", "desk backpack hook", "wall tube cradle"])
def test_parts_built_from_components_work_before_any_optimisation(tmp_path, make):
    spec = ff.DesignSpec.model_validate(make())
    result = ff.analyze(spec, ff.values_of(spec), tmp_path)
    assert result["passed"], [(c["name"], c.get("detail")) for c in result["checks"] if not c["passed"]]
    assert all(c["passed"] for c in result["checks"] if c["name"] in FUNCTION)


@needs_solver
def test_the_screw_clamp_grips_through_its_screw_and_its_nut_can_be_fitted(tmp_path):
    spec = ff.DesignSpec.model_validate(bottle_holder())
    bodies = {b["name"]: b for b in ff.analyze(spec, ff.values_of(spec), tmp_path)["scene"]}
    assert bodies["desk"]["touched_faces"].get("+z")  # upper jaw on the desk top; the screw supplies the underside
    assert all(bodies["clamp_screw"]["retained_by"].values()) and bodies["clamp_screw"]["insertion_clear"]
    assert all(bodies["clamp_nut"]["retained_by"].values()) and bodies["clamp_nut"]["insertion_clear"]


@needs_solver
@pytest.mark.parametrize("desk_thick", [18, 40])
def test_the_clamp_refits_itself_to_a_different_desk(tmp_path, desk_thick):
    # Reusable knowledge: the same component call fits any desk, because it is built from the desk.
    spec = ff.DesignSpec.model_validate(bottle_holder(desk_thick))
    result = ff.analyze(spec, ff.values_of(spec), tmp_path)
    assert all(c["passed"] for c in result["checks"] if c["name"] in FUNCTION), [c.get("detail") for c in result["checks"] if not c["passed"]]


# ---- the worked example drafting agents copy, joins, and repairs that move a payload --------------------
def _worked_example(**fixed):
    import json
    from pathlib import Path
    raw = json.loads((Path(__file__).parents[1] / "coform" / "templates" / "desk_bottle_holder.json").read_text(encoding="utf-8"))
    for name, value in fixed.items():
        raw["parameters"][name]["value"] = value
    return raw


@needs_solver
@pytest.mark.parametrize("fixed", [{}, {"bottle_d": 90}, {"bottle_d": 65, "desk_thick": 18}], ids=["as written", "1 L bottle", "slim bottle, thin shelf"])
def test_the_worked_example_passes_and_still_fits_when_the_request_changes(tmp_path, fixed):
    spec = ff.DesignSpec.model_validate(_worked_example(**fixed))
    assert "web" in [f.label for f in spec.features]  # joined by code, not by hand
    result = ff.analyze(spec, ff.values_of(spec), tmp_path)
    assert result["passed"], [(c["name"], c.get("detail")) for c in result["checks"] if not c["passed"]]


def test_a_join_names_the_pieces_it_could_have_meant():
    raw = _worked_example()
    raw["components"][2]["between"] = ["clamp spine", "cup rim"]
    with pytest.raises(ValueError, match=r"cannot find an added piece labelled 'cup rim'; pieces so far: .*'cup wall'"):
        ff.DesignSpec.model_validate(raw)


def test_a_repair_may_move_the_bottle_but_never_the_desk():
    from coform.designer_repair import apply_repair
    spec = ff.DesignSpec.model_validate(_worked_example())
    moved = apply_repair(spec, {"environment": [{"index": 1, "changes": {"at": ["25 + bottle_d / 2", 0, -60]}}]})
    assert moved.environment[1].at[0] == "25 + bottle_d / 2"
    with pytest.raises(ValueError, match="the desk, wall or keep-clear space does not move"):
        apply_repair(spec, {"environment": [{"index": 0, "changes": {"at": [-500, -200, -30]}}]})
    with pytest.raises(ValueError, match="Unsupported environment repair fields"):
        apply_repair(spec, {"environment": [{"index": 1, "changes": {"radius": 30}}]})


def test_component_options_may_be_expressions_and_an_empty_list_is_fine():
    # Live drafts wrote "width": "clamp_width" and sent "components": [] (both were rejected).
    raw = _worked_example()
    raw["parameters"]["clamp_width"] = {"value": 50, "min": 30, "max": 70, "role": "size"}
    raw["components"][0].update(width="clamp_width", reach="desk_thick + 15")
    spec = ff.DesignSpec.model_validate(raw)
    jaw = next(f for f in spec.features if f.label == "clamp upper jaw")
    assert jaw.at[1] == "-25" and ff.evaluate(jaw.at[0], ff.values_of(spec)) == -40
    plain = _worked_example()
    plain.update(components=[], features=[{"label": "block", "shape": "box", "at": [0, -10, 0], "size": [10, 20, 10]}])
    assert "components" not in ff.DesignSpec.model_validate(plain).model_dump()
