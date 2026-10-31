"""Load component YAMLs (board + add-ons) into one dict in the board frame,
and turn them into simple geometry: keep-out boxes, plug zones, cable zones.

A component bundle is a list of YAML names, e.g. ["rpi5", "active_cooler"].
Boxes are [xmin, xmax, ymin, ymax, zmin, zmax].
"""
import cadquery as cq
import yaml

from cad.common import ROOT

BUNDLES = {
    "rpi5_cooler": ["rpi5", "active_cooler"],
    "rpi5": ["rpi5"],
    "arduino_uno": ["arduino_uno"],
    "esp32_devkitc": ["esp32_devkitc"],
}

# Outward direction of each side: (axis index, sign)
SIDES = {"-x": (0, -1), "+x": (0, 1), "-y": (1, -1), "+y": (1, 1)}


def load_yaml(name):
    with open(ROOT / "components" / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_bundle(bundle):
    names = BUNDLES.get(bundle, [bundle])
    base = load_yaml(names[0])
    comp = {"bundle": bundle, "name": base["name"], "board": base["board"],
            "mounting_holes": base["mounting_holes"], "ports": list(base.get("ports", [])),
            "keepouts": list(base.get("keepouts", [])), "cable_access": list(base.get("cable_access", [])),
            "addons": []}
    for extra in names[1:]:
        d = load_yaml(extra)
        comp["addons"].append(d)
        comp["keepouts"] += d.get("keepouts", [])
        comp["ports"] += d.get("ports", [])
    return comp


def envelope(comp):
    """Bounding box of every keep-out (board + parts + add-ons)."""
    boxes = [k["box"] for k in comp["keepouts"]]
    b = comp["board"]
    boxes.append([0, b["length"], 0, b["width"], 0, b["thickness"]])
    return [min(x[0] for x in boxes), max(x[1] for x in boxes), min(x[2] for x in boxes),
            max(x[3] for x in boxes), min(x[4] for x in boxes), max(x[5] for x in boxes)]


def side_rect_box(side, centre, size, face, length, inward=0.0):
    """Box for a rectangle on a side wall: cross-section `size` centred on `centre`
    (along-edge coordinate, z), from `face - inward` outward by `length`."""
    axis, sign = SIDES[side]
    u, zc = centre
    w, h = size
    a0, a1 = sorted([face - sign * inward, face + sign * length])
    if axis == 0:
        return [a0, a1, u - w / 2, u + w / 2, zc - h / 2, zc + h / 2]
    return [u - w / 2, u + w / 2, a0, a1, zc - h / 2, zc + h / 2]


def plug_zones(comp):
    """Space a mating cable plug needs outside each port."""
    out = []
    for p in comp["ports"]:
        plug = p.get("plug", {})
        if plug.get("length", 0) <= 0:
            continue
        out.append({"name": p["name"], "side": p["side"],
                    "box": side_rect_box(p["side"], p["centre"], plug["size"], p["face"], plug["length"])})
    return out


def box_solid(box):
    x0, x1, y0, y1, z0, z1 = box
    return cq.Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, cq.Vector(x0, y0, z0))


def board_solid(comp):
    b = comp["board"]
    s = (cq.Workplane("XY").box(b["length"], b["width"], b["thickness"], centered=False)
         .edges("|Z").fillet(b.get("corner_radius", 0.5)))
    d = comp["mounting_holes"]["diameter"]
    for x, y in comp["mounting_holes"]["positions"]:
        s = s.cut(cq.Workplane("XY").center(x, y).circle(d / 2).extrude(b["thickness"] + 1))
    return s


def keepout_model(comp):
    """(board, parts) as CadQuery objects for rendering and collision checks."""
    parts = [box_solid(k["box"]) for k in comp["keepouts"] if k["name"] != "pcb"]
    return board_solid(comp), cq.Workplane("XY").newObject([cq.Compound.makeCompound(parts)])


def wall_recess(port, wall_outer):
    """How far the wall's outer face sits past the connector front face (mm, >0 means the plug
    body must enter the wall opening)."""
    _, sign = SIDES[port["side"]]
    return sign * (wall_outer - port["face"])


def port_opening(port, wall_outer, clearance):
    """(width, height) of the wall opening for a port: the connector face plus clearance,
    or the plug body plus clearance if the wall sticks out past the connector face."""
    size = port["size"]
    plug = port.get("plug", {})
    if wall_recess(port, wall_outer) > 0 and plug.get("length", 0) > 0:
        size = [max(a, b) for a, b in zip(size, plug["size"])]
    return [size[0] + 2 * clearance, size[1] + 2 * clearance]
