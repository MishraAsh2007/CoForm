"""The design is a JSON feature spec, not code. Agents edit it with JSON patches.

Frame: the board frame of the component (components/rpi5.yaml). z = 0 is the PCB bottom.
All numbers in mm. See default_spec() for every field, and validate() for the rules.
"""
import copy
import json

import jsonpatch
import jsonschema

from cad.components import SIDES, envelope, load_bundle

FASTENER_STYLES = ("pilot", "through_nut")
FACES = ("lid", "-x", "+x", "-y", "+y")

_num = {"type": "number"}
_pos = {"type": "number", "exclusiveMinimum": 0}
_xy = {"type": "array", "items": _num, "minItems": 2, "maxItems": 2}

SCHEMA = {
    "type": "object",
    "required": ["bundle", "clearances", "fasteners", "walls", "box", "standoffs", "ports", "lid",
                 "lid_screws", "vents", "cable_slots", "mount_tabs", "ribs"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "bundle": {"type": "string"},
        "clearance_source": {"enum": ["generic", "measured"]},
        "clearances": {"type": "object", "required": ["board_gap", "port", "lid_fit", "screw_hole", "pilot_hole",
                                                      "nut_pocket_af", "nut_pocket_depth"],
                       "additionalProperties": False,
                       "properties": {k: _pos for k in ["board_gap", "port", "lid_fit", "screw_hole", "pilot_hole",
                                                        "nut_pocket_af", "nut_pocket_depth"]}},
        "fasteners": {"type": "object", "required": ["board", "lid"], "additionalProperties": False,
                      "properties": {"board": {"enum": list(FASTENER_STYLES)},
                                     "lid": {"enum": list(FASTENER_STYLES)},
                                     "screw": {"type": "string"}}},
        "walls": {"type": "object", "required": ["wall", "floor", "lid", "outer_fillet"], "additionalProperties": False,
                  "properties": {"wall": _pos, "floor": _pos, "lid": _pos, "outer_fillet": {"type": "number", "minimum": 0}}},
        "box": {"type": "object", "required": ["gap", "inner_top"], "additionalProperties": False,
                "properties": {"gap": {"type": "object", "required": list(SIDES), "additionalProperties": False,
                                       "properties": {s: {"type": "number", "minimum": 0} for s in SIDES}},
                               "inner_top": _num}},
        "standoffs": {"type": "object", "required": ["height", "diameter"], "additionalProperties": False,
                      "properties": {"height": _pos, "diameter": _pos}},
        "ports": {"type": "array", "items": {
            "type": "object", "required": ["name", "enabled"], "additionalProperties": False,
            "properties": {"name": {"type": "string"}, "enabled": {"type": "boolean"},
                           "extra": {"type": "number"}, "open_top": {"type": "boolean"}}}},
        "lid": {"type": "object", "required": ["lip_height", "lip_width", "notch_fillers"], "additionalProperties": False,
                "properties": {"lip_height": {"type": "number", "minimum": 0}, "lip_width": _pos,
                               "notch_fillers": {"type": "boolean"}}},
        "lid_screws": {"type": "object", "required": ["boss_diameter", "positions"], "additionalProperties": False,
                       "properties": {"boss_diameter": _pos, "positions": {"type": "array", "items": _xy}}},
        "vents": {"type": "array", "items": {
            "type": "object", "required": ["face", "centre", "size", "slot_width", "pitch"], "additionalProperties": False,
            "properties": {"face": {"enum": list(FACES)}, "centre": _xy, "size": _xy, "slot_width": _pos,
                           "pitch": _pos, "along": {"enum": ["u", "v"]}}}},
        "cable_slots": {"type": "array", "items": {
            "type": "object", "required": ["name", "enabled"], "additionalProperties": False,
            "properties": {"name": {"type": "string"}, "enabled": {"type": "boolean"}, "extra": {"type": "number"},
                           "open_top": {"type": "boolean"}}}},
        "mount_tabs": {"type": "array", "items": {
            "type": "object", "required": ["side", "offset", "width", "length", "thickness", "hole"],
            "additionalProperties": False,
            "properties": {"side": {"enum": list(SIDES)}, "offset": _num, "width": _pos, "length": _pos,
                           "thickness": _pos, "hole": _pos}}},
        "ribs": {"type": "array", "items": {
            "type": "object", "required": ["axis", "position", "height", "width"], "additionalProperties": False,
            "properties": {"axis": {"enum": ["x", "y"]}, "position": _num, "height": _pos, "width": _pos}}},
    },
}


def clearances_from_rules(rules):
    v = rules["values"]
    return {
        "board_gap": v["part_gap"],
        "port": v["port_cutout"],
        "lid_fit": round(v["lid_groove_extra"] / 2, 3),
        "screw_hole": v["m25_clearance_hole"],
        "pilot_hole": v["pilot_hole"],
        "nut_pocket_af": v["nut_pocket_af"],
        "nut_pocket_depth": 2.2,          # nut 2.0 thick (ISO 4032) + 0.2 so it sits below the surface
    }


