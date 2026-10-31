"""Turn the hand-filled fit test (data/fit_test_results.yaml) into clearances and fastener rules.

rules(cfg, source) -> {"source", "values": {...}, "notes": [...], "fallbacks": [...]}

source = "generic": Formlabs design-guide values from config.yaml.
source = "measured": derived from the fit test, feature by feature. Any feature with no data
falls back to the generic value and is listed in "fallbacks" (and in notes), so it is always
visible when a "measured" design is really still generic.
"""
import math
from pathlib import Path

import yaml

from cad.common import ROOT

RESULTS = ("free", "snug", "press", "no_fit", "cracked", "failed_to_print")
KEY_PEG = 5.00      # the key's peg (test_print.key.peg_diameter)
KEY_TONGUE = 2.00   # the key's tongue
NUT_AF = 5.0        # ISO 4032 M2.5 nut, across flats (components/sources.md)
NUT_CORNERS = NUT_AF / math.cos(math.radians(30))   # 5.77: a pocket this wide lets the nut spin


def load_results(path=None):
    p = Path(path or ROOT / "data" / "fit_test_results.yaml")
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _rows(results, key):
    """{nominal_mm: result} for one feature row, ignoring blanks; validates the words."""
    out = {}
    for k, v in (results.get(key) or {}).items():
        if v is None or v == "":
            continue
        if v not in RESULTS:
            raise ValueError(f"fit_test_results.{key}.{k}: '{v}' is not one of {RESULTS}")
        out[float(k)] = v
    return dict(sorted(out.items()))


def _smallest(rows, ok):
    vals = [k for k, v in rows.items() if v in ok]
    return min(vals) if vals else None


def rules(cfg, source=None, results=None):
    source = source or cfg.get("clearance_source", "generic")
    g = dict(cfg["clearances"]["generic"])
    out = {"source": source, "values": dict(g), "notes": [], "fallbacks": []}
    if source == "generic":
        out["notes"].append("generic clearances from the Formlabs design guide (see components/sources.md)")
        return out
    if source != "measured":
        raise ValueError(f"clearance_source must be generic or measured, got {source}")

    res = load_results() if results is None else results
    v = out["values"]
    meta = res.get("meta") or {}
    pr = cfg.get("print", {})
    if meta.get("resin") and meta["resin"] != pr.get("material"):
        out["notes"].append(f"WARNING: fit test was printed in {meta['resin']}, current material is {pr.get('material')}")
    if meta.get("layer_thickness_mm") and float(meta["layer_thickness_mm"]) != float(pr.get("layer_thickness_mm", 0)):
        out["notes"].append(f"WARNING: fit test layer {meta['layer_thickness_mm']} mm, current layer "
                            f"{pr.get('layer_thickness_mm')} mm: measured clearances may not apply")

    def fallback(name, why):
        out["fallbacks"].append(name)
        out["notes"].append(f"MEASURED MODE FALLBACK: {name} uses generic {g[name]} ({why})")

    # Lid lip / board gap: the smallest groove the 2.00 tongue slides into freely.
    grooves = _rows(res, "grooves")
    w = _smallest(grooves, {"free"}) or _smallest(grooves, {"snug"})
    if w is None:
        fallback("part_gap", "no free/snug groove recorded")
        fallback("lid_groove_extra", "no free/snug groove recorded")
    else:
        per_side = round((w - KEY_TONGUE) / 2, 3)
        v["part_gap"] = max(per_side, 0.05)
        v["lid_groove_extra"] = round(w - KEY_TONGUE, 3)
        out["notes"].append(f"groove {w} fits the 2.00 tongue ({grooves[w]}): {per_side} mm per side for lid lip and board gap")
        v["port_cutout"] = max(v["part_gap"], 0.3)
        out["notes"].append(f"port cutout clearance {v['port_cutout']} mm per side (part gap, min 0.3 for position error)")

    # Peg holes: smallest free hole for the 5.00 peg.
    pegs = _rows(res, "peg_holes")
    d = _smallest(pegs, {"free"})
    if d is None:
        fallback("peg_hole_extra", "no free peg hole recorded")
    else:
        v["peg_hole_extra"] = round(d - KEY_PEG, 3)
        out["notes"].append(f"peg hole {d} is free on the 5.00 peg: hole = peg + {v['peg_hole_extra']}")

    # M2.5 clearance hole: smallest hole a screw drops through.
    holes = _rows(res, "m25_holes")
    d = _smallest(holes, {"free"})
    if d is None:
        fallback("m25_clearance_hole", "no free M2.5 hole recorded")
    else:
        v["m25_clearance_hole"] = d
        out["notes"].append(f"M2.5 clearance hole {d} (screw drops through)")

    # Nut pocket: smallest pocket that holds the nut (snug), else press; never one wide enough to spin.
    nuts = _rows(res, "nut_pockets")
    af = _smallest(nuts, {"snug"}) or _smallest(nuts, {"press"})
    if af is None:
        fallback("nut_pocket_af", "no snug/press nut pocket recorded")
    else:
        v["nut_pocket_af"] = af
        out["notes"].append(f"nut pocket {af} AF holds the M2.5 nut ({nuts[af]})")

    # Screw posts: does a self-tapping M2.5 crack a post of this OD?
    posts = _rows(res, "screw_posts")
    if not posts:
        fallback("fastener_style", "no screw post results recorded")
        fallback("min_post_od_for_pilot", "no screw post results recorded")
    else:
        ok = [od for od, r in posts.items() if r in ("snug", "press")]
        cracked = [od for od, r in posts.items() if r == "cracked"]
        if ok:
            v["min_post_od_for_pilot"] = min(ok)
            v["fastener_style"] = "pilot"
            msg = f"pilot-hole posts hold without cracking from OD {min(ok)} mm"
            if cracked:
                msg += f"; OD {', '.join(str(c) for c in cracked)} cracked"
            out["notes"].append(msg)
        else:
            v["fastener_style"] = "through_nut"
            v["min_post_od_for_pilot"] = None
            out["notes"].append(f"pilot holes cracked posts of OD {', '.join(str(c) for c in cracked)}: "
                                f"use through-hole + nut")

    # Thin walls: thinnest wall that printed whole and stiff.
    walls = _rows(res, "thin_walls")
    t = _smallest(walls, {"free"})
    if t is None:
        fallback("min_wall", "no thin wall results recorded")
    else:
        v["min_wall"] = t
        out["notes"].append(f"thinnest good wall {t} mm")

    if len(out["fallbacks"]) >= 8:
        out["notes"].insert(0, "MEASURED MODE: data/fit_test_results.yaml is empty, so every value is GENERIC")
    return out


