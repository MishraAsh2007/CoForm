"""Tested mechanical building blocks the drafting agent can place instead of inventing every mechanism.

A component is built from the reference body it serves, so it fits by construction: a clamp takes the desk's
top, bottom and edge; a cup takes the bottle's radius and base. Each expands into ordinary parameters,
features and hardware bodies when a spec is read, so repairs, the optimiser and the checks see nothing new.
Functional voids (a cup's interior, screw holes, nut pockets) are cut after every added feature, so no
connecting piece can fill them. Dimensions the optimiser may tune are parameters named "<component>_<what>".

    {"type": "desk_clamp", "name": "clamp", "grips": "desk", "edge": "+x", "along": 0, "width": 40, "reach": 40,
     "screw_d": 6, "gap": 4, "drop": 0}
    {"type": "cup", "name": "cup", "holds": "bottle", "clearance": 1, "height": 40, "drain_r": 4}
    {"type": "cradle", "name": "saddle", "holds": "tube", "length": 40}
    {"type": "wall_plate", "name": "plate", "mounts": "wall", "face": "+x", "center": [0, 60], "width": 40,
     "height": 80, "screws": 2, "screw_d": 4}
    {"type": "hook", "name": "hook", "holds": "strap", "direction": "+x", "start": 1, "width": 25, "lip": 10}
"""
from __future__ import annotations

AXES = "xyz"


def _relation(left, bound, op, right, other_bound, axis):
    return {"left_feature": left, "left_bound": bound, "op": op,
            "right_feature": right, "right_bound": other_bound, "axis": axis}


def _through(cut, solid, axis):
    return [_relation(cut, "min", "le", solid, "min", axis), _relation(cut, "max", "ge", solid, "max", axis)]


def _n(x: float) -> str:
    return f"{x:.6g}"


def _box(label: str, ranges, op: str = "add") -> dict:
    """A box from three (low, high) expression pairs in world axes."""
    return {"label": label, "op": op, "shape": "box", "at": [lo for lo, _ in ranges],
            "size": [f"({hi}) - ({lo})" for lo, hi in ranges]}


def _cyl(label: str, at, axis: str, radius, length, op: str = "add") -> dict:
    return {"label": label, "op": op, "shape": "cylinder", "at": at, "axis": axis, "radius": radius, "length": length}


def _param(value, low, high, role="thickness", note=""):
    return {"value": value, "min": low, "max": high, "role": role, "note": note}


def _body(raw: dict, fixed: dict, name: str, role: str, shapes=("box", "cylinder")) -> dict:
    """A reference body's numbers (evaluated with fixed dimensions only)."""
    from .freeform import evaluate
    body = next((b for b in raw.get("environment", []) if b.get("name") == name), None)
    if body is None or body.get("role") != role or body.get("shape") not in shapes:
        raise ValueError(f"needs a {role} body named '{name}' ({' or '.join(shapes)}) in the environment")
    at = [evaluate(v, fixed) for v in body["at"]]
    if body["shape"] == "box":
        size = [evaluate(v, fixed) for v in body["size"]]
        return {"shape": "box", "lo": at, "hi": [a + s for a, s in zip(at, size)]}
    axis = AXES.index(body.get("axis", "z"))
    r, length = evaluate(body["radius"], fixed), evaluate(body["length"], fixed)
    lo, hi = [a - r for a in at], [a + r for a in at]
    lo[axis], hi[axis] = at[axis], at[axis] + length
    return {"shape": "cylinder", "base": at, "axis": axis, "radius": r, "length": length, "lo": lo, "hi": hi}


def _edge(edge: str) -> tuple[int, int, int]:
    if edge not in ("+x", "-x", "+y", "-y"):
        raise ValueError("edge must be +x, -x, +y or -y: the desk face the clamp wraps around")
    k = AXES.index(edge[1])
    return k, 1 if edge[0] == "+" else -1, 1 - k  # outward axis, its sign, the axis along the edge


