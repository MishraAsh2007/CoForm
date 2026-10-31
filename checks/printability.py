"""Printability check (owned by the Manufacturing agent): local, no PreForm needed.

check(spec, cfg, rules, built=None, mesh_paths=None) -> {"passed", "failures", "metrics"}

Spec level (exact numbers, readable messages):
  walls/floor/lid/lip thicker than min_wall; material left between neighbouring openings;
  wall left around holes in standoffs, bosses and tabs; vent ribs between slots;
  holes and slots above min_hole; nut pockets that would break out of the part.
Mesh level (safety net for anything the spec rules miss):
  ray-cast local thickness on the exported STL; the 1st percentile must be >= min_supported_wall.
"""
import math

import numpy as np

from cad.components import SIDES
from cad.enclosure import build


def _fail(out, code, msg, amount):
    out.append({"code": code, "msg": msg, "amount_mm": round(float(amount), 2)})


def _need(fails, what, value, limit, code="wall"):
    if value < limit - 1e-6:
        _fail(fails, code, f"{what} {value:.2f} mm < {limit} mm", limit - value)


def check(spec, cfg, rules, built=None, mesh_paths=None):
    v = rules["values"]
    min_wall, min_hole = v["min_wall"], v["min_hole"]
    built = built or build(spec)
    meta, comp = built["meta"], built["comp"]
    c = spec["clearances"]
    fails, metrics = [], {"min_wall_rule_mm": min_wall, "rule_source": rules["source"]}

    w = spec["walls"]
    for k in ("wall", "floor", "lid"):
        _need(fails, f"{k} thickness", w[k], min_wall)
    if spec["lid"]["lip_height"] > 0:
        _need(fails, "lid lip width", spec["lid"]["lip_width"], min_wall)

    # material between openings on the same wall, and to the wall ends
    ob = meta["outer_box"]
    for side, (axis, _) in SIDES.items():
        ops = sorted([o for o in meta["openings"] if o["side"] == side],
                     key=lambda o: o["box"][2] if axis == 0 else o["box"][0])
        lo_i, hi_i = (2, 3) if axis == 0 else (0, 1)
        span = (ob[2], ob[3]) if axis == 0 else (ob[0], ob[1])
        prev_hi, prev_name = span[0], "wall end"
        for o in ops:
            lo, hi = o["box"][lo_i], o["box"][hi_i]
            gap = lo - prev_hi
            # overlapping openings (gap < 0) just merge into one; touching the wall end cuts the corner
            if (0 <= gap < min_wall) or (gap < min_wall and prev_name == "wall end"):
                _fail(fails, "sliver", f"{side} wall: {gap:.2f} mm of material between {prev_name} and {o['name']}",
                      min_wall - gap)
            prev_hi, prev_name = max(prev_hi, hi), o["name"]
        end_gap = span[1] - prev_hi
        if ops and end_gap < min_wall:
            _fail(fails, "sliver", f"{side} wall: {end_gap:.2f} mm between {prev_name} and the wall end", min_wall - end_gap)

    # holes in posts, bosses, tabs
    board_hole = c["pilot_hole"] if spec["fasteners"]["board"] == "pilot" else c["screw_hole"]
    lid_hole = c["pilot_hole"] if spec["fasteners"]["lid"] == "pilot" else c["screw_hole"]
    _need(fails, "standoff wall around its hole", (spec["standoffs"]["diameter"] - board_hole) / 2, min_wall)
    _need(fails, "lid boss wall around its hole", (spec["lid_screws"]["boss_diameter"] - lid_hole) / 2, min_wall)
    for i, t in enumerate(spec["mount_tabs"]):
        _need(fails, f"tab {i + 1} material beside its hole", (t["width"] - t["hole"]) / 2, min_wall)
        _need(fails, f"tab {i + 1} thickness", t["thickness"], min_wall)

    # vents
    for i, vent in enumerate(spec["vents"]):
        _need(fails, f"vent {i + 1} rib between slots", vent["pitch"] - vent["slot_width"], min_wall, "vent")
        _need(fails, f"vent {i + 1} slot width", vent["slot_width"], min_hole, "small_feature")

    for name, d in (("pilot hole", c["pilot_hole"]), ("screw hole", c["screw_hole"])):
        _need(fails, name, d, min_hole, "small_feature")
    for i, t in enumerate(spec["mount_tabs"]):
        _need(fails, f"tab {i + 1} hole", t["hole"], min_hole, "small_feature")

    # nut pockets must stay inside the part they are cut into
    corner_r = c["nut_pocket_af"] / math.cos(math.radians(30)) / 2
    if spec["fasteners"]["lid"] == "through_nut":
        for i, (x, y) in enumerate(spec["lid_screws"]["positions"]):
            # pocket is cut from the bottom of the base: it must sit inside the outer box with min_wall left
            margin = min(x - ob[0], ob[1] - x, y - ob[2], ob[3] - y) - corner_r
            boss_margin = spec["lid_screws"]["boss_diameter"] / 2 - corner_r
            m = max(margin, boss_margin)
            if m < min_wall:
                _fail(fails, "nut_pocket", f"lid screw {i + 1}: nut pocket leaves {m:.2f} mm wall (breaks out)", min_wall - m)
    if spec["fasteners"]["board"] == "through_nut":
        depth = spec["walls"]["floor"] + spec["standoffs"]["height"]
        _need(fails, "material above board nut pocket", depth - c["nut_pocket_depth"], min_wall, "nut_pocket")

    # mesh-level safety net
    if mesh_paths:
        thin = {}
        for name, p in mesh_paths.items():
            t1 = mesh_thickness_p1(p)
            thin[name] = t1
            if t1 is not None and t1 < v["min_supported_wall"]:
                _fail(fails, "mesh_thin", f"{name}.stl: 1% of surface is thinner than {t1:.2f} mm "
                                          f"(min supported wall {v['min_supported_wall']})", v["min_supported_wall"] - t1)
        metrics["mesh_thickness_p1_mm"] = thin

    return {"name": "printability", "passed": not fails, "failures": fails, "metrics": metrics}


def mesh_thickness_p1(stl_path, samples=6000, seed=0):
    """1st percentile of local thickness: from surface samples, ray inward to the opposite surface."""
    import trimesh
    mesh = trimesh.load(str(stl_path))
    pts, face_idx = trimesh.sample.sample_surface(mesh, samples, seed=seed)
    normals = mesh.face_normals[face_idx]
    origins = pts - normals * 1e-3
    loc, ray_idx, _ = mesh.ray.intersects_location(origins, -normals, multiple_hits=False)
    if len(ray_idx) == 0:
        return None
    d = np.linalg.norm(loc - origins[ray_idx], axis=1)
    d = d[d > 0.02]
    return round(float(np.percentile(d, 1)), 3) if len(d) else None
