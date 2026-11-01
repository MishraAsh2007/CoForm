# Bracket geometry generator. Runs under FreeCAD's bundled Python, NOT the project .venv.
# bracket.py launches it as a subprocess: JSON parameters on stdin, JSON result on stdout.
import json
import math
import os
import sys

# FreeCAD's modules live next to its interpreter; make them importable.
_bin = os.path.dirname(sys.executable)
for _p in (_bin, os.path.join(os.path.dirname(_bin), "lib")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

import FreeCAD  # noqa: E402
import MeshPart  # noqa: E402
import Part  # noqa: E402

Vector = FreeCAD.Vector

STL_LINEAR_DEFLECTION = 0.05  # mm, max chord error when meshing for PreForm
STL_ANGULAR_DEFLECTION = 0.2  # rad


def _rib_junction_edges(solid, t, h, r, x0, x1, tol=1e-6):
    """Concave edges where the rib meets the plates (sides, inner-fillet arcs, toe, top)."""
    def near(a, b):
        return abs(a - b) < tol

    found = []
    for edge in solid.Edges:
        bb = edge.BoundBox
        on_side = near(bb.XMin, bb.XMax) and (near(bb.XMin, x0) or near(bb.XMin, x1))
        across = near(bb.XMin, x0) and near(bb.XMax, x1)
        if on_side and (
                (near(bb.ZMin, t) and near(bb.ZMax, t))                      # on the arm top
                or (near(bb.YMin, t) and near(bb.YMax, t))                   # on the wall face
                or (bb.YMax <= t + r + tol and bb.ZMax <= t + r + tol)):     # along the inner fillet
            found.append(edge)
        elif across and ((near(bb.YMin, t + h) and near(bb.ZMin, t))         # toe on the arm
                         or (near(bb.YMin, t) and near(bb.ZMin, t + h))):    # top on the wall
            found.append(edge)
    return found


def build(plate_thickness, rib_height, rib_thickness, width, arm_length, wall_height,
          fillet_radius, rib_fillet_radius):
    """Return a single valid Part.Solid; see bracket.build_bracket for the layout."""
    t, h, rt, r = plate_thickness, rib_height, rib_thickness, fillet_radius

    # Construct the two perpendicular plates as one L-profile in the YZ plane,
    # with a fillet of radius r at the inner corner, extruded along X.
    def p(y, z):
        return Vector(0, y, z)

    c = t + r - r / math.sqrt(2)  # arc midpoint coordinate
    edges = [
        Part.LineSegment(p(0, 0), p(arm_length, 0)).toShape(),
        Part.LineSegment(p(arm_length, 0), p(arm_length, t)).toShape(),
        Part.LineSegment(p(arm_length, t), p(t + r, t)).toShape(),
        Part.Arc(p(t + r, t), p(c, c), p(t, t + r)).toShape(),
        Part.LineSegment(p(t, t + r), p(t, wall_height)).toShape(),
        Part.LineSegment(p(t, wall_height), p(0, wall_height)).toShape(),
        Part.LineSegment(p(0, wall_height), p(0, 0)).toShape(),
    ]
    plates = Part.Face(Part.Wire(edges)).extrude(Vector(width, 0, 0))

    # Construct the reinforcing rib in the YZ plane, centered in X. The profile
    # extends into both plates so the fuse overlaps volumes instead of faces.
    x0 = (width - rt) / 2
    profile = [(0, 0), (t + h, 0), (t + h, t), (t, t + h), (0, t + h)]
    points = [Vector(x0, y, z) for y, z in profile]
    rib = Part.Face(Part.makePolygon(points + [points[0]])).extrude(Vector(rt, 0, 0))

    # Combine them into one solid.
    fused = plates.fuse(rib).removeSplitter()
    if len(fused.Solids) != 1 or not fused.Solids[0].isValid():
        raise RuntimeError("bracket geometry is not a single valid solid")
    solid = fused.Solids[0]

    # Round the sharp rib-to-plate junctions. Without this, peak stresses there
    # are singular in FEA and never converge; real prints are not perfectly sharp.
    if rib_fillet_radius > 0:
        edges = _rib_junction_edges(solid, t, h, r, x0, x0 + rt)
        if len(edges) != 8:   # 2 sides x (arm line, wall line, arc) + toe + top
            raise RuntimeError(f"expected 8 rib junction edges, found {len(edges)}")
        solid = solid.makeFillet(rib_fillet_radius, edges)
        if not solid.isValid() or len(solid.Solids) != 1:
            raise RuntimeError("rib fillet produced invalid geometry")
        solid = solid.Solids[0]
    return solid


def _partial(path):
    """Temporary sibling name; watchers (e.g. the Fusion add-in) skip *.partial.*."""
    base, ext = os.path.splitext(path)
    return f"{base}.partial{ext}"


def main():
    args = json.load(sys.stdin)
    solid = build(**args["parameters"])

    # Write to a temporary name and rename, so readers never see a partial file.
    mesh = MeshPart.meshFromShape(Shape=solid,
                                  LinearDeflection=STL_LINEAR_DEFLECTION,
                                  AngularDeflection=STL_ANGULAR_DEFLECTION,
                                  Relative=False)
    mesh.write(_partial(args["stl_path"]))
    os.replace(_partial(args["stl_path"]), args["stl_path"])
    solid.exportStep(_partial(args["step_path"]))
    os.replace(_partial(args["step_path"]), args["step_path"])

    print(json.dumps({"volume_mm3": solid.Volume, "freecad_version": ".".join(FreeCAD.Version()[:3])}))


if __name__ == "__main__":
    main()
