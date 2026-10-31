r"""Enclosure generator: feature spec + component data -> base and lid solids.

build(spec) returns {"base", "lid" (CadQuery Workplanes), "meta" (numbers the checks use)}.
export(spec, out_dir) writes base.stl, lid.stl, spec.json and pictures.

Run:  .venv\Scripts\python.exe -m cad.enclosure [generic|measured]
"""
import json
import math
import sys
from pathlib import Path

import cadquery as cq

from cad.common import ROOT, export_stl, load_config
from cad.components import SIDES, board_solid, box_solid, load_bundle, port_opening
from cad.render import render


def levels(spec):
    zf = -spec["standoffs"]["height"]
    return {"floor_top": zf, "floor_bot": zf - spec["walls"]["floor"], "top": spec["box"]["inner_top"],
            "lid_top": spec["box"]["inner_top"] + spec["walls"]["lid"]}


def inner_box(spec, comp):
    g, b = spec["box"]["gap"], comp["board"]
    return [-g["-x"], b["length"] + g["+x"], -g["-y"], b["width"] + g["+y"]]


def outer_box(spec, comp):
    x0, x1, y0, y1 = inner_box(spec, comp)
    w = spec["walls"]["wall"]
    return [x0 - w, x1 + w, y0 - w, y1 + w]


def wall_faces(spec, comp):
    """{side: (inner face coordinate, outer face coordinate)} along that side's axis."""
    i, o = inner_box(spec, comp), outer_box(spec, comp)
    return {"-x": (i[0], o[0]), "+x": (i[1], o[1]), "-y": (i[2], o[2]), "+y": (i[3], o[3])}


def _rounded_block(rect, z0, z1, r):
    x0, x1, y0, y1 = rect
    wp = cq.Workplane("XY").workplane(offset=z0).center((x0 + x1) / 2, (y0 + y1) / 2).rect(x1 - x0, y1 - y0)
    s = wp.extrude(z1 - z0)
    return s.edges("|Z").fillet(r) if r > 0 else s


def _cyl(x, y, d, z0, z1):
    return cq.Workplane("XY").workplane(offset=z0).center(x, y).circle(d / 2).extrude(z1 - z0)


def _hex(x, y, af, z0, z1):
    return cq.Workplane("XY").workplane(offset=z0).center(x, y).polygon(6, af / math.cos(math.radians(30))).extrude(z1 - z0)


def wall_cut_box(side, faces, centre, size, z_lo, z_hi):
    """Box through one wall only, centred on (u along the edge) with width size[0]."""
    axis, sign = SIDES[side]
    inner, outer = faces[side]
    a0, a1 = sorted([inner - sign * 0.3, outer + sign * 1.0])
    u = centre[0]
    w = size[0]
    if axis == 0:
        return [a0, a1, u - w / 2, u + w / 2, z_lo, z_hi]
    return [u - w / 2, u + w / 2, a0, a1, z_lo, z_hi]


def openings(spec, comp):
    """Every wall opening: ports and enabled cable slots, with their boxes (board frame)."""
    faces = wall_faces(spec, comp)
    lv = levels(spec)
    clr = spec["clearances"]["port"]
    ports = {p["name"]: p for p in comp["ports"]}
    cables = {a["name"]: a for a in comp["cable_access"]}
    out = []
    for item, kind, src in ([(p, "port", ports) for p in spec["ports"]] +
                            [(s, "cable", cables) for s in spec["cable_slots"]]):
        if not item["enabled"]:
            continue
        c = src[item["name"]]
        extra = item.get("extra", 0.0)
        if kind == "port":
            w, h = port_opening(c, faces[c["side"]][1], clr)
        else:
            w, h = c["size"][0] + 2 * clr, c["size"][1] + 2 * clr
        w, h = w + 2 * extra, h + 2 * extra
        z_lo = c["centre"][1] - h / 2
        z_hi = lv["top"] + 1 if item.get("open_top") else c["centre"][1] + h / 2
        out.append({"name": item["name"], "kind": kind, "side": c["side"], "size": [round(w, 3), round(h, 3)],
                    "open_top": bool(item.get("open_top")), "top": round(c["centre"][1] + h / 2, 3),
                    "box": wall_cut_box(c["side"], faces, c["centre"], [w, h], z_lo, z_hi)})
    return out


