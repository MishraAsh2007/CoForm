"""One quality score per design (weights in config.yaml `quality:`). Computed only from logged data:
the spec, the exported STL meshes, the check metrics and (when it ran) real PreForm results."""
import math

import trimesh


def components(spec, ev, rules, cfg, brief, real_preform=None):
    q = cfg["quality"]
    vol, ext = 0.0, [0.0, 0.0, 0.0]
    for p in ev.get("paths", {}).values():
        m = trimesh.load(p)
        vol += abs(m.volume) / 1000
        ext = [max(a, b) for a, b in zip(ext, sorted(m.extents))]
    tilt = math.radians(q["tilt_deg"])
    height = ext[2] * math.sin(tilt) + ext[1] * math.cos(tilt)
    v = rules["values"]
    c = spec.get("clearances", {})
    clr = [c.get("board_gap", 0) - v["part_gap"], c.get("port", 0) - v["port_cutout"],
           2 * c.get("lid_fit", 0) - v["lid_groove_extra"]]
    clr_margin = max(0.0, min(min(clr), q["clearance_margin_cap_mm"])) if c else 0.0
    am = ev["results"].get("assembly", {}).get("metrics", {})
    chk = cfg["checks"]
    gaps = list((am.get("board_edge_gap_mm") or {}).values())
    room = [g - chk["min_part_gap"] for g in gaps]
    if am.get("lid_gap_above_tallest_part_mm") is not None:
        room.append(am["lid_gap_above_tallest_part_mm"] - chk["lid_min_gap"])
    asm_margin = max(0.0, min(min(room), q["assembly_margin_cap_mm"])) if room else 0.0
    feats = (sum(p["enabled"] for p in spec.get("ports", [])) + sum(s["enabled"] for s in spec.get("cable_slots", []))
             + len(spec.get("mount_tabs", [])) + len(spec.get("vents", []))
             + len(spec.get("drain_holes", {}).get("positions", [])) + len(spec.get("ribs", [])))
    needed = (len(brief.get("required_ports", [])) + len(brief.get("required_cables", []))
              + brief.get("mount_tabs_min", 0) + (1 if brief.get("vent_over_fan") else 0))
    warnings = None
    if real_preform is not None:
        warnings = len(real_preform.get("failures", []))
    return {"volume_ml": round(vol, 2), "print_height_mm": round(height, 1), "preform_warnings": warnings,
            "clearance_margin_mm": round(clr_margin, 3), "assembly_margin_mm": round(asm_margin, 3),
            "extra_features": max(0, feats - needed)}


def score(comp, cfg):
    q = cfg["quality"]
    s = (q["base"] - q["w_volume"] * comp["volume_ml"] - q["w_height"] * comp["print_height_mm"]
         - q["w_warning"] * (comp["preform_warnings"] or 0)
         + q["w_clearance"] * comp["clearance_margin_mm"] + q["w_assembly"] * comp["assembly_margin_mm"]
         - q["w_feature"] * comp["extra_features"])
    return round(s, 2)


def quality(spec, ev, rules, cfg, brief, real_preform=None):
    comp = components(spec, ev, rules, cfg, brief, real_preform)
    return {"score": score(comp, cfg), **comp}
