r"""Phase 2 fit-check frame: a thin plate with the 4 standoffs (with locating pins) and
1.2 mm walls with port notches on the port sides. Set the Pi on it to check port alignment
before printing a full enclosure.

Run:  .venv\Scripts\python.exe -m cad.fit_check
Writes results/fit_check/fit_check_frame.stl and pictures.
"""
import cadquery as cq

from cad.common import ROOT, engrave, export_stl, load_config, write_print_info
from cad.components import SIDES, box_solid, load_bundle, port_opening
from cad.render import render


def wall_box(side, b, g, w, z0, z1):
    L, W = b["length"], b["width"]
    if side == "-y":
        return [-g - w, L + g + w, -g - w, -g, z0, z1]
    if side == "+y":
        return [-g - w, L + g + w, W + g, W + g + w, z0, z1]
    if side == "+x":
        return [L + g, L + g + w, -g - w, W + g + w, z0, z1]
    return [-g - w, -g, -g - w, W + g + w, z0, z1]


def wall_outer(side, b, g, w):
    return {"-y": -g - w, "+y": b["width"] + g + w, "+x": b["length"] + g + w, "-x": -g - w}[side]


def notch_box(port, opening, wall_bx, z_top):
    """Open-top notch through a wall: from the bottom of the opening up past the wall top."""
    axis, _ = SIDES[port["side"]]
    u, zc = port["centre"]
    w, h = opening
    z0 = zc - h / 2
    if axis == 0:
        return [wall_bx[0] - 1, wall_bx[1] + 1, u - w / 2, u + w / 2, z0, z_top + 1]
    return [u - w / 2, u + w / 2, wall_bx[2] - 1, wall_bx[3] + 1, z0, z_top + 1]


def make_frame(cfg, comp):
    f = cfg["fit_check"]
    b = comp["board"]
    clr = cfg["clearances"]["generic"]["port_cutout"]
    g, w = f["wall_gap"], f["wall_thickness"]
    z_plate_top = -f["standoff_height"]
    z_plate_bot = z_plate_top - f["plate_thickness"]
    plate = [-g - w, b["length"] + g + w, -g - w, b["width"] + g + f["label_strip"], z_plate_bot, z_plate_top]
    part = cq.Workplane("XY").add(box_solid(plate))
    for x, y in comp["mounting_holes"]["positions"]:
        post = (cq.Workplane("XY").workplane(offset=z_plate_top).center(x, y)
                .circle(f["standoff_diameter"] / 2).extrude(f["standoff_height"])
                .faces(">Z").workplane().circle(f["locating_pin_diameter"] / 2).extrude(f["locating_pin_height"]))
        part = part.union(post)
    notches = []
    for side in f["port_sides"]:
        ports = [p for p in comp["ports"] if p["side"] == side]
        openings = [port_opening(p, wall_outer(side, b, g, w), clr) for p in ports]
        # wall only as tall as needed: highest opening top + margin (thin slivers stay short)
        top = min(f["wall_top_above_pcb"],
                  max(p["centre"][1] + o[1] / 2 for p, o in zip(ports, openings)) + f["wall_margin_above_ports"])
        wb = wall_box(side, b, g, w, z_plate_top, top)
        part = part.union(cq.Workplane("XY").add(box_solid(wb)))
        for p, opening in zip(ports, openings):
            nb = notch_box(p, opening, wb, top)
            notches.append({"port": p["name"], "opening_mm": [round(v, 2) for v in opening]})
            part = part.cut(cq.Workplane("XY").add(box_solid(nb)))
    label = f"PI5 FIT v1 gap{g} clr{clr}"
    part = engrave(part, label, b["length"] / 2, b["width"] + g + f["label_strip"] / 2, z_plate_top, cfg)
    return part, notches


def drop_in_collisions(part, comp, z_top=60.0):
    """Sweep every keep-out straight up (the board is lowered in from above) and report overlaps."""
    hits = []
    solid = part.val()
    sweeps = [("pcb", _board_sweep(comp, z_top))]
    for k in comp["keepouts"]:
        if k["name"] == "pcb":
            continue
        x0, x1, y0, y1, z0, _ = k["box"]
        sweeps.append((k["name"], box_solid([x0, x1, y0, y1, z0, z_top])))
    for name, sweep in sweeps:
        v = solid.intersect(sweep).Volume()
        if v > 1e-3:
            hits.append((name, round(v, 2)))
    return hits


def _board_sweep(comp, z_top):
    """The board outline (with its mounting holes) extruded from z 0 to z_top."""
    b = comp["board"]
    s = (cq.Workplane("XY").box(b["length"], b["width"], z_top, centered=False)
         .edges("|Z").fillet(b.get("corner_radius", 0.5)))
    d = comp["mounting_holes"]["diameter"]
    for x, y in comp["mounting_holes"]["positions"]:
        s = s.cut(cq.Workplane("XY").center(x, y).circle(d / 2).extrude(z_top + 1))
    return s.val()


def main():
    cfg = load_config()
    comp = load_bundle(cfg["fit_check"]["bundle"])
    part, notches = make_frame(cfg, comp)
    out = ROOT / cfg["fit_check"]["out_dir"]
    stl = export_stl(part, out / "fit_check_frame.stl", cfg)
    hits = drop_in_collisions(part, comp)
    write_print_info(out, cfg, {"parts": ["fit_check_frame.stl"]})
    print("notches:", notches)
    print("drop-in collisions:", hits or "none")
    tmp = ROOT / "results" / "tmp"
    from cad.components import board_solid
    ko = cq.Compound.makeCompound([box_solid(k["box"]) for k in comp["keepouts"] if k["name"] != "pcb"])
    meshes = [(stl, "#d9d9d9"), (export_stl(board_solid(comp), tmp / "fc_board.stl", cfg), "#2e8b57"),
              (export_stl(ko, tmp / "fc_parts.stl", cfg), "#4a90d9", 0.6)]
    render([(stl, "#d9d9d9")], out / "fit_check_frame.png", view="iso")
    render(meshes, out / "fit_check_with_pi.png", view="iso")
    print("wrote", stl)


if __name__ == "__main__":
    main()
