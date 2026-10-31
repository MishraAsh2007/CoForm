"""Rule-based fake model for --mock runs (zero API cost).

Each failure (with its structured target fields) maps to one concrete JSON-patch fix.
propose(agent, ctx, rng) returns the same JSON a real model would:
  {"action": "propose", "patch": [...], "reason": "..."}
  {"action": "note", "text": "...", "suggest_patch": [...]}   (fix needs keys the agent does not own)
  {"action": "pass", "reason": "..."}
vote(agent, ctx, proposal) -> {"vote": "support"|"object", "reason": "..."}
"""
import json

from cad.components import SIDES, plug_zones
from core import protocol


def _idx(items, name):
    return next((i for i, x in enumerate(items) if x["name"] == name), None)


def _rep(path, value):
    return {"op": "replace", "path": path, "value": round(value, 3) if isinstance(value, float) else value}


def _port_for(spec, part_name):
    """Port entry for a keep-out name like 'usb_a_lower_overhang'."""
    for i, p in enumerate(spec["ports"]):
        if part_name == p["name"] or part_name.startswith(p["name"] + "_"):
            return i
    return None


def _nearest_side(box, ctx):
    b = ctx["comp"]["board"]
    d = {"-x": box[0], "+x": b["length"] - box[1], "-y": box[2], "+y": b["width"] - box[3]}
    return min(d, key=d.get)


def _free_tab_offset(spec, ctx, side, skip=None, width=10.0):
    """First offset on a side that avoids required plug zones, enabled openings and other tabs."""
    comp, brief = ctx["comp"], ctx["brief"]
    axis, _ = SIDES[side]
    b = comp["board"]
    length = b["length"] if axis == 1 else b["width"]
    required = set(brief.get("required_ports", []))
    blocked = []
    for z in plug_zones(comp):
        if z["side"] == side and z["name"] in required:
            lo, hi = (z["box"][0], z["box"][1]) if axis == 1 else (z["box"][2], z["box"][3])
            blocked.append((lo - 4, hi + 4))
    for i, t in enumerate(spec["mount_tabs"]):
        if t["side"] == side and i != skip:
            blocked.append((t["offset"] - width - 1, t["offset"] + width + 1))
    o = 8.0
    while o <= length - 8.0:
        if all(not (lo < o + width / 2 and o - width / 2 < hi) for lo, hi in blocked):
            return o
        o += 2.0
    return None