def screws(spec, comp):
    """Every screw: where its head sits and which way it is driven (for screwdriver access)."""
    lv = levels(spec)
    out = []
    for i, (x, y) in enumerate(comp["mounting_holes"]["positions"]):
        out.append({"name": f"board_screw_{i + 1}", "x": x, "y": y, "head_z": comp["board"]["thickness"],
                    "drive": "down", "style": spec["fasteners"]["board"],
                    "length_needed": round(comp["board"]["thickness"] + spec["standoffs"]["height"]
                                           + (spec["walls"]["floor"] if spec["fasteners"]["board"] == "through_nut" else -0.5), 2)})
    for i, (x, y) in enumerate(spec["lid_screws"]["positions"]):
        out.append({"name": f"lid_screw_{i + 1}", "x": x, "y": y, "head_z": lv["lid_top"], "drive": "down",
                    "style": spec["fasteners"]["lid"],
                    "length_needed": round(spec["walls"]["lid"] + (lv["top"] - lv["floor_bot"] if spec["fasteners"]["lid"] == "through_nut"
                                                                    else 6.0), 2)})
    return out


def build(spec, comp=None):
    comp = comp or load_bundle(spec["bundle"])
    lv = levels(spec)
    w = spec["walls"]
    c = spec["clearances"]
    ib, ob = inner_box(spec, comp), outer_box(spec, comp)
    faces = wall_faces(spec, comp)

    # ---------- base ----------
    base = _rounded_block(ob, lv["floor_bot"], lv["top"], w["outer_fillet"])
    base = base.cut(cq.Workplane("XY").add(box_solid([*ib, lv["floor_top"], lv["top"] + 1])))

    for r in spec["ribs"]:
        if r["axis"] == "x":
            bx = [ib[0], ib[1], r["position"] - r["width"] / 2, r["position"] + r["width"] / 2]
        else:
            bx = [r["position"] - r["width"] / 2, r["position"] + r["width"] / 2, ib[2], ib[3]]
        base = base.union(cq.Workplane("XY").add(box_solid([*bx, lv["floor_top"] - 0.01, lv["floor_top"] + r["height"]])))

    st = spec["standoffs"]
    for x, y in comp["mounting_holes"]["positions"]:
        base = base.union(_cyl(x, y, st["diameter"], lv["floor_top"] - 0.01, 0.0))
    for x, y in spec["lid_screws"]["positions"]:
        base = base.union(_cyl(x, y, spec["lid_screws"]["boss_diameter"], lv["floor_bot"], lv["top"]))

    for t in spec["mount_tabs"]:
        axis, sign = SIDES[t["side"]]
        outer = faces[t["side"]][1]
        a0, a1 = sorted([outer - sign * 0.5, outer + sign * t["length"]])
        u0, u1 = t["offset"] - t["width"] / 2, t["offset"] + t["width"] / 2
        bx = [a0, a1, u0, u1] if axis == 0 else [u0, u1, a0, a1]
        tab = cq.Workplane("XY").add(box_solid([*bx, lv["floor_bot"], lv["floor_bot"] + t["thickness"]]))
        hc = outer + sign * t["length"] / 2
        hx, hy = (hc, t["offset"]) if axis == 0 else (t["offset"], hc)
        base = base.union(tab).cut(_cyl(hx, hy, t["hole"], lv["floor_bot"] - 1, lv["floor_bot"] + t["thickness"] + 1))

    # holes after all unions
    for x, y in comp["mounting_holes"]["positions"]:
        if spec["fasteners"]["board"] == "pilot":
            base = base.cut(_cyl(x, y, c["pilot_hole"], lv["floor_bot"] - 1, 1.0))
        else:
            base = base.cut(_cyl(x, y, c["screw_hole"], lv["floor_bot"] - 1, 1.0))
            base = base.cut(_hex(x, y, c["nut_pocket_af"], lv["floor_bot"] - 1, lv["floor_bot"] + c["nut_pocket_depth"]))
    for x, y in spec["lid_screws"]["positions"]:
        if spec["fasteners"]["lid"] == "pilot":
            base = base.cut(_cyl(x, y, c["pilot_hole"], lv["floor_bot"] - 1, lv["top"] + 1))
        else:
            base = base.cut(_cyl(x, y, c["screw_hole"], lv["floor_bot"] - 1, lv["top"] + 1))
            base = base.cut(_hex(x, y, c["nut_pocket_af"], lv["floor_bot"] - 1, lv["floor_bot"] + c["nut_pocket_depth"]))

    ops = openings(spec, comp)
    for o in ops:
        base = base.cut(cq.Workplane("XY").add(box_solid(o["box"])))

    for v in spec["vents"]:
        if v["face"] != "lid":
            base = base.cut(_side_vent(v, faces))

    # ---------- lid ----------
    lid = _rounded_block(ob, lv["top"], lv["lid_top"], w["outer_fillet"])
    for x, y in spec["lid_screws"]["positions"]:   # ears matching the base bosses
        lid = lid.union(_cyl(x, y, spec["lid_screws"]["boss_diameter"], lv["top"], lv["lid_top"]))
    lip = spec["lid"]
    if lip["lip_height"] > 0:
        f = c["lid_fit"]
        outer_ring = [ib[0] + f, ib[1] - f, ib[2] + f, ib[3] - f]
        inner_ring = [outer_ring[0] + lip["lip_width"], outer_ring[1] - lip["lip_width"],
                      outer_ring[2] + lip["lip_width"], outer_ring[3] - lip["lip_width"]]
        ring = (cq.Workplane("XY").add(box_solid([*outer_ring, lv["top"] - lip["lip_height"], lv["top"] + 0.01]))
                .cut(cq.Workplane("XY").add(box_solid([*inner_ring, lv["top"] - lip["lip_height"] - 1, lv["top"] + 1]))))
        # relief: the lip steps around every part and boss with the board gap
        gap = c["board_gap"]
        for k in comp["keepouts"]:
            x0, x1, y0, y1, z0, z1 = k["box"]
            ring = ring.cut(cq.Workplane("XY").add(box_solid([x0 - gap, x1 + gap, y0 - gap, y1 + gap, z0 - gap, z1 + gap])))
        for x, y in spec["lid_screws"]["positions"]:
            ring = ring.cut(_cyl(x, y, spec["lid_screws"]["boss_diameter"] + 2 * f, lv["top"] - lip["lip_height"] - 1, lv["top"] + 1))
        lid = lid.union(ring)
    if lip.get("notch_fillers"):
        for o in ops:
            if o["open_top"] and o["top"] < lv["top"] - 0.5:
                bx = list(o["box"])
                axis, sign = SIDES[o["side"]]
                inner, outer = faces[o["side"]]
                a0, a1 = sorted([inner, outer])
                if axis == 0:
                    bx[0], bx[1] = a0, a1
                    bx[2], bx[3] = bx[2] + c["lid_fit"], bx[3] - c["lid_fit"]
                else:
                    bx[2], bx[3] = a0, a1
                    bx[0], bx[1] = bx[0] + c["lid_fit"], bx[1] - c["lid_fit"]
                bx[4], bx[5] = o["top"], lv["top"] + 0.01
                lid = lid.union(cq.Workplane("XY").add(box_solid(bx)))
    for x, y in spec["lid_screws"]["positions"]:
        lid = lid.cut(_cyl(x, y, c["screw_hole"], lv["top"] - 1, lv["lid_top"] + 1))
    for v in spec["vents"]:
        if v["face"] == "lid":
            lid = lid.cut(_lid_vent(v, lv))

    meta = {"levels": lv, "inner_box": ib, "outer_box": ob, "wall_faces": faces, "openings": ops,
            "screws": screws(spec, comp)}
    return {"base": base, "lid": lid, "meta": meta, "comp": comp}