def check(spec, rules):
    """Fit check (owned by the Fit agent): does the design follow the clearance/fastener rules?

    Returns {"name": "fit", "passed", "failures", "metrics"}. Every failure has the rule's number.
    """
    v = rules["values"]
    c = spec["clearances"]
    fails = []

    def need(what, have, want, why, key=None):
        if have < want - 1e-6:
            fails.append({"code": "clearance", "msg": f"{what} {have} < {want} ({why})", "amount_mm": round(want - have, 3),
                          "target": key or what, "need": want})

    src = rules["source"]
    need("board gap", c["board_gap"], v["part_gap"], f"{src} part gap", "/clearances/board_gap")
    for side, g in spec["box"]["gap"].items():
        need(f"{side} wall gap", g, v["part_gap"], f"{src} part gap", f"/box/gap/{side}")
    need("port clearance", c["port"], v["port_cutout"], f"{src} port clearance", "/clearances/port")
    need("lid fit per side", c["lid_fit"], round(v["lid_groove_extra"] / 2, 3), f"{src} lid groove", "/clearances/lid_fit")
    need("M2.5 clearance hole", c["screw_hole"], v["m25_clearance_hole"], f"{src} M2.5 hole", "/clearances/screw_hole")

    styles = {spec["fasteners"]["board"], spec["fasteners"]["lid"]}
    if "through_nut" in styles:
        if c["nut_pocket_af"] >= NUT_CORNERS:
            fails.append({"code": "nut", "msg": f"nut pocket {c['nut_pocket_af']} AF >= nut corner width "
                                                f"{NUT_CORNERS:.2f}: the nut spins", "amount_mm": round(c["nut_pocket_af"] - NUT_CORNERS + 0.05, 3),
                          "target": "/clearances/nut_pocket_af"})
        need("nut pocket AF", c["nut_pocket_af"], v["nut_pocket_af"] if v["nut_pocket_af"] < NUT_CORNERS else NUT_AF,
             f"{src} nut pocket", "/clearances/nut_pocket_af")
    if "pilot" in styles:
        posts = []
        if spec["fasteners"]["board"] == "pilot":
            posts.append(("standoff", spec["standoffs"]["diameter"]))
        if spec["fasteners"]["lid"] == "pilot":
            posts.append(("lid boss", spec["lid_screws"]["boss_diameter"]))
        min_od = v.get("min_post_od_for_pilot")
        for name, od in posts:
            if v["fastener_style"] == "through_nut" or min_od is None:
                fails.append({"code": "fastener", "msg": f"{name}: pilot-hole posts cracked in the fit test; "
                                                         f"use through_nut", "amount_mm": 1.0, "target": name})
            else:
                need(f"{name} OD for a pilot hole", od, min_od, f"{src}: smallest post that did not crack", name)
    need("pilot hole", c["pilot_hole"], v["pilot_hole"], f"{src} pilot hole", "/clearances/pilot_hole")

    return {"name": "fit", "passed": not fails, "failures": fails,
            "metrics": {"rule_source": src, "fallbacks": rules["fallbacks"]}}