def desk_clamp(use: dict, raw: dict, fixed: dict) -> dict:
    """C-clamp over a desk or shelf edge: upper jaw on top, spine past the edge, lower jaw below with a thumb
    screw (in a slide-in nut seat) pressing up on the underside. With screw_d 0 the lower jaw touches directly."""
    name, desk = use["name"], _body(raw, fixed, use["grips"], "mount", ("box",))
    k, s, j = _edge(use.get("edge", "+x"))
    edge_at = desk["hi"][k] if s > 0 else desk["lo"][k]
    top, bottom = desk["hi"][2], desk["lo"][2]
    along = float(use.get("along", (desk["lo"][j] + desk["hi"][j]) / 2))
    width, reach = float(use.get("width", 40)), float(use.get("reach", 40))
    screw_d = float(use.get("screw_d", 6))
    gap = float(use.get("gap", 4 if screw_d else 0))
    drop = float(use.get("drop", 0))
    jaw, spine = f"{name}_jaw_t", f"{name}_spine_t"
    nut_h, nut_w = 0.8 * screw_d + 0.4, 1.8 * screw_d + 0.4  # hex nut thickness and across-corners, with clearance
    jaw_min = nut_h + 3 if screw_d else 3
    params = {jaw: _param(max(5.0, jaw_min + 2), jaw_min, jaw_min + 8, note="jaw thickness"),
              spine: _param(6, 4, 12, note="spine thickness past the desk edge")}

    def u(lo, hi):  # outward from the edge face, in world terms along axis k
        return (f"{_n(edge_at)} + ({lo})", f"{_n(edge_at)} + ({hi})") if s > 0 else (f"{_n(edge_at)} - ({hi})", f"{_n(edge_at)} - ({lo})")

    def world(uk, vj, z):
        ranges = [None, None, z]
        ranges[k], ranges[j] = uk, vj
        return ranges
    v = (_n(along - width / 2), _n(along + width / 2))
    low = f"{_n(bottom - gap)} - {jaw}"
    adds = [_box(f"{name} upper jaw", world(u(-reach, spine), v, (_n(top), f"{_n(top)} + {jaw}"))),
            _box(f"{name} spine", world(u(0, spine), v, (f"{low} - {_n(drop)}", f"{_n(top)} + {jaw}"))),
            _box(f"{name} lower jaw", world(u(-reach, spine), v, (low, _n(bottom - gap))))]
    cuts, hardware = [], []
    if screw_d:
        if gap <= 0:
            raise ValueError("a screw clamp needs gap > 0: the travel between the lower jaw and the desk")
        at = [0.0, 0.0, 0.0]
        at[k], at[j] = edge_at - s * reach / 2, along
        r = screw_d / 2
        deepest = bottom - gap - (jaw_min + 8)  # under the thickest jaw the optimiser may choose
        hole_at = [_n(at[0]), _n(at[1]), f"{low} - 1"]
        cuts.append(_cyl(f"{name} screw hole", hole_at, "z", _n(r), f"{jaw} + 2", op="subtract"))
        # Nut seat: a pocket at mid-jaw, open toward the jaw's tip so the nut slides in from there.
        mid = f"{low} + {jaw} / 2 - {_n(nut_h / 2)}"
        seat_u = u(-reach - 1, -reach / 2 + nut_w / 2)
        seat_v = (_n(along - nut_w / 2), _n(along + nut_w / 2))
        cuts.append(_box(f"{name} nut seat", world(seat_u, seat_v, (mid, f"{mid} + {_n(nut_h)}")), op="subtract"))
        screw_at = [_n(at[0]), _n(at[1]), _n(deepest - 15)]
        other = [f"+{AXES[k]}", f"-{AXES[k]}", f"+{AXES[j]}", f"-{AXES[j]}"]
        hardware.append({"name": f"{name}_screw", "role": "hardware", "shape": "cylinder", "at": screw_at, "axis": "z",
                         "radius": _n(r), "length": _n(bottom - (deepest - 15)), "insert": "+z", "retain": other,
                         "bears_on": use["grips"], "note": f"M{screw_d:g} thumb screw tightened up against the underside"})
        nut_at = [None, None, mid]  # follows the jaw thickness: hardware goes where the design puts its seat
        nut_at[k], nut_at[j] = _n(at[k] - nut_w / 2), _n(along - nut_w / 2)
        hardware.append({"name": f"{name}_nut", "role": "hardware", "shape": "box", "at": nut_at,
                         "size": [_n(nut_w), _n(nut_w), _n(nut_h)], "insert": f"{'+' if s > 0 else '-'}{AXES[k]}",
                         "retain": ["+z", "-z", f"+{AXES[j]}", f"-{AXES[j]}"], "note": "Nut slid into its seat from the jaw tip"})
    return {"parameters": params, "adds": adds, "cuts": cuts, "hardware": hardware,
            "relationships": _through(f"{name} screw hole", f"{name} lower jaw", "z") if screw_d else []}