def _slots(region, slot_w, pitch, along):
    """Slot rectangles (u0,u1,v0,v1) filling region (cu, cv, w, h); slots long along u or v."""
    cu, cv, w, h = region
    out = []
    if along == "u":
        n = max(1, int((h + pitch - slot_w) // pitch))
        start = cv - (n - 1) * pitch / 2
        for i in range(n):
            v = start + i * pitch
            out.append((cu - w / 2, cu + w / 2, v - slot_w / 2, v + slot_w / 2))
    else:
        n = max(1, int((w + pitch - slot_w) // pitch))
        start = cu - (n - 1) * pitch / 2
        for i in range(n):
            u = start + i * pitch
            out.append((u - slot_w / 2, u + slot_w / 2, cv - h / 2, cv + h / 2))
    return out


def _lid_vent(v, lv):
    solids = [box_solid([u0, u1, v0, v1, lv["top"] - 1, lv["lid_top"] + 1])
              for u0, u1, v0, v1 in _slots((*v["centre"], *v["size"]), v["slot_width"], v["pitch"], v.get("along", "u"))]
    return cq.Workplane("XY").add(cq.Compound.makeCompound(solids))


def _side_vent(v, faces):
    axis, sign = SIDES[v["face"]]
    inner, outer = faces[v["face"]]
    a0, a1 = sorted([inner - sign * 0.3, outer + sign * 1.0])
    solids = []
    for u0, u1, z0, z1 in _slots((*v["centre"], *v["size"]), v["slot_width"], v["pitch"], v.get("along", "v")):
        bx = [a0, a1, u0, u1, z0, z1] if axis == 0 else [u0, u1, a0, a1, z0, z1]
        solids.append(box_solid(bx))
    return cq.Workplane("XY").add(cq.Compound.makeCompound(solids))


def export(spec, out_dir, cfg=None, pictures=True, tag=""):
    """Build, write base.stl / lid.stl / spec.json (+ pictures). Returns paths and meta."""
    cfg = cfg or load_config()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    built = build(spec)
    paths = {"base": export_stl(built["base"], out / "base.stl", cfg),
             "lid": export_stl(built["lid"], out / "lid.stl", cfg)}
    (out / "spec.json").write_text(json.dumps(spec, indent=1, sort_keys=True), encoding="utf-8")
    if pictures:
        paths.update(render_views(built, out, cfg, tag))
    return paths, built


def render_views(built, out, cfg, tag=""):
    comp = built["comp"]
    tmp = Path(out) / "_mesh"
    tmp.mkdir(exist_ok=True)
    base = export_stl(built["base"], tmp / "base.stl", cfg)
    lid = export_stl(built["lid"], tmp / "lid.stl", cfg)
    lid_up = export_stl(built["lid"].translate((0, 0, 25)), tmp / "lid_up.stl", cfg)
    brd = export_stl(board_solid(comp), tmp / "board.stl", cfg)
    parts = export_stl(cq.Compound.makeCompound([box_solid(k["box"]) for k in comp["keepouts"] if k["name"] != "pcb"]),
                       tmp / "parts.stl", cfg)
    t = f" {tag}" if tag else ""
    pics = {
        "assembled": render([(base, "#d9d9d9"), (lid, "#9ec5e8", 0.45), (brd, "#2e8b57"), (parts, "#4a90d9", 0.5)],
                            Path(out) / "assembled.png", title=f"assembled{t}"),
        "exploded": render([(base, "#d9d9d9"), (lid_up, "#9ec5e8"), (brd, "#2e8b57"), (parts, "#4a90d9", 0.6)],
                           Path(out) / "exploded.png", title=f"exploded{t}"),
        "base_top": render([(base, "#d9d9d9"), (brd, "#2e8b57", 0.35)], Path(out) / "base_top.png", view="top",
                           title=f"base from above{t}"),
        "lid_bottom": render([(lid, "#9ec5e8")], Path(out) / "lid_iso.png", title=f"lid{t}"),
    }
    return pics


def main(source="generic"):
    cfg = load_config()
    from cad.spec import default_spec, validate
    from checks.fit_rules import rules
    r = rules(cfg, source)
    for n in r["notes"]:
        print(n)
    spec = validate(default_spec(cfg["enclosure"]["default_bundle"], r, cfg))
    out = ROOT / cfg["enclosure"]["out_dir"] / f"v0_{source}"
    paths, built = export(spec, out, cfg, tag=f"v0 {source}")
    print(json.dumps({k: str(v) for k, v in paths.items()}, indent=1))
    print("openings:", [(o["name"], o["size"]) for o in built["meta"]["openings"]])


if __name__ == "__main__":
    main(*sys.argv[1:])