def default_spec(bundle, rules, cfg):
    """v0 design: plain, symmetric, made only from component data and clearance rules."""
    comp = load_bundle(bundle)
    c = clearances_from_rules(rules)
    style = rules["values"]["fastener_style"]
    env = envelope(comp)
    gap = c["board_gap"]
    wall = 2.0
    b = comp["board"]
    g = {"-x": gap, "+x": gap, "-y": gap, "+y": max(gap, 1.5)}
    # corner lid-screw bosses on the wall centre lines
    cx0, cx1 = -g["-x"] - wall / 2, b["length"] + g["+x"] + wall / 2
    cy0, cy1 = -g["-y"] - wall / 2, b["width"] + g["+y"] + wall / 2
    spec = {
        "name": f"{bundle}_enclosure",
        "bundle": bundle,
        "clearance_source": rules["source"],
        "clearances": c,
        "fasteners": {"board": style, "lid": style, "screw": "M2.5"},
        "walls": {"wall": wall, "floor": 2.0, "lid": 2.0, "outer_fillet": 1.0},
        "box": {"gap": g, "inner_top": round(env[5] + cfg["enclosure"]["lid_gap_above_parts"], 2)},
        "standoffs": {"height": 4.0, "diameter": 5.5},
        "ports": [{"name": p["name"], "enabled": True, "extra": 0.0, "open_top": True} for p in comp["ports"]],
        "lid": {"lip_height": 3.0, "lip_width": 1.5, "notch_fillers": True},
        "lid_screws": {"boss_diameter": 5.5, "positions": [[cx0, cy0], [cx1, cy0], [cx0, cy1], [cx1, cy1]]},
        "vents": [],
        "cable_slots": [{"name": a["name"], "enabled": False, "extra": 0.0, "open_top": False}
                        for a in comp["cable_access"]],
        "mount_tabs": [],
        "ribs": [],
    }
    fan = next((a["fan"] for a in comp["addons"] if "fan" in a), None)
    if fan:
        d = fan["intake_diameter"] + 2
        spec["vents"].append({"face": "lid", "centre": fan["intake_centre"], "size": [d, d],
                              "slot_width": 2.0, "pitch": 4.0, "along": "u"})
    spec["mount_tabs"] = [
        {"side": "-y", "offset": 55.0, "width": 10.0, "length": 8.0, "thickness": 3.0, "hole": 3.4},
        {"side": "-y", "offset": 78.0, "width": 10.0, "length": 8.0, "thickness": 3.0, "hole": 3.4},
        {"side": "+y", "offset": 10.0, "width": 10.0, "length": 8.0, "thickness": 3.0, "hole": 3.4},
        {"side": "+y", "offset": 75.0, "width": 10.0, "length": 8.0, "thickness": 3.0, "hole": 3.4},
    ] if bundle.startswith("rpi5") else []
    return spec


def validate(spec):
    """Raise ValueError with a readable message if the spec is invalid. Returns the spec."""
    try:
        jsonschema.validate(spec, SCHEMA)
    except jsonschema.ValidationError as e:
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        raise ValueError(f"spec invalid at {where}: {e.message}") from None
    comp = load_bundle(spec["bundle"])
    names = {p["name"] for p in comp["ports"]}
    for p in spec["ports"]:
        if p["name"] not in names:
            raise ValueError(f"spec invalid: port '{p['name']}' not in component {spec['bundle']}")
    cables = {a["name"] for a in comp["cable_access"]}
    for s in spec["cable_slots"]:
        if s["name"] not in cables:
            raise ValueError(f"spec invalid: cable slot '{s['name']}' not in component {spec['bundle']}")
    w = spec["walls"]
    if w["outer_fillet"] >= w["wall"]:
        raise ValueError(f"spec invalid: outer_fillet {w['outer_fillet']} must be < wall {w['wall']}")
    if spec["lid"]["lip_width"] > 5:
        raise ValueError(f"spec invalid: lid lip_width {spec['lid']['lip_width']} > 5")
    for v in spec["vents"]:
        if v["pitch"] <= v["slot_width"]:
            raise ValueError(f"spec invalid: vent pitch {v['pitch']} must be > slot_width {v['slot_width']}")
    return spec


def apply_patch(spec, patch):
    """Apply a JSON patch (RFC 6902) and validate. Returns a new spec; the input is not changed."""
    new = jsonpatch.apply_patch(copy.deepcopy(spec), patch)
    return validate(new)


def diff(old, new):
    """Readable JSON patch from old to new."""
    return jsonpatch.make_patch(old, new).patch


def dumps(spec):
    return json.dumps(spec, indent=1, sort_keys=True)