def cup(use: dict, raw: dict, fixed: dict) -> dict:
    """Open-top cup around an upright cylinder (bottle, can, pen pot): floor under it, wall around it, drain hole."""
    name, held = use["name"], _body(raw, fixed, use["holds"], "payload", ("cylinder",))
    if held["axis"] != 2:
        raise ValueError("a cup holds an upright cylinder (axis z); use a cradle for one lying down")
    cx, cy, bz = held["base"]
    inner = held["radius"] + float(use.get("clearance", 1))
    height, drain = float(use.get("height", min(40.0, held["length"] / 2))), float(use.get("drain_r", 4))
    wall, floor = f"{name}_wall_t", f"{name}_floor_t"
    params = {wall: _param(3, 2, 6, note="cup wall"), floor: _param(3, 2, 6, note="cup floor")}
    outer = f"{_n(inner)} + {wall}"
    adds = [_cyl(f"{name} floor", [_n(cx), _n(cy), f"{_n(bz)} - {floor}"], "z", outer, floor),
            _cyl(f"{name} wall", [_n(cx), _n(cy), _n(bz)], "z", outer, _n(height))]
    cuts = [_cyl(f"{name} void", [_n(cx), _n(cy), _n(bz)], "z", _n(inner), _n(height + 1), op="subtract")]
    if drain:
        cuts.append(_cyl(f"{name} drain", [_n(cx), _n(cy), f"{_n(bz - 1)} - {floor}"], "z", _n(drain), f"{floor} + 2", op="subtract"))
    relations = [_relation(f"{name} void", "min", "ge", f"{name} floor", "max", "z"),
                 _relation(f"{name} wall", "min", "eq", f"{name} floor", "max", "z")]
    if drain:
        relations += _through(f"{name} drain", f"{name} floor", "z")
    return {"parameters": params, "adds": adds, "cuts": cuts, "hardware": [], "relationships": relations}


def cradle(use: dict, raw: dict, fixed: dict) -> dict:
    """Saddle under a cylinder lying along x or y: a block with a round cut that wraps it up to its centre line."""
    name, held = use["name"], _body(raw, fixed, use["holds"], "payload", ("cylinder",))
    a = held["axis"]
    if a == 2:
        raise ValueError("a cradle holds a cylinder lying along x or y; use a cup for an upright one")
    c = 1 - a
    r, base = held["radius"], held["base"]
    length = float(use.get("length", min(40.0, held["length"])))
    start = base[a] + (held["length"] - length) / 2
    t, side = f"{name}_base_t", f"{name}_wall_t"
    params = {t: _param(4, 2, 10, note="material under the cylinder"), side: _param(4, 2, 8, note="saddle sides")}
    ranges = [None, None, (f"{_n(base[2] - r)} - {t}", _n(base[2]))]
    ranges[a] = (_n(start), _n(start + length))
    ranges[c] = (f"{_n(base[c] - r)} - {side}", f"{_n(base[c] + r)} + {side}")
    cut_at = [_n(v) for v in base]
    cut_at[a] = _n(start - 1)
    return {"parameters": params, "adds": [_box(f"{name} block", ranges)],
            "cuts": [_cyl(f"{name} seat", cut_at, AXES[a], _n(r), _n(length + 2), op="subtract")], "hardware": [],
            "relationships": _through(f"{name} seat", f"{name} block", AXES[a])}


