"""Runs inside FreeCAD's Python (FreeCADCmd), never imported by ScienceClaw.

Job JSON path arrives in SCIENCECLAW_FREECAD_JOB. The master model is a Spreadsheet
("Params", aliases sc_*) driving Part solids through expressions, so a person can open
it in the FreeCAD GUI, change a cell, and see the same parametric rebuild the agents use.
All FreeCAD lengths are millimeters.
"""
import json
import os
import traceback

import FreeCAD as App
import MeshPart
import Part  # noqa: F401  (registers Part types)

PARAMS = (("sc_length", "Total length"), ("sc_base_width", "Base width"),
          ("sc_wall_height", "Wall height above base / outside height minus thickness"), ("sc_thickness", "Thickness"))


def _box(doc, name, length, width, height, y=None, z=None):
    box = doc.addObject("Part::Box", name)
    box.setExpression("Length", length)
    box.setExpression("Width", width)
    box.setExpression("Height", height)
    if y:
        box.setExpression("Placement.Base.y", y)
    if z:
        box.setExpression("Placement.Base.z", z)
    return box


def build_master(path, family, values):
    doc = App.newDocument("ScienceClawSupport")
    sheet = doc.addObject("Spreadsheet::Sheet", "Params")
    sheet.set("A1", "ScienceClaw parameters (mm). Edit column B; the solid rebuilds.")
    sheet.set("A2", "Section family")
    sheet.set("B2", family)
    for row, (alias, label) in enumerate(PARAMS, start=3):
        sheet.set(f"A{row}", label)
        sheet.set(f"B{row}", f"{values[alias]} mm")
        sheet.setAlias(f"B{row}", alias)
    doc.recompute()
    L, b, h, t = "Params.sc_length", "Params.sc_base_width", "Params.sc_wall_height", "Params.sc_thickness"
    if family == "u_channel":
        # Walls stand on the full-width base; arm extends along +X from the clamp end.
        base = _box(doc, "Base", L, b, t)
        wall_a = _box(doc, "WallA", L, t, h, z=t)
        wall_b = _box(doc, "WallB", L, t, h, y=f"{b} - {t}", z=t)
        arm = doc.addObject("Part::MultiFuse", "SupportArm")
        arm.Shapes = [base, wall_a, wall_b]
        arm.Refine = True
    elif family == "rectangular_tube":
        outer = _box(doc, "Outer", L, b, f"{h} + {t}")
        bore = _box(doc, "Bore", L, f"{b} - 2 * {t}", f"{h} - {t}", y=t, z=t)
        arm = doc.addObject("Part::Cut", "SupportArm")
        arm.Base, arm.Tool = outer, bore
        arm.Refine = True
    else:
        arm = _box(doc, "SupportArm", L, b, f"{h} + {t}")
    doc.recompute()
    doc.saveAs(path)
    return doc


def main():
    job = json.load(open(os.environ["SCIENCECLAW_FREECAD_JOB"], encoding="utf-8"))
    result = {"ok": False, "freecad_version": ".".join(App.Version()[:3])}
    try:
        if job["action"] == "version":
            result["ok"] = True
            return result
        master = job["master"]
        doc = App.openDocument(master) if os.path.exists(master) else build_master(master, job["family"], job["values_mm"])
        sheet = doc.getObject("Params")
        if sheet.get("B2") != job["family"]:
            raise ValueError(f"Master model is a {sheet.get('B2')} section, not {job['family']}")
        for alias, value in job["values_mm"].items():
            sheet.set(sheet.getCellFromAlias(alias), f"{value} mm")
        doc.recompute()
        broken = [o.Name for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]
        compound = doc.getObject("SupportArm").Shape
        # Boolean results arrive as compounds; a printable arm must be exactly one solid.
        if len(compound.Solids) != 1:
            broken.append(f"SupportArm has {len(compound.Solids)} solids")
        shape = compound.Solids[0] if compound.Solids else compound
        length = float(job["values_mm"]["sc_length"])
        volume = shape.Volume
        # Unit-density inertia about the centroid: I_yy = Iz_section * L + A * L^3 / 12 for a prism along X.
        inertia_yy = shape.MatrixOfInertia.A22
        area = volume / length
        box = shape.BoundBox
        result.update(
            broken_objects=broken, is_valid=shape.isValid(), solids=len(shape.Solids),
            volume_mm3=volume, area_mm2=area, second_moment_mm4=inertia_yy / length - area * length**2 / 12,
            extents_mm=[box.XLength, box.YLength, box.ZLength])
        doc.saveAs(job["fcstd"])  # The per-candidate snapshot; the master file on disk is unchanged.
        shape.exportStep(job["step"])
        mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=0.01, AngularDeflection=0.1)
        mesh.write(job["stl"])
        App.closeDocument(doc.Name)
        result["ok"] = not broken and shape.isValid()
        if not result["ok"]:
            result["error"] = "FreeCAD model failed to rebuild: " + ", ".join(broken or ["invalid shape"])
    except Exception as exc:  # Reported to ScienceClaw; the subprocess must always write a result.
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["trace"] = traceback.format_exc()[-2000:]
    return result


output = json.load(open(os.environ["SCIENCECLAW_FREECAD_JOB"], encoding="utf-8"))["result"]
with open(output, "w", encoding="utf-8") as handle:
    json.dump(main(), handle)
