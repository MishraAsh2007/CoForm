"""Assembly check (owned by the Assembly agent).

check(spec, brief, cfg, built=None) -> {"passed", "failures": [{code, msg, amount_mm}], "metrics"}

Sub-checks:
  collision     base/lid vs every part, keeping at least checks.min_part_gap
  drop_in       the board can be lowered straight into the base
  lid           lid closes: lid vs base, lid gap above parts
  plug          every required port has its opening and room for the cable plug
  screwdriver   straight line above every screw is clear; head fits; a stock screw length works
  cable         required cable slots are enabled and clear
  tabs          robot mounting tabs: count, bolt head and driver access
  box           outer size within the brief's allowed box
  sides         openings only on the sides the brief allows
  airflow       lid vent over the fan intake (if the bundle has a fan)
"""
import math

import cadquery as cq

from cad.components import SIDES, plug_zones
from cad.enclosure import build
from checks.geom import bbox_of, cylinder, inflate, overlap


def _fail(out, code, msg, amount=0.0, **info):
    out.append({"code": code, "msg": msg, "amount_mm": round(float(amount), 2), **info})


def _board_sweep(comp, z0, z1):
    b = comp["board"]
    s = (cq.Workplane("XY").workplane(offset=z0).box(b["length"], b["width"], z1 - z0, centered=False)
         .edges("|Z").fillet(b.get("corner_radius", 0.5)))
    d = comp["mounting_holes"]["diameter"]
    for x, y in comp["mounting_holes"]["positions"]:
        s = s.cut(cq.Workplane("XY").workplane(offset=z0 - 1).center(x, y).circle(d / 2).extrude(z1 - z0 + 2))
    return s.val()


