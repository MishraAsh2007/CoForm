r"""Clearance margins for the risk check: exact minimum distance (OCC BRepExtrema) from every part keep-out box,
plug zone and cable path to the base and to the lid, plus the nearest point (to say where it is).

  .venv\Scripts\python.exe -m cad.margins <spec.json> <out.json> [sla|fdm]
"""
import json
import sys
from pathlib import Path

import yaml
from OCP.BRepExtrema import BRepExtrema_DistShapeShape

from cad.common import ROOT
from cad.components import SIDES, box_solid, plug_zones
from cad.enclosure import build


def dist(a, b):
    d = BRepExtrema_DistShapeShape(a.wrapped, b.wrapped)
    d.Perform()
    p = d.PointOnShape1(1)
    return round(d.Value(), 2), [round(p.X(), 1), round(p.Y(), 1), round(p.Z(), 1)]


def main(spec_path, out_path, process="sla"):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    brief = yaml.safe_load((ROOT / "benchmark" / "briefs" / "a_pi5_standard.yaml").read_text(encoding="utf-8"))
    built = build(spec)
    comp, meta = built["comp"], built["meta"]
    base, lid = built["base"].val(), built["lid"].val()
    items = [("keepout", k["name"], k["box"]) for k in comp["keepouts"] if k["name"] != "pcb"]
    items += [("plug_zone", z["name"], z["box"]) for z in plug_zones(comp)]
    cables = {a["name"]: a for a in comp["cable_access"]}
    for name in brief.get("required_cables", []):
        a = cables[name]
        axis, sign = SIDES[a["side"]]
        inner, outer = meta["wall_faces"][a["side"]]
        u, zc = a["centre"]
        w, h = a["size"]
        lo, hi = sorted([inner - sign * 1.0, outer + sign * 10.0])
        zone = [lo, hi, u - w / 2, u + w / 2, zc - h / 2, zc + h / 2] if axis == 0 else \
            [u - w / 2, u + w / 2, lo, hi, zc - h / 2, zc + h / 2]
        items.append(("cable_path", name, zone))
    rows = []
    for kind, name, box in items:
        s = box_solid(box)
        db, pb = dist(s, base)
        dl, pl = dist(s, lid)
        rows.append({"kind": kind, "name": name, "box": box, "to_base_mm": db, "base_point": pb,
                     "to_lid_mm": dl, "lid_point": pl, "margin_mm": min(db, dl)})
    lv = meta["levels"]
    out = {"process": process, "spec": str(spec_path), "levels": lv, "inner_box": meta["inner_box"],
           "outer_box": meta["outer_box"], "openings": meta["openings"], "screws": meta["screws"], "rows": rows}
    Path(out_path).write_text(json.dumps(out, indent=1), encoding="utf-8")
    for r in sorted(rows, key=lambda r: r["margin_mm"]):
        print(f"{r['margin_mm']:6.2f}  {r['kind']:10s} {r['name']:24s} base {r['to_base_mm']:6.2f} @{r['base_point']}  "
              f"lid {r['to_lid_mm']:6.2f} @{r['lid_point']}")


if __name__ == "__main__":
    main(*sys.argv[1:])
