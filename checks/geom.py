"""Small geometry helpers shared by the checks."""
import cadquery as cq

from cad.components import box_solid


def bbox_of(shape):
    b = shape.BoundingBox()
    return [b.xmin, b.xmax, b.ymin, b.ymax, b.zmin, b.zmax]


def boxes_touch(a, b):
    return not (a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2] or a[5] <= b[4] or b[5] <= a[4])


def inflate(box, d):
    return [box[0] - d, box[1] + d, box[2] - d, box[3] + d, box[4] - d, box[5] + d]


def overlap(solid, other, solid_bbox=None):
    """Depth (mm) of the overlap between two shapes: smallest side of the intersection's
    bounding box. 0.0 if they don't overlap. `other` is a CadQuery shape or a box list."""
    if isinstance(other, (list, tuple)):
        if solid_bbox is not None and not boxes_touch(solid_bbox, other):
            return 0.0
        other = box_solid(other)
    if isinstance(solid, cq.Workplane):
        solid = solid.val()
    inter = solid.intersect(other)
    if inter.Volume() < 1e-4:
        return 0.0
    b = inter.BoundingBox()
    return round(min(b.xlen, b.ylen, b.zlen), 2)


def cylinder(x, y, d, z0, z1):
    return cq.Solid.makeCylinder(d / 2, z1 - z0, cq.Vector(x, y, z0))
