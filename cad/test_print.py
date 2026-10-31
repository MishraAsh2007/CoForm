"""Phase 1 tolerance test print: a labelled Plate and a Key.

Run:  .venv\\Scripts\\python.exe -m cad.test_print
Writes results/test_print/plate.stl and key.stl.

Plate rows (top to bottom), each feature has its value engraved below it:
  P  round holes for the 5.00 mm key peg
  G  3 mm deep grooves for the 2.00 mm key tongue
  H  M2.5 through-holes
  N  M2.5 hex nut pockets (with a through-hole for the screw)
  T  screw posts with a pilot hole (outer diameter labelled), then W thin walls
"""
import math

import cadquery as cq

from cad.common import ROOT, engrave, export_stl, load_config


def plate_layout(cfg):
    """Return row centre heights so the generator, checklist and tests agree."""
    tp = cfg["test_print"]
    pl = tp["plate"]
    lab = cfg["labels"]["size_mm"]
    hex_corner = max(tp["nut_pockets"]["across_flats_mm"]) / math.cos(math.radians(30))
    rows = [
        ("P", max(tp["peg_holes_mm"])),
        ("G", tp["grooves"]["length"]),
        ("H", max(tp["m25_holes_mm"])),
        ("N", hex_corner),
        ("T", max(max(tp["screw_posts"]["outer_diameters_mm"]), tp["thin_walls"]["length"])),
    ]
    y = pl["height"] - pl["margin"]
    out = {}
    for name, size in rows:
        fy = y - size / 2
        ly = fy - size / 2 - pl["label_gap"] - lab / 2
        out[name] = {"feature_y": fy, "label_y": ly, "size": size}
        y = ly - lab / 2 - pl["row_gap"]
    if y < 0:
        raise ValueError(f"plate rows do not fit: {-y:.1f} mm short; raise test_print.plate.height")
    return out


def column_x(cfg, i):
    pl = cfg["test_print"]["plate"]
    return pl["first_column_x"] + i * pl["column_pitch"]


def make_plate(cfg):
    tp = cfg["test_print"]
    pl = tp["plate"]
    W, H, T = pl["width"], pl["height"], pl["thickness"]
    rows = plate_layout(cfg)
    part = cq.Workplane("XY").box(W, H, T, centered=False)
    labels = []  # (text, x, y)

    def through_cylinder(x, y, d, z0=-1, h=None):
        return cq.Workplane("XY").workplane(offset=z0).center(x, y).circle(d / 2).extrude(h or T + 20)

    # P: peg holes
    r = rows["P"]
    for i, d in enumerate(tp["peg_holes_mm"]):
        x = column_x(cfg, i)
        part = part.cut(through_cylinder(x, r["feature_y"], d))
        labels.append((f"{d:.1f}", x, r["label_y"]))

    # G: grooves, long axis along Y
    r = rows["G"]
    g = tp["grooves"]
    for i, w in enumerate(g["widths_mm"]):
        x = column_x(cfg, i)
        slot = (cq.Workplane("XY").workplane(offset=T - g["depth"])
                .center(x, r["feature_y"]).rect(w, g["length"]).extrude(g["depth"] + 1))
        part = part.cut(slot)
        labels.append((f"{w:.1f}", x, r["label_y"]))

    # H: M2.5 through-holes
    r = rows["H"]
    for i, d in enumerate(tp["m25_holes_mm"]):
        x = column_x(cfg, i)
        part = part.cut(through_cylinder(x, r["feature_y"], d))
        labels.append((f"{d:.1f}", x, r["label_y"]))

    # N: hex nut pockets from the top, with a through-hole
    r = rows["N"]
    n = tp["nut_pockets"]
    for i, af in enumerate(n["across_flats_mm"]):
        x = column_x(cfg, i)
        corner = af / math.cos(math.radians(30))
        pocket = (cq.Workplane("XY").workplane(offset=T - n["depth"])
                  .center(x, r["feature_y"]).polygon(6, corner).extrude(n["depth"] + 1))
        part = part.cut(pocket).cut(through_cylinder(x, r["feature_y"], n["through_hole"]))
        labels.append((f"{af:.1f}", x, r["label_y"]))

    # T: screw posts, then W: thin walls (same row)
    r = rows["T"]
    s = tp["screw_posts"]
    for i, od in enumerate(s["outer_diameters_mm"]):
        x = column_x(cfg, i)
        post = cq.Workplane("XY").workplane(offset=T).center(x, r["feature_y"]).circle(od / 2).extrude(s["height"])
        part = part.union(post).cut(through_cylinder(x, r["feature_y"], s["pilot_hole"]))
        labels.append((f"{od:.0f}", x, r["label_y"]))
    w = tp["thin_walls"]
    for i, t in enumerate(w["thicknesses_mm"]):
        x = w["first_x"] + i * w["pitch"]
        wall = (cq.Workplane("XY").workplane(offset=T).center(x, r["feature_y"])
                .rect(t, w["length"]).extrude(w["height"]))
        part = part.union(wall)
        labels.append((f"{t:.1f}", x, r["label_y"]))
    labels.append(("W", w["first_x"] - 4.5, r["feature_y"]))

    # row letters
    for name in rows:
        labels.append((name, pl["row_letter_x"], rows[name]["feature_y"]))

    for text, x, y in labels:
        part = engrave(part, text, x, y, T, cfg)
    return part


def make_key(cfg):
    k = cfg["test_print"]["key"]
    hx, hy, hz = k["handle"]
    part = cq.Workplane("XY").box(hx, hy, hz, centered=False)
    peg = (cq.Workplane("XY").workplane(offset=hz).center(k["peg_x"], hy / 2)
           .circle(k["peg_diameter"] / 2).extrude(k["peg_length"])
           .faces(">Z").edges().chamfer(k["tip_chamfer"]))
    tongue = (cq.Workplane("XY").workplane(offset=hz).center(k["tongue_x"], hy / 2)
              .rect(k["tongue_width"], k["tongue_length"]).extrude(k["tongue_height"])
              .faces(">Z").edges().chamfer(k["tip_chamfer"]))
    part = part.union(peg).union(tongue)
    part = engrave(part, f"{k['peg_diameter']:.2f}", k["peg_x"] + 9, hy / 2, hz, cfg)
    part = engrave(part, f"{k['tongue_width']:.2f}", k["tongue_x"] - 9, hy / 2, hz, cfg)
    return part


def main():
    cfg = load_config()
    out = ROOT / cfg["test_print"]["out_dir"]
    p = export_stl(make_plate(cfg), out / "plate.stl", cfg)
    k = export_stl(make_key(cfg), out / "key.stl", cfg)
    print(f"wrote {p}\nwrote {k}")


if __name__ == "__main__":
    main()