def check(spec, brief, cfg, built=None):
    c = cfg["checks"]
    built = built or build(spec)
    comp, meta = built["comp"], built["meta"]
    base, lid = built["base"].val(), built["lid"].val()
    bb_base, bb_lid = bbox_of(base), bbox_of(lid)
    lv = meta["levels"]
    gap = c["min_part_gap"]
    fails, metrics = [], {}
    parts = [k for k in comp["keepouts"] if k["name"] != "pcb"]

    # --- collision: base and lid vs parts (with the minimum gap) and vs the PCB ---
    for k in parts:
        for name, solid, bb in (("base", base, bb_base), ("lid", lid, bb_lid)):
            d = overlap(solid, inflate(k["box"], gap), bb)
            if d > 0:
                _fail(fails, "collision", f"{name} within {gap} mm of {k['name']} (overlap {d} mm)", d, target=k["name"], part=name, box=k["box"])
    pcb = _board_sweep(comp, 0.0, comp["board"]["thickness"])
    for name, solid in (("base", base), ("lid", lid)):
        d = overlap(solid, pcb)
        if d > 0:
            _fail(fails, "collision", f"{name} intersects the PCB by {d} mm", d, target="pcb", part=name)
    ib = meta["inner_box"]
    b = comp["board"]
    edge_gaps = {"-x": -ib[0], "+x": ib[1] - b["length"], "-y": -ib[2], "+y": ib[3] - b["width"]}
    metrics["board_edge_gap_mm"] = {s: round(v, 2) for s, v in edge_gaps.items()}
    for s, v in edge_gaps.items():
        if v < gap:
            _fail(fails, "collision", f"board edge to {s} wall gap {v:.2f} mm < {gap} mm", gap - v, target="wall_gap", side=s)

    # --- drop-in: lower the board + parts straight down into the base ---
    top = lv["top"] + 40
    d = overlap(base, _board_sweep(comp, 0.0, top))
    if d > 0:
        _fail(fails, "drop_in", f"board outline hits the base while lowering it in ({d} mm)", d, target="pcb")
    for k in parts:
        x0, x1, y0, y1, z0, _ = k["box"]
        d = overlap(base, [x0, x1, y0, y1, z0, top], bb_base)
        if d > 0:
            _fail(fails, "drop_in", f"{k['name']} hits the base while lowering the board in ({d} mm)", d, target=k["name"], box=k["box"])

    # --- lid closes ---
    d = overlap(lid, base)
    if d > 0:
        _fail(fails, "lid", f"lid collides with base by {d} mm (lip/fillers vs walls/bosses)", d, target="lid_base")
    tallest = max(k["box"][5] for k in parts)
    lid_gap = round(lv["top"] - tallest, 2)
    metrics["lid_gap_above_tallest_part_mm"] = lid_gap
    if lid_gap < c["lid_min_gap"]:
        _fail(fails, "lid", f"lid underside only {lid_gap} mm above the tallest part (need {c['lid_min_gap']})",
              c["lid_min_gap"] - lid_gap, target="lid_gap")

    # --- ports: required openings and plug room ---
    required = set(brief.get("required_ports", [p["name"] for p in comp["ports"]]))
    enabled = {p["name"] for p in spec["ports"] if p["enabled"]}
    for name in sorted(required - enabled):
        _fail(fails, "plug", f"required port {name} has no opening", 10.0, target=name, kind="missing")
    plug_ok = 0
    for z in plug_zones(comp):
        if z["name"] not in required:
            continue
        worst = 0.0
        for name, solid, bb in (("base", base, bb_base), ("lid", lid, bb_lid)):
            d = overlap(solid, z["box"], bb)
            if d > 0:
                worst = max(worst, d)
                _fail(fails, "plug", f"{z['name']} plug zone hits the {name} by {d} mm", d, target=z["name"], kind="zone", part=name)
        plug_ok += worst == 0
    metrics["plugs_clear"] = f"{plug_ok}/{len([z for z in plug_zones(comp) if z['name'] in required])}"

    # --- screwdriver access, head room, screw length ---
    lengths = c["screw_lengths_available"]
    for s in meta["screws"]:
        is_board = s["name"].startswith("board")
        reach = cylinder(s["x"], s["y"], c["screwdriver_diameter"], s["head_z"] + 2.0, s["head_z"] + 80)
        head = cylinder(s["x"], s["y"], c["screw_head_diameter"], s["head_z"], s["head_z"] + 2.0)
        blockers = list(parts) if is_board else []
        for k in blockers:
            for what, shape in (("screwdriver", reach), ("screw head", head)):
                d = overlap(shape, k["box"])
                if d > 0:
                    _fail(fails, "screwdriver", f"{s['name']}: {what} blocked by {k['name']} ({d} mm)", d, target=s["name"], kind="blocked")
        # base never blocks the driver for a top-driven screw unless something hangs over it
        d = overlap(base, reach)
        if d > 0:
            _fail(fails, "screwdriver", f"{s['name']}: screwdriver blocked by the base ({d} mm)", d, target=s["name"], kind="blocked")
        ok, why = _screw_length(s, spec, comp, lv, lengths, c["min_screw_engagement"])
        if not ok:
            _fail(fails, "screwdriver", f"{s['name']}: {why}", 2.0, target=s["name"], kind="length")

    # --- cable slots ---
    slots = {o["name"]: o for o in meta["openings"] if o["kind"] == "cable"}
    cables = {a["name"]: a for a in comp["cable_access"]}
    for name in brief.get("required_cables", []):
        if name not in slots:
            _fail(fails, "cable", f"required cable {name} has no slot", 10.0, target=name, kind="missing")
            continue
        a = cables[name]
        axis, sign = SIDES[a["side"]]
        inner, outer = meta["wall_faces"][a["side"]]
        u, zc = a["centre"]
        w, h = a["size"]
        lo, hi = sorted([inner - sign * 1.0, outer + sign * 10.0])
        zone = [lo, hi, u - w / 2, u + w / 2, zc - h / 2, zc + h / 2] if axis == 0 else \
            [u - w / 2, u + w / 2, lo, hi, zc - h / 2, zc + h / 2]
        for nm, solid, bb in (("base", base, bb_base), ("lid", lid, bb_lid)):
            d = overlap(solid, zone, bb)
            if d > 0:
                _fail(fails, "cable", f"{name} cable path hits the {nm} by {d} mm", d, target=name, kind="path")

    # --- robot mounting tabs ---
    tabs = spec["mount_tabs"]
    need = brief.get("mount_tabs_min", 0)
    metrics["mount_tabs"] = len(tabs)
    if len(tabs) < need:
        _fail(fails, "tabs", f"{len(tabs)} mounting tabs, brief needs {need}", float(need - len(tabs)), kind="count", need=need)
    pz = plug_zones(comp)
    for i, t in enumerate(tabs):
        axis, sign = SIDES[t["side"]]
        outer = meta["wall_faces"][t["side"]][1]
        hc = outer + sign * t["length"] / 2
        hx, hy = (hc, t["offset"]) if axis == 0 else (t["offset"], hc)
        ztop = lv["floor_bot"] + t["thickness"]
        head = cylinder(hx, hy, c["tab_bolt_head_diameter"], ztop + 0.01, ztop + 3)
        reach = cylinder(hx, hy, c["screwdriver_diameter"], ztop + 3, ztop + 80)
        for what, shape in (("bolt head", head), ("screwdriver", reach)):
            for nm, solid in (("base", base), ("lid", lid)):
                d = overlap(shape, solid)
                if d > 0:
                    _fail(fails, "tabs", f"tab {i + 1} ({t['side']} @ {t['offset']}): {what} hits the {nm} by {d} mm", d, kind="blocked", index=i)
            for z in pz:
                if z["name"] in required:
                    d = overlap(shape, z["box"])
                    if d > 0:
                        _fail(fails, "tabs", f"tab {i + 1}: {what} blocked by {z['name']} plug zone ({d} mm)", d, kind="blocked", index=i)

    # --- outer size ---
    full = [min(bb_base[0], bb_lid[0]), max(bb_base[1], bb_lid[1]), min(bb_base[2], bb_lid[2]),
            max(bb_base[3], bb_lid[3]), min(bb_base[4], bb_lid[4]), max(bb_base[5], bb_lid[5])]
    size = [round(full[1] - full[0], 2), round(full[3] - full[2], 2), round(full[5] - full[4], 2)]
    metrics["outer_size_mm"] = size
    limit = brief.get("max_box", c["max_box"])
    for axis, s, m in zip("xyz", size, limit):
        if s > m:
            _fail(fails, "box", f"outer size {axis} {s} mm > allowed {m} mm", s - m, axis=axis)

    # --- openings only on allowed sides ---
    allowed = brief.get("allowed_opening_sides")
    if allowed:
        for o in meta["openings"]:
            if o["side"] not in allowed:
                _fail(fails, "sides", f"opening {o['name']} on {o['side']} side; brief allows only {allowed}", 5.0, target=o["name"], kind=o["kind"])
        for v in spec["vents"]:
            if v["face"] != "lid" and v["face"] not in allowed:
                _fail(fails, "sides", f"vent on {v['face']} side; brief allows only {allowed}", 5.0, target="vent", kind="vent")

    # --- airflow over the fan ---
    fan = next((a["fan"] for a in comp["addons"] if "fan" in a), None)
    if fan and brief.get("vent_over_fan", True):
        frac = _vent_fraction(spec, fan)
        metrics["fan_intake_vented_fraction"] = round(frac, 2)
        want = brief.get("min_vent_fraction", 0.25)
        if frac < want:
            _fail(fails, "airflow", f"lid vents open {frac:.0%} of the fan intake (need {want:.0%})",
                  (want - frac) * fan["intake_diameter"])

    return {"name": "assembly", "passed": not fails, "failures": fails, "metrics": metrics}


