r"""Organize results\print\ for the print candidate: Formlabs, SLA STL, FDM STL, Onshape STEP, slide renders, zips.

  .venv\Scripts\python.exe -m cad.print_package layout     # formlabs\ + stl_sla\ (copies, PRINT_SETTINGS.json)
  .venv\Scripts\python.exe -m cad.print_package step       # onshape\ STEP files in the Pi 5 board frame
  .venv\Scripts\python.exe -m cad.print_package renders    # slide PNGs
  .venv\Scripts\python.exe -m cad.print_package zips       # download zips for the app

One step per call (low memory). The FDM files come from cad/fdm.py.
Frame for every STEP: the Raspberry Pi 5 board frame (components/rpi5.yaml): origin = PCB bottom-left corner,
x along 85 mm, y along 56 mm, z = 0 at the PCB bottom. The official Raspberry Pi STEP uses the same frame,
so every file lands assembled when imported at the origin.
"""
import json
import shutil
import sys
import zipfile
from pathlib import Path

from cad.common import ROOT, load_config, print_settings

RUN = ROOT / "results" / "runs" / "a_pi5_standard__collective__generic__s0__claude-sonnet-5-5"
OUT = ROOT / "results" / "print"
PI_STEP = ROOT / "results" / "tmp" / "dl" / "step" / "rpi-5b_no_graphics.step"
PI_LICENSE = PI_STEP.parent / "LICENSE.txt"


def spec():
    return json.loads((RUN / "final_spec.json").read_text(encoding="utf-8"))


