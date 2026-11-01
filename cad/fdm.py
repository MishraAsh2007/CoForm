r"""FDM variant of an existing design: same spec, FDM rules, no agent rerun.

  .venv\Scripts\python.exe -m cad.fdm <run_dir> <out_dir>

1. fdm_spec(): apply the FDM profile (config.yaml processes.fdm) to the spec deterministically:
   clearances from the FDM rules, wall gaps >= FDM part gap, walls/lip >= 3 perimeters, enough wall
   around every screw hole, hole compensation on the robot-tab holes.
2. Build it and run the LOCAL assembly, printability and fit checks with the FDM rules and minimums.
   PreForm is never run: it is an SLA tool and says nothing about FDM.
3. List bridges (closed-top openings wider than max_bridge_mm) that need support when the base prints open side up.
4. Export pre-rotated STLs: base open side up (as built), lid flipped so its flat top face is on the bed.
Writes <out_dir>/fdm_checks.json, fdm_spec.json, base_fdm.stl, lid_fdm.stl.
"""
import copy
import json
import math
import sys
from pathlib import Path

import trimesh

from cad.common import export_stl, load_config
from cad.enclosure import build
from cad.spec import clearances_from_rules, validate
from checks import assembly, fit_rules, printability


def _repair_standoffs(s, v, min_gap, setv):
    """Shrink the standoffs (never below hole + 2 x min wall) so each keeps the FDM minimum gap,
    sideways, from every part keep-out box that reaches down to standoff height."""
    from cad.components import load_bundle
    comp = load_bundle(s["bundle"])
    hole = s["clearances"]["pilot_hole"] if s["fasteners"]["board"] == "pilot" else s["clearances"]["screw_hole"]
    d = s["standoffs"]["diameter"]
    for x, y in comp["mounting_holes"]["positions"]:
        for k in comp["keepouts"]:
            if k["name"] == "pcb":
                continue
            x0, x1, y0, y1, z0, _ = k["box"]
            if z0 - min_gap >= 0.0:          # standoff tops are at z 0 (PCB bottom)
                continue
            dx = max(x0 - x, 0, x - x1)
            dy = max(y0 - y, 0, y - y1)
            dist = math.hypot(dx, dy)
            if dist > 0:
                d = min(d, math.floor((dist - min_gap) * 2 * 10) / 10)
    d = max(d, round(hole + 2 * v["min_wall"], 2))
    setv("/standoffs/diameter", s["standoffs"], "diameter", d)


def _repair_slivers(s, v, floor_clearance, setv):
    """Neighbouring wall openings closer than min_wall: narrow both (port `extra` < 0) so min_wall of material
    is left, but never below the SLA generic port clearance per side (floor_clearance)."""
    from cad.components import SIDES, load_bundle
    from cad.enclosure import openings
    comp = load_bundle(s["bundle"])
    ports = {p["name"]: p for p in s["ports"]}
    ops = openings(s, comp)
    for side, (axis, _) in SIDES.items():
        lo_i, hi_i = (2, 3) if axis == 0 else (0, 1)
        row = sorted([o for o in ops if o["side"] == side and o["kind"] == "port"], key=lambda o: o["box"][lo_i])
        for a, b in zip(row, row[1:]):
            gap = b["box"][lo_i] - a["box"][hi_i]
            if 0 <= gap < v["min_wall"]:
                cut = math.ceil((v["min_wall"] - gap) / 2 * 100) / 100
                for o in (a, b):
                    p = ports[o["name"]]
                    new = round(p.get("extra", 0.0) - cut, 2)
                    if s["clearances"]["port"] + new >= floor_clearance - 1e-6:
                        setv(f"/ports/{o['name']}/extra", p, "extra", new)