def fix(check, f, spec, ctx):
    """(patch, reason) for one failure, or None if no rule applies."""
    rules = ctx["rules"]["values"]
    cfgc = ctx["cfg"]["checks"]
    code, amt = f["code"], f.get("amount_mm", 0.0) or 0.0
    t = f.get("target")
    why = f"fix {check}: {f['msg']}"

    if check == "assembly":
        if code == "collision" and t == "wall_gap":
            return [_rep(f"/box/gap/{f['side']}", max(rules["part_gap"], cfgc["min_part_gap"]) + 0.1)], why
        if code == "collision" and "box" in f:
            box = f["box"]
            if f.get("part") == "lid" and box[5] > spec["box"]["inner_top"] - 2:
                return [_rep("/box/inner_top", spec["box"]["inner_top"] + amt + 0.2)], why
            if box[4] < 0 and f.get("part") == "base" and box[5] <= 0.5:
                return [_rep("/standoffs/height", spec["standoffs"]["height"] + amt + 0.2)], why
            i = _port_for(spec, t)
            if i is not None:
                return [_rep(f"/ports/{i}/extra", spec["ports"][i].get("extra", 0) + amt + 0.1)], why
            s = _nearest_side(box, ctx)
            return [_rep(f"/box/gap/{s}", spec["box"]["gap"][s] + amt + 0.2)], why
        if code == "drop_in":
            i = _port_for(spec, t or "")
            if i is not None and not spec["ports"][i].get("open_top"):
                return [_rep(f"/ports/{i}/open_top", True)], why
            if "box" in f:
                s = _nearest_side(f["box"], ctx)
                return [_rep(f"/box/gap/{s}", spec["box"]["gap"][s] + amt + 0.2)], why
        if code == "lid" and t == "lid_gap":
            return [_rep("/box/inner_top", spec["box"]["inner_top"] + amt + 0.2)], why
        if code == "lid" and t == "lid_base":
            return [_rep("/clearances/lid_fit", spec["clearances"]["lid_fit"] + amt + 0.05)], why
        if code == "plug" and f.get("kind") == "missing":
            i = _idx(spec["ports"], t)
            if i is None:
                return [{"op": "add", "path": "/ports/-", "value": {"name": t, "enabled": True, "extra": 0.0,
                                                                     "open_top": True}}], why
            return [_rep(f"/ports/{i}/enabled", True)], why
        if code == "plug":
            i = _idx(spec["ports"], t)
            return [_rep(f"/ports/{i}/extra", spec["ports"][i].get("extra", 0) + amt + 0.1)], why
        if code == "screwdriver" and f.get("kind") == "length":
            which = "board" if t.startswith("board") else "lid"
            other = "pilot" if spec["fasteners"][which] == "through_nut" else "through_nut"
            return [_rep(f"/fasteners/{which}", other)], why
        if code == "cable":
            i = _idx(spec["cable_slots"], t)
            if f.get("kind") == "missing":
                if i is None:
                    return [{"op": "add", "path": "/cable_slots/-",
                             "value": {"name": t, "enabled": True, "extra": 0.0, "open_top": False}}], why
                return [_rep(f"/cable_slots/{i}/enabled", True)], why
            return [_rep(f"/cable_slots/{i}/extra", spec["cable_slots"][i].get("extra", 0) + amt + 0.1)], why
        if code == "tabs" and f.get("kind") == "count":
            for side in ("+y", "-y", "-x", "+x"):
                o = _free_tab_offset(spec, ctx, side)
                if o is not None:
                    return [{"op": "add", "path": "/mount_tabs/-", "value": {
                        "side": side, "offset": o, "width": 10.0, "length": 8.0, "thickness": 3.0, "hole": 3.4}}], why
        if code == "tabs" and f.get("kind") == "blocked":
            i = f["index"]
            tab = spec["mount_tabs"][i]
            for side in (tab["side"], "+y", "-y", "-x", "+x"):
                o = _free_tab_offset(spec, ctx, side, skip=i)
                if o is not None and (side, o) != (tab["side"], tab["offset"]):
                    return [_rep(f"/mount_tabs/{i}/side", side), _rep(f"/mount_tabs/{i}/offset", o)], why
        if code == "box":
            return _shrink(f["axis"], spec, ctx, why)
        if code == "sides":
            if f.get("kind") == "vent":
                keep = [v for v in spec["vents"] if v["face"] == "lid"]
                return [_rep("/vents", keep)], why
            arr = "ports" if f.get("kind") == "port" else "cable_slots"
            i = _idx(spec[arr], t)
            return [_rep(f"/{arr}/{i}/enabled", False)], why
        if code == "airflow":
            fan = next((a["fan"] for a in ctx["comp"]["addons"] if "fan" in a), None)
            if fan:
                d = fan["intake_diameter"] + 2
                return [{"op": "add", "path": "/vents/-", "value": {"face": "lid", "centre": fan["intake_centre"],
                                                                    "size": [d, d], "slot_width": 2.0, "pitch": 4.0,
                                                                    "along": "u"}}], why
    if check == "printability":
        need = f.get("need", 0)
        tt = t or ""
        if code == "wall":
            for k in ("wall", "floor", "lid"):
                if tt == f"{k} thickness":
                    return [_rep(f"/walls/{k}", need)], why
            if tt == "lid lip width":
                return [_rep("/lid/lip_width", need)], why
            if tt.startswith("standoff wall"):
                return [_rep("/standoffs/diameter", spec["standoffs"]["diameter"] + 2 * amt + 0.1)], why
            if tt.startswith("lid boss wall"):
                return [_rep("/lid_screws/boss_diameter", spec["lid_screws"]["boss_diameter"] + 2 * amt + 0.1)], why
        if code == "vent":
            i = f["index"]
            v = spec["vents"][i]
            return [_rep(f"/vents/{i}/pitch", v["slot_width"] + need + 0.1)], why
        if code == "sliver":
            for name in (tt, f.get("other")):
                for arr in ("ports", "cable_slots"):
                    i = _idx(spec[arr], name or "")
                    if i is not None and spec[arr][i].get("extra", 0) > 0:
                        return [_rep(f"/{arr}/{i}/extra", max(0.0, spec[arr][i]["extra"] - amt - 0.05))], why
            for arr in ("ports", "cable_slots"):
                i = _idx(spec[arr], tt)
                if i is not None and f.get("other") != "wall end":
                    return [_rep(f"/{arr}/{i}/extra", spec[arr][i].get("extra", 0) + f.get("gap", 0) + 0.05)], \
                        why + " (merge the two openings)"
        if code == "nut_pocket":
            which = "lid" if t == "lid" else "board"
            return [_rep(f"/fasteners/{which}", "pilot")], why
    if check == "preform" and code == "cups":
        pos = spec["drain_holes"]["positions"]
        b = ctx["comp"]["board"]
        cand = [[round(b["length"] * f, 1), round(b["width"] / 2, 1)] for f in (0.38, 0.62, 0.5, 0.25, 0.75)]
        new = next((p for p in cand if p not in pos), None)
        if new:
            return [{"op": "add", "path": "/drain_holes/positions/-", "value": new}], why + " (floor drain hole)"
    if check == "fit":
        if code == "clearance" and str(t).startswith("/"):
            return [_rep(t, f["need"])], why
        if code == "clearance" and t == "standoff":
            if f["need"] <= ctx["comp"]["mounting_holes"].get("pad_diameter", 6.0):
                return [_rep("/standoffs/diameter", f["need"])], why
            return [_rep("/fasteners/board", "through_nut")], why
        if code == "clearance" and t == "lid boss":
            return [_rep("/lid_screws/boss_diameter", f["need"])], why
        if code == "nut":
            af = rules["nut_pocket_af"] if rules["nut_pocket_af"] < 5.77 else 5.3
            return [_rep("/clearances/nut_pocket_af", af)], why
        if code == "fastener":
            which = "board" if t == "standoff" else "lid"
            return [_rep(f"/fasteners/{which}", "through_nut")], why
    return None