def layout():
    cfg = load_config()
    fl, sla = OUT / "formlabs", OUT / "stl_sla"
    fl.mkdir(parents=True, exist_ok=True)
    sla.mkdir(parents=True, exist_ok=True)
    for name in ("enclosure_a.form", "enclosure_a.json"):
        src = OUT / name
        if src.exists():
            shutil.move(str(src), str(fl / name))
    rep = json.loads((fl / "enclosure_a.json").read_text(encoding="utf-8"))
    info = {**print_settings(cfg),
            "note": "Open enclosure_a.form in PreForm and print as is: parts are already oriented, supported and "
                    "laid out on one build platform; PreForm reported zero warnings.",
            "parts": ["base", "lid"], "orientation": rep.get("orientation_chosen"),
            "print_time_min": round(rep["print_time_s"] / 60, 1), "resin_ml_incl_supports": round(rep["volume_ml"], 1),
            "preform_warnings": rep["warnings"], "source_run": RUN.name}
    (fl / "PRINT_SETTINGS.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    for p in ("base", "lid"):
        shutil.copy2(RUN / f"{p}.stl", sla / f"{p}.stl")
    shutil.copy2(fl / "PRINT_SETTINGS.json", sla / "PRINT_SETTINGS.json")
    # the .form was made from these exact meshes with the "flat" orientation = no rotation (checks/orientations.py)
    print(json.dumps(info, indent=1))


def _board_simplified(comp):
    import cadquery as cq
    from cad.components import board_solid, box_solid
    b = board_solid(comp).val()
    boxes = [box_solid(k["box"]) for k in comp["keepouts"]
             if k["name"] != "pcb" and not k["name"].startswith("cooler") and k["name"] != "active_cooler"]
    return b, cq.Compound.makeCompound(boxes)


def step():
    import cadquery as cq
    from cad.components import box_solid, load_bundle
    from cad.enclosure import build
    on = OUT / "onshape"
    on.mkdir(parents=True, exist_ok=True)
    built = build(spec())
    comp = built["comp"]
    base, lid = built["base"].val(), built["lid"].val()
    cooler = cq.Compound.makeCompound([box_solid(k["box"]) for k in comp["keepouts"]
                                       if k["name"] == "active_cooler" or k["name"].startswith("cooler_pin")])
    pcb, parts = _board_simplified(comp)
    from cad.components import plug_zones
    plugs = cq.Compound.makeCompound([box_solid(z["box"]) for z in plug_zones(comp)])
    files = {"enclosure_base.step": base, "enclosure_lid.step": lid,
             "active_cooler_keepout_APPROX.step": cooler,
             "plug_zones_APPROX.step": plugs,
             "rpi5_board_simplified.step": cq.Compound.makeCompound([pcb, parts])}
    (on / "plug_zones").mkdir(exist_ok=True)
    for z in plug_zones(comp):   # one file per plug zone, so each can be measured on its own in Onshape
        cq.exporters.export(cq.Workplane("XY").add(box_solid(z["box"])),
                            str(on / "plug_zones" / f"plug_{z['name']}_APPROX.step"))
    for name, shape in files.items():
        cq.exporters.export(cq.Workplane("XY").add(shape), str(on / name))
    asm = cq.Assembly(name="pi5_enclosure_assembly")
    asm.add(base, name="enclosure_base", color=cq.Color(0.85, 0.85, 0.85))
    asm.add(lid, name="enclosure_lid", color=cq.Color(0.62, 0.77, 0.91, 0.5))
    asm.add(cooler, name="active_cooler_keepout_APPROX", color=cq.Color(0.29, 0.56, 0.85))
    asm.add(pcb, name="rpi5_pcb_simplified", color=cq.Color(0.18, 0.55, 0.34))
    asm.add(parts, name="rpi5_connector_keepouts_simplified", color=cq.Color(0.29, 0.56, 0.85))
    asm.add(plugs, name="plug_zones_APPROX", color=cq.Color(0.85, 0.33, 0.31, 0.4))
    asm.save(str(on / "assembly_enclosure_pi5.step"))
    if PI_STEP.exists() and not (on / "rpi5_board_official.step").exists():
        shutil.copy2(PI_STEP, on / "rpi5_board_official.step")
        shutil.copy2(PI_LICENSE, on / "rpi5_board_official_LICENSE.txt")
    for f in sorted(on.glob("*.step")):
        print(f.name, round(f.stat().st_size / 1e6, 2), "MB")


def renders():
    import cadquery as cq
    from cad.common import export_stl
    from cad.components import board_solid, box_solid
    from cad.enclosure import build
    from cad.render import render
    cfg = load_config()
    built = build(spec())
    comp = built["comp"]
    tmp = OUT / "_mesh"
    tmp.mkdir(exist_ok=True)
    base = export_stl(built["base"], tmp / "base.stl", cfg)
    lid = export_stl(built["lid"], tmp / "lid.stl", cfg)
    lid_up = export_stl(built["lid"].translate((0, 0, 30)), tmp / "lid_up.stl", cfg)
    brd = export_stl(board_solid(comp), tmp / "board.stl", cfg)
    parts = export_stl(cq.Compound.makeCompound([box_solid(k["box"]) for k in comp["keepouts"]
                                                 if k["name"] not in ("pcb", "active_cooler")]), tmp / "parts.stl", cfg)
    cooler = export_stl(cq.Compound.makeCompound([box_solid(k["box"]) for k in comp["keepouts"]
                                                  if k["name"] == "active_cooler"]), tmp / "cooler.stl", cfg)
    t = "Pi 5 enclosure (print candidate)"
    render([(base, "#d9d9d9"), (lid, "#9ec5e8", 0.45), (brd, "#2e8b57"), (parts, "#4a90d9", 0.5), (cooler, "#e0a030", 0.5)],
           OUT / "render_assembled.png", size=(1600, 1200), title=f"{t}: assembled (lid see-through)")
    render([(base, "#d9d9d9"), (lid_up, "#9ec5e8"), (brd, "#2e8b57"), (parts, "#4a90d9", 0.6), (cooler, "#e0a030", 0.6)],
           OUT / "render_exploded.png", size=(1600, 1200), title=f"{t}: exploded")
    render([(base, "#d9d9d9"), (brd, "#2e8b57"), (parts, "#4a90d9", 0.8), (cooler, "#e0a030", 0.8)],
           OUT / "render_lid_off.png", size=(1600, 1200),
           title=f"{t}: lid off, board + connectors (blue) + Active Cooler keep-out (orange, approximate)")
    shutil.rmtree(tmp, ignore_errors=True)
    print("renders written")


def zips():
    def z(name, files):
        with zipfile.ZipFile(OUT / name, "w", zipfile.ZIP_DEFLATED) as f:
            for p in files:
                f.write(p, p.relative_to(OUT))
        print(name, round((OUT / name).stat().st_size / 1e6, 2), "MB")
    z("stl_sla.zip", sorted((OUT / "stl_sla").glob("*")))
    z("fdm.zip", [p for p in sorted((OUT / "fdm").glob("*")) if p.is_file()])
    z("onshape_step.zip", sorted(p for p in (OUT / "onshape").rglob("*") if p.is_file()))


if __name__ == "__main__":
    {"layout": layout, "step": step, "renders": renders, "zips": zips}[sys.argv[1]]()