def wall_plate(use: dict, raw: dict, fixed: dict) -> dict:
    """Plate flat on a mount's face, with screws through it into the mount (holes sized to the screws)."""
    name, wall = use["name"], _body(raw, fixed, use["mounts"], "mount", ("box",))
    face = use.get("face", "+x")
    if face not in ("+x", "-x", "+y", "-y", "+z", "-z"):
        raise ValueError("face must be one of +x, -x, +y, -y, +z, -z")
    n, s = AXES.index(face[1]), 1 if face[0] == "+" else -1
    p, q = [i for i in range(3) if i != n]
    at_face = wall["hi"][n] if s > 0 else wall["lo"][n]
    cp, cq = (float(v) for v in use.get("center", [(wall["lo"][p] + wall["hi"][p]) / 2, (wall["lo"][q] + wall["hi"][q]) / 2]))
    width, height = float(use.get("width", 40)), float(use.get("height", 80))
    count, screw_d = int(use.get("screws", 2)), float(use.get("screw_d", 4))
    t = f"{name}_t"
    params = {t: _param(5, 3, 10, note="plate thickness")}
    ranges = [None, None, None]
    ranges[n] = (_n(at_face), f"{_n(at_face)} + {t}") if s > 0 else (f"{_n(at_face)} - {t}", _n(at_face))
    ranges[p], ranges[q] = (_n(cp - width / 2), _n(cp + width / 2)), (_n(cq - height / 2), _n(cq + height / 2))
    cuts, hardware = [], []
    for i in range(count):
        offset = 0.0 if count == 1 else (i / (count - 1) - 0.5) * (height - 4 * screw_d)
        hole = [None, None, None]
        hole[p], hole[q] = _n(cp), _n(cq + offset)
        hole[n] = _n(at_face - 1) if s > 0 else f"{_n(at_face + 1)} - {t} - 2"
        cuts.append(_cyl(f"{name} screw hole {i + 1}", hole, AXES[n], _n(screw_d / 2), f"{t} + 2", op="subtract"))
        # 20 mm into the mount and 20 mm out of it (past the thickest plate): the same span for either facing.
        screw = [None, None, None]
        screw[p], screw[q], screw[n] = _n(cp), _n(cq + offset), _n(at_face - 20)
        hardware.append({"name": f"{name}_screw_{i + 1}", "role": "hardware", "shape": "cylinder", "at": screw,
                         "axis": AXES[n], "radius": _n(screw_d / 2), "length": "40",
                         "insert": f"{'-' if s > 0 else '+'}{AXES[n]}", "retain": [f"+{AXES[p]}", f"-{AXES[p]}", f"+{AXES[q]}", f"-{AXES[q]}"],
                         "bears_on": use["mounts"], "note": f"{screw_d:g} mm screw driven into the {use['mounts']}"})
    return {"parameters": params, "adds": [_box(f"{name} plate", ranges)], "cuts": cuts, "hardware": hardware,
            "relationships": [r for cut in cuts for r in _through(cut["label"], f"{name} plate", AXES[n])]}


def hook(use: dict, raw: dict, fixed: dict) -> dict:
    """Arm under a hanging load (a strap or loop, modelled as a box) with a lip past its far side to keep it on."""
    name, held = use["name"], _body(raw, fixed, use["holds"], "payload", ("box",))
    direction = use.get("direction", "+x")
    k, s, j = _edge(direction)
    start = float(use["start"])
    width = float(use.get("width", held["hi"][j] - held["lo"][j]))
    center = (held["lo"][j] + held["hi"][j]) / 2
    lip_h = float(use.get("lip", 10))
    arm, lip = f"{name}_arm_t", f"{name}_lip_t"
    params = {arm: _param(6, 3, 12, note="arm depth under the load"), lip: _param(4, 2, 8, note="lip thickness")}
    under = held["lo"][2]
    far = held["hi"][k] if s > 0 else held["lo"][k]
    along = (_n(center - width / 2), _n(center + width / 2))

    def span(a, b):  # along the hook direction, from coordinate a outward by expression b
        return (a, f"{a} + {b}") if s > 0 else (f"{a} - {b}", a)
    arm_range = [None, None, (f"{_n(under)} - {arm}", _n(under))]
    arm_range[k] = (_n(min(start, far)), f"{_n(far)} + {lip}") if s > 0 else (f"{_n(far)} - {lip}", _n(max(start, far)))
    arm_range[j] = along
    lip_range = [None, None, (f"{_n(under)} - {arm}", _n(under + lip_h))]
    lip_range[k] = span(_n(far), lip)
    lip_range[j] = along
    return {"parameters": params, "adds": [_box(f"{name} arm", arm_range), _box(f"{name} lip", lip_range)], "cuts": [], "hardware": []}