def _screw_length(s, spec, comp, lv, lengths, engage):
    """Pick a stock screw that engages enough and does not stick out below the base."""
    if s["name"].startswith("board"):
        grip = comp["board"]["thickness"]
        depth = spec["standoffs"]["height"] + spec["walls"]["floor"]   # hole runs through standoff + floor
        if s["style"] == "pilot":
            ok = [L for L in lengths if L - grip >= engage and L - grip <= depth]
            return (bool(ok), f"no stock length gives {engage} mm engagement within {depth} mm of post"
                    if not ok else f"M2.5x{min(ok)}")
        nut_pos = depth - spec["clearances"]["nut_pocket_depth"]
        ok = [L for L in lengths if grip + nut_pos + 2.0 <= L <= grip + depth]
        return (bool(ok), f"through+nut needs {grip + nut_pos + 2.0:.1f}..{grip + depth:.1f} mm, no stock length fits"
                if not ok else f"M2.5x{min(ok)}")
    grip = spec["walls"]["lid"]
    depth = lv["top"] - lv["floor_bot"]
    if s["style"] == "pilot":
        ok = [L for L in lengths if L - grip >= engage and L - grip <= depth]
        return (bool(ok), "no stock length fits the lid boss" if not ok else f"M2.5x{min(ok)}")
    nut_pos = depth - spec["clearances"]["nut_pocket_depth"]
    ok = [L for L in lengths if grip + nut_pos + 2.0 <= L <= grip + depth]
    return (bool(ok), f"lid through+nut needs {grip + nut_pos + 2.0:.1f}..{grip + depth:.1f} mm screw, no stock length fits"
            if not ok else f"M2.5x{min(ok)}")


def _vent_fraction(spec, fan, n=40):
    """Share of the fan intake circle under lid vent slots (grid sampling)."""
    from cad.enclosure import _slots
    cx, cy = fan["intake_centre"]
    r = fan["intake_diameter"] / 2
    slots = []
    for v in spec["vents"]:
        if v["face"] == "lid":
            slots += _slots((*v["centre"], *v["size"]), v["slot_width"], v["pitch"], v.get("along", "u"))
    inside = hit = 0
    for i in range(n):
        for j in range(n):
            x = cx - r + (i + 0.5) * 2 * r / n
            y = cy - r + (j + 0.5) * 2 * r / n
            if math.hypot(x - cx, y - cy) > r:
                continue
            inside += 1
            hit += any(u0 <= x <= u1 and v0 <= y <= v1 for u0, u1, v0, v1 in slots)
    return hit / inside if inside else 0.0