def fdm_spec(spec, rules, prof, min_gap=0.5, sla_port=0.5):
    s = copy.deepcopy(spec)
    v = rules["values"]
    changes = []

    def setv(path, obj, key, new):
        if obj[key] != new:
            changes.append({"path": path, "from": obj[key], "to": new})
            obj[key] = new

    for k, new in clearances_from_rules(rules).items():
        setv(f"/clearances/{k}", s["clearances"], k, new)
    for side in s["box"]["gap"]:
        setv(f"/box/gap/{side}", s["box"]["gap"], side, max(s["box"]["gap"][side], v["part_gap"]))
    for k in ("wall", "floor", "lid"):
        setv(f"/walls/{k}", s["walls"], k, max(s["walls"][k], v["min_wall"]))
    if s["lid"]["lip_height"] > 0:
        setv("/lid/lip_width", s["lid"], "lip_width", max(s["lid"]["lip_width"], v["min_wall"]))
    if s["walls"]["outer_fillet"] >= s["walls"]["wall"]:
        setv("/walls/outer_fillet", s["walls"], "outer_fillet", round(s["walls"]["wall"] - 0.2, 2))
    hole = s["clearances"]["pilot_hole"] if s["fasteners"]["board"] == "pilot" else s["clearances"]["screw_hole"]
    # standoff OD capped by the 6.0 mm pad on the board (components/rpi5.yaml)
    setv("/standoffs/diameter", s["standoffs"], "diameter",
         round(min(6.0, max(s["standoffs"]["diameter"], hole + 2 * v["min_wall"])), 2))
    hole = s["clearances"]["pilot_hole"] if s["fasteners"]["lid"] == "pilot" else s["clearances"]["screw_hole"]
    setv("/lid_screws/boss_diameter", s["lid_screws"], "boss_diameter",
         round(max(s["lid_screws"]["boss_diameter"], hole + 2 * v["min_wall"]), 2))
    comp_mm = prof["hole_compensation_mm"]
    for i, t in enumerate(s["mount_tabs"]):
        setv(f"/mount_tabs/{i}/hole", t, "hole", round(t["hole"] + comp_mm, 2))
    _repair_standoffs(s, v, min_gap, setv)
    _repair_slivers(s, v, sla_port, setv)
    s["clearance_source"] = "fdm"
    s["name"] = spec.get("name", "design") + "__fdm"
    return validate(s), changes


def bridges(built, prof):
    """Closed-top wall openings: their top edge is a bridge when the base prints open side up."""
    out = []
    for o in built["meta"]["openings"]:
        if not o["open_top"]:
            w = o["size"][0]
            out.append({"name": o["name"], "side": o["side"], "span_mm": round(w, 2),
                        "needs_support": w > prof["max_bridge_mm"]})
    return out


def rotated_lid(lid_stl):
    m = trimesh.load(str(lid_stl))
    m.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
    m.apply_translation(-m.bounds[0])
    return m


def make(run_dir, out_dir, cfg=None):
    cfg = cfg or load_config()
    prof = cfg["processes"]["fdm"]
    rules = fit_rules.rules(cfg, process="fdm")
    ccfg = fit_rules.process_cfg(cfg, "fdm")
    brief_name = json.loads((Path(run_dir) / "summary.json").read_text(encoding="utf-8"))["brief"]
    import yaml
    from cad.common import ROOT
    brief = yaml.safe_load((ROOT / "benchmark" / "briefs" / f"{brief_name}.yaml").read_text(encoding="utf-8"))
    spec = json.loads((Path(run_dir) / "final_spec.json").read_text(encoding="utf-8"))
    s, changes = fdm_spec(spec, rules, prof, min_gap=ccfg["checks"]["min_part_gap"],
                          sla_port=cfg["clearances"]["generic"]["port_cutout"])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    built = build(s)
    tmp = out / "_mesh"
    paths = {"base": export_stl(built["base"], tmp / "base.stl", cfg), "lid": export_stl(built["lid"], tmp / "lid.stl", cfg)}
    res = {"assembly": assembly.check(s, brief, ccfg, built),
           "printability": printability.check(s, ccfg, rules, built, mesh_paths=paths),
           "fit": fit_rules.check(s, rules)}
    # pre-rotated exports
    base = trimesh.load(str(paths["base"]))
    base.apply_translation(-base.bounds[0])
    base.export(out / "base_fdm.stl")
    lid = rotated_lid(paths["lid"])
    lid.export(out / "lid_fdm.stl")
    report = {"process": "fdm", "profile": prof, "rules": rules, "checks_cfg": ccfg["checks"],
              "source_run": str(run_dir), "spec_changes": changes,
              "results": {k: {"passed": r["passed"], "failures": r["failures"], "metrics": r["metrics"]} for k, r in res.items()},
              "bridges": bridges(built, prof),
              "parts": {"base_fdm.stl": {"extents_mm": [round(x, 2) for x in base.extents], "volume_cm3": round(base.volume / 1000, 2),
                                         "orientation": "open side up (floor on the bed)"},
                        "lid_fdm.stl": {"extents_mm": [round(x, 2) for x in lid.extents], "volume_cm3": round(lid.volume / 1000, 2),
                                        "orientation": "flat top face on the bed (lip and screw ears up)"}},
              "preform": "NOT RUN: PreForm is an SLA tool; this variant is not checked by PreForm"}
    (out / "fdm_spec.json").write_text(json.dumps(s, indent=1, sort_keys=True), encoding="utf-8")
    (out / "fdm_checks.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


if __name__ == "__main__":
    r = make(sys.argv[1], sys.argv[2])
    for k, v in r["results"].items():
        print(k, "PASS" if v["passed"] else "FAIL", [f["msg"] for f in v["failures"]], v["metrics"])
    print("changes:", r["spec_changes"])
    print("bridges:", r["bridges"])
    print("parts:", r["parts"])