def join(use: dict, raw: dict, fixed: dict, features: list | None = None) -> dict:
    """A bridge between two pieces named by label, measured by code: across the extent they share and
    from one into the other where they are apart. Drafting agents placed such webs a few mm off."""
    from .freeform import Feature, bridge, feature_bounds
    values = {n: float(p["value"]) for n, p in (raw.get("parameters") or {}).items() if isinstance(p, dict) and "value" in p}
    between = use.get("between")
    if not isinstance(between, list) or len(between) != 2:
        raise ValueError("needs between: [label of one piece, label of the other]")
    bounds = []
    for label in between:
        found = next((f for f in (features or []) if f.get("label") == label and f.get("op", "add") == "add"), None)
        if found is None:
            known = ", ".join(repr(f.get("label")) for f in (features or []) if f.get("op", "add") == "add")
            raise ValueError(f"cannot find an added piece labelled '{label}'; pieces so far: {known}")
        bounds.append(feature_bounds(Feature.model_validate(found), values))
    lo, hi = bridge(*bounds)
    return {"parameters": {}, "cuts": [], "hardware": [],
            "adds": [{"label": use["name"], "shape": "box", "at": [_n(v) for v in lo], "size": [_n(h - l) for l, h in zip(lo, hi)]}]}


NAMED = {"type", "name", "grips", "holds", "mounts", "edge", "face", "direction", "between"}
COMPONENTS = {"desk_clamp": desk_clamp, "cup": cup, "cradle": cradle, "wall_plate": wall_plate, "hook": hook, "join": join}


def expand(raw: dict) -> dict:
    """Replace raw["components"] with ordinary parameters, features and hardware bodies."""
    import re
    uses = raw.get("components") or []
    out = {k: v for k, v in raw.items() if k != "components"}
    if not uses:
        return out
    if not isinstance(uses, list):
        raise ValueError("components must be a list")
    parameters = dict(out.get("parameters") or {})
    fixed = {}
    from .freeform import evaluate
    for key, p in parameters.items():
        if isinstance(p, dict) and p.get("role") == "fixed" and "value" in p:
            fixed[key] = float(evaluate(p["value"], {}))
    adds, cuts, hardware = [], [], []
    relationships = list(out.get("relationships") or [])
    own = list(out.get("features") or [])
    values = {}
    for key, p in parameters.items():
        try:
            values[key] = float(evaluate(p["value"], {}))
        except (KeyError, TypeError, ValueError):
            pass

    def settle(use: dict) -> dict:
        """Option values may be numbers or expressions over parameters ("clamp_width", "desk_thick + 5"); they
        are evaluated once, here. Names of bodies, faces and pieces stay as written."""
        out_use = {}
        for key, value in use.items():
            if key in NAMED:
                out_use[key] = value
                continue
            try:
                if isinstance(value, list):
                    out_use[key] = [evaluate(v, values) if isinstance(v, str) else v for v in value]
                else:
                    out_use[key] = evaluate(value, values) if isinstance(value, str) else value
            except ValueError as exc:
                raise ValueError(f"Component '{use.get('name', '')}' option {key}: {exc}") from None
        return out_use
    # Joins last: they measure the pieces every other component (and the agent's own features) made.
    for use in sorted(uses, key=lambda u: (u or {}).get("type") == "join"):
        kind, name = (use or {}).get("type"), (use or {}).get("name", "")
        if kind not in COMPONENTS:
            raise ValueError(f"Unknown component '{kind}'; available: {', '.join(COMPONENTS)}")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,20}", str(name)):
            raise ValueError("Each component needs a short lower_snake name")
        use = settle(use)
        try:
            made = (join(use, {**out, "parameters": parameters}, fixed, [*adds, *own]) if kind == "join"
                    else COMPONENTS[kind](use, out, fixed))
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Component '{name}' ({kind}) is missing a setting: {exc}") from None
        except ValueError as exc:
            raise ValueError(f"Component '{name}' ({kind}) {exc}") from None
        clash = set(made["parameters"]) & set(parameters)
        if clash:
            raise ValueError(f"Component '{name}' parameter names clash with {sorted(clash)}")
        parameters.update(made["parameters"])
        adds += made["adds"]
        cuts += made["cuts"]
        hardware += made["hardware"]
        relationships += made.get("relationships", [])
    out["parameters"] = parameters
    joins = [f for f in adds if f["label"] in {u.get("name") for u in uses if u.get("type") == "join"}]
    out["features"] = [*(f for f in adds if f not in joins), *own, *joins, *cuts]
    out["environment"] = [*(out.get("environment") or []), *hardware]
    out["relationships"] = relationships
    return out