def _shrink(axis, spec, ctx, why):
    rules = ctx["rules"]["values"]
    gmin = max(rules["part_gap"], ctx["cfg"]["checks"]["min_part_gap"])
    g = spec["box"]["gap"]
    if axis in ("x", "y"):
        sides = ("-x", "+x") if axis == "x" else ("-y", "+y")
        for s in sides:
            if g[s] > gmin + 0.05:
                return [_rep(f"/box/gap/{s}", gmin)], why
        tabs = spec["mount_tabs"]
        on_axis = [i for i, t in enumerate(tabs) if t["side"] in sides]
        if on_axis:
            other = ("-y", "+y") if axis == "x" else ("-x", "+x")
            for side in other:
                o = _free_tab_offset(spec, ctx, side)
                enabled_here = [x for x in spec["ports"] + spec["cable_slots"] if x["enabled"]
                                and any(p["name"] == x["name"] and p["side"] == side
                                        for p in ctx["comp"]["ports"] + ctx["comp"]["cable_access"])]
                if o is not None and not enabled_here:
                    i = on_axis[0]
                    return [_rep(f"/mount_tabs/{i}/side", side), _rep(f"/mount_tabs/{i}/offset", o)], \
                        why + f" (move tab {i + 1} to {side})"
            for i in on_axis:
                if tabs[i]["length"] > 6.0:
                    return [_rep(f"/mount_tabs/{i}/length", 6.0)], why
        if spec["walls"]["wall"] > 1.6:
            return [_rep("/walls/wall", 1.6), _rep("/walls/outer_fillet", min(spec["walls"]["outer_fillet"], 1.0))], why
    if axis == "z":
        comp = ctx["comp"]
        tallest = max(k["box"][5] for k in comp["keepouts"])
        lowest = min(k["box"][4] for k in comp["keepouts"])
        want_top = round(tallest + ctx["cfg"]["checks"]["lid_min_gap"] + 0.1, 2)
        if spec["box"]["inner_top"] > want_top + 0.05:
            return [_rep("/box/inner_top", want_top)], why
        want_h = round(-lowest + gmin + 0.1, 2)
        if spec["standoffs"]["height"] > want_h + 0.05:
            return [_rep("/standoffs/height", want_h)], why
        for k in ("floor", "lid"):
            if spec["walls"][k] > 1.5:
                return [_rep(f"/walls/{k}", 1.5)], why
    return None


