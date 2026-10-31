r"""Render the keep-out model of a component bundle (board, parts, add-ons, plug zones).

Run:  .venv\Scripts\python.exe -m cad.keepout_view rpi5_cooler
Writes results/components/<bundle>_keepout_{iso,top}.png
"""
import sys

import cadquery as cq

from cad.common import ROOT, export_stl, load_config
from cad.components import board_solid, box_solid, load_bundle, plug_zones
from cad.render import render


def main(bundle="rpi5_cooler"):
    cfg = load_config()
    comp = load_bundle(bundle)
    out = ROOT / "results" / "components"
    tmp = ROOT / "results" / "tmp"
    meshes = [(export_stl(board_solid(comp), tmp / "kv_board.stl", cfg), "#2e8b57")]
    addon_names = {k["name"] for a in comp["addons"] for k in a.get("keepouts", [])}
    groups = {"parts": [], "addons": [], "plugs": []}
    for k in comp["keepouts"]:
        if k["name"] == "pcb":
            continue
        groups["addons" if k["name"] in addon_names else "parts"].append(box_solid(k["box"]))
    groups["plugs"] = [box_solid(p["box"]) for p in plug_zones(comp)]
    colors = {"parts": ("#c0c0c0", 1.0), "addons": ("#4a90d9", 0.8), "plugs": ("#e0a030", 0.35)}
    for g, solids in groups.items():
        if solids:
            p = export_stl(cq.Compound.makeCompound(solids), tmp / f"kv_{g}.stl", cfg)
            meshes.append((p, *colors[g]))
    for view in ("iso", "top"):
        render(meshes, out / f"{bundle}_keepout_{view}.png", view=view,
               title=f"{comp['name']} keep-outs (grey parts, blue add-ons, orange plug zones)")
    print("wrote", out)


if __name__ == "__main__":
    main(*sys.argv[1:])