def brief_fixes(spec, ctx):
    """What the Design agent can see without any check: requirements written in the brief."""
    brief, comp = ctx["brief"], ctx["comp"]
    out = []
    allowed = brief.get("allowed_opening_sides")
    required = set(brief.get("required_ports", []))
    sides = {p["name"]: p["side"] for p in comp["ports"] + comp["cable_access"]}
    for arr in ("ports", "cable_slots"):
        for i, x in enumerate(spec[arr]):
            if allowed and x["enabled"] and sides[x["name"]] not in allowed and x["name"] not in required:
                out.append(([_rep(f"/{arr}/{i}/enabled", False)], f"brief: no openings on {sides[x['name']]}"))
    for name in brief.get("required_cables", []):
        i = _idx(spec["cable_slots"], name)
        if i is not None and not spec["cable_slots"][i]["enabled"]:
            out.append(([_rep(f"/cable_slots/{i}/enabled", True)], f"brief: cable {name} must leave the case"))
    fan = next((a["fan"] for a in comp["addons"] if "fan" in a), None)
    if fan and brief.get("vent_over_fan") and not any(v["face"] == "lid" for v in spec["vents"]):
        d = fan["intake_diameter"] + 2
        out.append(([{"op": "add", "path": "/vents/-", "value": {"face": "lid", "centre": fan["intake_centre"],
                                                                 "size": [d, d], "slot_width": 2.0, "pitch": 4.0,
                                                                 "along": "u"}}], "brief: vent over the fan"))
    return out


def _new(patch, ctx):
    return json.dumps(patch, sort_keys=True) not in ctx["rejected"]


def propose(agent, ctx, rng):
    spec = ctx["spec"]
    cands = []
    # 1. requests other agents posted for keys this agent owns
    for n in ctx.get("requests", []):
        if protocol.can_edit(agent, n["patch"]):
            cands.append((n["patch"], f"adopting request from {n['author']}: {n['text']}"))
    # 2. the brief (design and the single agents read it directly)
    if agent in ("design", "generalist"):
        cands += brief_fixes(spec, ctx)
    # 3. failures in checks this agent can see in full
    fails = []
    for check, r in ctx["results"].items():
        if "failures" in r:
            fails += [(check, f) for f in r["failures"]]
    rng.shuffle(fails)
    fails.sort(key=lambda cf: -(cf[1].get("amount_mm") or 0))
    for check, f in fails:
        got = fix(check, f, spec, ctx)
        if got:
            cands.append(got)
    notes = []
    for patch, reason in cands:
        if not _new(patch, ctx):
            continue
        if protocol.can_edit(agent, patch):
            return {"action": "propose", "patch": patch, "reason": reason}
        notes.append((patch, reason))
    for patch, reason in notes:
        key = json.dumps(patch, sort_keys=True)
        if key not in ctx.get("my_requests", set()):
            keys = protocol.forbidden_keys(agent, patch)
            return {"action": "note", "text": f"need an owner of {keys} to apply: {reason}", "suggest_patch": patch}
    return {"action": "pass", "reason": "nothing I can fix"}


def one_shot(ctx):
    """Design agent, once, no checks: apply every brief requirement it can read."""
    patch, reasons = [], []
    for p, r in brief_fixes(ctx["spec"], ctx):
        patch += p
        reasons.append(r)
    return {"action": "propose", "patch": patch, "reason": "; ".join(reasons) or "v0 as is"}


def vote(agent, ctx, proposal):
    before, after = ctx["before_total"], ctx["after_total"]
    if after < before:
        return {"vote": "support", "reason": f"total failures {before} -> {after}"}
    return {"vote": "object", "reason": f"total failures {before} -> {after}, no net gain"}
