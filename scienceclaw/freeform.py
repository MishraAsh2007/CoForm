"""Free-form parts from a design spec: CAD, finite-element tests and printability checks.

A spec is a small parametric CAD program: boxes and cylinders added or cut, every dimension a
named parameter with an allowed range, plus where the part is held (supports) and loaded
(loads). Gmsh's OpenCASCADE kernel builds the solid (STEP for Fusion, STL for printing) and
CalculiX tests it. Anything an agent writes either builds into one solid or is rejected with a
reason it can act on. Units: mm, N, MPa; z is up.
"""
from __future__ import annotations

import ast
import json
import math
import operator
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .domain import Record
from .fea import find_tools

# Design values for the resin this project prints in (Formlabs TDS FLAPFA01 V1, default post-cure).
MATERIALS = {
    "alpha_fast": {"label": "Formlabs Alpha Fast Resin", "modulus_pa": 2.23e9, "poisson": 0.35,
                   "tensile_strength_pa": 46e6, "density_g_cm3": 1.22, "min_wall_mm": 1.0,
                   "source": "Formlabs Alpha Fast TDS FLAPFA01 V1: 2.23 GPa tensile modulus, 46 MPa tensile strength "
                             "(default post-cure), 1.22 g/cm3. Minimum wall 1.0 mm is a conservative design rule."},
}
# Assumed printable envelope; PreForm checks the real fit when the part is prepared.
BUILD_VOLUME_MM = (200.0, 125.0, 210.0)
Expr = float | str


class Param(Record):
    value: float
    min: float
    max: float
    role: Literal["thickness", "size", "position"] = "size"
    note: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def ordered(self):
        if not self.min <= self.value <= self.max or self.min >= self.max:
            raise ValueError("Each parameter needs min < max and min <= value <= max")
        return self


class Rotation(Record):
    axis: Literal["x", "y", "z"]
    degrees: Expr


class Leg(Record):
    axis: Literal["x", "y", "z"]
    length: Expr  # signed: a negative length runs the negative way from `at`


class Feature(Record):
    op: Literal["add", "subtract"] = "add"
    shape: Literal["box", "cylinder", "wedge"]
    at: tuple[Expr, Expr, Expr]
    size: tuple[Expr, Expr, Expr] | None = None  # box: dx, dy, dz from `at`
    axis: Literal["x", "y", "z"] = "z"            # cylinder: base centre at `at`
    radius: Expr | None = None
    length: Expr | None = None
    rotate: Rotation | None = None                # about `at`
    # wedge: a right-triangle prism for gussets and ribs. `at` is the right-angle corner, the two legs run
    # along two different axes, and `thickness` runs along the third axis (signed, like the legs).
    legs: tuple[Leg, Leg] | None = None
    thickness: Expr | None = None
    label: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def wedge_shape(self):
        if self.shape == "wedge" and (self.legs is None or self.thickness is None or self.legs[0].axis == self.legs[1].axis):
            raise ValueError("A wedge needs two legs along different axes and a thickness")
        return self


class Region(Record):
    name: str = Field(min_length=1, max_length=80)
    min: tuple[Expr, Expr, Expr]
    max: tuple[Expr, Expr, Expr]


class Load(Region):
    force_n: tuple[float, float, float]


class Requirements(Record):
    max_displacement_mm: float = Field(gt=0)
    safety_factor: float = Field(default=2.0, ge=1)
    max_mass_g: float | None = Field(default=None, gt=0)


class DesignSpec(Record):
    name: str = Field(min_length=1, max_length=80)
    summary: str = Field(default="", max_length=600)
    parameters: dict[str, Param] = Field(min_length=1, max_length=24)
    features: list[Feature] = Field(min_length=1, max_length=30)
    supports: list[Region] = Field(min_length=1, max_length=4)
    loads: list[Load] = Field(min_length=1, max_length=4)
    requirements: Requirements

    @model_validator(mode="after")
    def names(self):
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", n) for n in self.parameters):
            raise ValueError("Parameter names must be lower_snake_case")
        if self.features[0].op != "add":
            raise ValueError("The first feature must add material")
        return self


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def evaluate(expr: Expr, values: dict[str, float]) -> float:
    """Arithmetic over parameter names only: + - * /, parentheses, min(), max()."""
    if isinstance(expr, (int, float)):
        return float(expr)

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise ValueError(f"Unknown parameter '{node.id}' in '{expr}'")
            return values[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -walk(node.operand) if isinstance(node.op, ast.USub) else walk(node.operand)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("min", "max") and node.args:
            return (min if node.func.id == "min" else max)(walk(a) for a in node.args)
        raise ValueError(f"Unsupported expression '{expr}'")
    try:
        return float(walk(ast.parse(str(expr), mode="eval")))
    except (SyntaxError, ZeroDivisionError) as exc:
        raise ValueError(f"Bad expression '{expr}': {exc}") from None


def values_of(spec: DesignSpec, overrides: dict[str, float] | None = None) -> dict[str, float]:
    values = {n: p.value for n, p in spec.parameters.items()}
    for name, value in (overrides or {}).items():
        if name not in spec.parameters:
            raise ValueError(f"Unknown parameter '{name}'")
        p = spec.parameters[name]
        if not p.min - 1e-9 <= value <= p.max + 1e-9:
            raise ValueError(f"{name}={value:g} is outside its allowed range {p.min:g}–{p.max:g}")
        values[name] = float(value)
    return values


def _vec(items, values):
    return [evaluate(v, values) for v in items]


def geometry_script(spec: DesignSpec, values: dict[str, float]) -> str:
    """The .geo program that builds the solid; raises ValueError for impossible dimensions."""
    out = ['SetFactory("OpenCASCADE");', 'Geometry.OCCTargetUnit = "MM";']
    for k, f in enumerate(spec.features, start=1):
        x, y, z = _vec(f.at, values)
        if f.shape == "wedge":
            corners = _wedge_triangle(f, values)
            t_axis, t = _wedge_thickness(f, values)
            if abs(t) <= 0 or any(abs(evaluate(leg.length, values)) <= 0 for leg in f.legs):
                raise ValueError(f"Feature {k} ({f.label or 'wedge'}) has a zero leg or thickness")
            ext = [0.0, 0.0, 0.0]
            ext[t_axis] = t
            for n, p in enumerate(corners, start=1):
                out.append(f"Point({1000 * k + n}) = {{{p[0]:.6g},{p[1]:.6g},{p[2]:.6g}}};")
            out += [f"Line({1000 * k + 1}) = {{{1000 * k + 1},{1000 * k + 2}}};", f"Line({1000 * k + 2}) = {{{1000 * k + 2},{1000 * k + 3}}};",
                    f"Line({1000 * k + 3}) = {{{1000 * k + 3},{1000 * k + 1}}};",
                    f"Curve Loop({1000 * k}) = {{{1000 * k + 1},{1000 * k + 2},{1000 * k + 3}}};", f"Plane Surface({1000 * k}) = {{{1000 * k}}};",
                    f"ex{k}[] = Extrude {{{ext[0]:.6g},{ext[1]:.6g},{ext[2]:.6g}}} {{ Surface{{{1000 * k}}}; }};", f"fv{k} = ex{k}[1];"]
        elif f.shape == "box":
            if f.size is None:
                raise ValueError(f"Feature {k} ({f.label or 'box'}) needs a size")
            dx, dy, dz = _vec(f.size, values)
            if min(dx, dy, dz) <= 0:
                raise ValueError(f"Feature {k} ({f.label or 'box'}) has a non-positive size {dx:g} × {dy:g} × {dz:g} mm")
            out.append(f"Box({k}) = {{{x:.6g},{y:.6g},{z:.6g}, {dx:.6g},{dy:.6g},{dz:.6g}}};")
            out.append(f"fv{k} = {k};")
        else:
            if f.radius is None or f.length is None:
                raise ValueError(f"Feature {k} ({f.label or 'cylinder'}) needs a radius and a length")
            r, length = evaluate(f.radius, values), evaluate(f.length, values)
            if r <= 0 or length <= 0:
                raise ValueError(f"Feature {k} ({f.label or 'cylinder'}) has a non-positive radius or length")
            ax = {"x": (length, 0, 0), "y": (0, length, 0), "z": (0, 0, length)}[f.axis]
            out.append(f"Cylinder({k}) = {{{x:.6g},{y:.6g},{z:.6g}, {ax[0]:.6g},{ax[1]:.6g},{ax[2]:.6g}, {r:.6g}}};")
            out.append(f"fv{k} = {k};")
        if f.rotate is not None:
            axis = {"x": "1,0,0", "y": "0,1,0", "z": "0,0,1"}[f.rotate.axis]
            angle = math.radians(evaluate(f.rotate.degrees, values))
            out.append(f"Rotate {{{{{axis}}},{{{x:.6g},{y:.6g},{z:.6g}}},{angle:.8g}}} {{ Volume{{fv{k}}}; }}")
    out.append("part() = {fv1};")
    for k, f in enumerate(spec.features[1:], start=2):
        verb = "BooleanUnion" if f.op == "add" else "BooleanDifference"
        out.append(f"part() = {verb}{{ Volume{{part()}}; Delete; }}{{ Volume{{fv{k}}}; Delete; }};")
    out.append('Printf("SOLIDS %g", #part());')
    return "\n".join(out) + "\n"


class BuildError(ValueError):
    """The spec does not build into exactly one printable solid; the message says why."""


def _run(tools, args, cwd):
    done = subprocess.run([tools["gmsh"], *args], cwd=cwd, capture_output=True, text=True, timeout=300)
    text = (done.stdout or "") + (done.stderr or "")
    errors = [line for line in text.splitlines() if "Error" in line]
    if done.returncode or errors:
        raise BuildError("The CAD kernel rejected the geometry: " + re.sub(r"\x1b\[[0-9;]*m", "", " ".join(errors))[:300])
    return text


def build(spec: DesignSpec, values: dict[str, float], folder: Path) -> dict:
    """Build the solid; write part.step and part.stl. Exactly one connected solid is required."""
    tools = find_tools()
    if tools is None:
        raise RuntimeError("Gmsh and CalculiX not found; install FreeCAD 1.x or set GMSH_EXE and CCX_EXE")
    folder.mkdir(parents=True, exist_ok=True)
    script = geometry_script(spec, values)
    (folder / "part.geo").write_text(script + 'Save "part.step";\n', encoding="utf-8")
    text = _run(tools, ["part.geo", "-0", "-v", "4"], folder)
    found = re.search(r"SOLIDS (\d+)", text)
    solids = int(found.group(1)) if found else 0
    if solids != 1:
        raise BuildError(f"The features form {solids} separate solids; every added piece must overlap or touch the rest of the part")
    # Printable surface for PreForm: curvature-aware triangles, millimetres.
    (folder / "stl.geo").write_text(script + "Mesh.MeshSizeFromCurvature = 24;\nMesh.CharacteristicLengthMax = 2;\nMesh.Binary = 1;\n", encoding="utf-8")
    _run(tools, ["stl.geo", "-2", "-format", "stl", "-o", "part.stl", "-v", "2"], folder)
    report = repair_stl(folder / "part.stl")
    (folder / "stl_report.json").write_text(json.dumps(report), encoding="utf-8")
    return {"step": "part.step", "stl": "part.stl"}


def repair_stl(path: Path) -> dict:
    """Make an STL print-ready: consistent winding, outer skin facing out, cavities facing in, true normals.

    Gmsh orients each surface patch on its own; after booleans some patches come out inside-out, which
    slicers show as broken or inverted faces. Also reports sealed internal cavities (resin traps).
    """
    import struct
    from collections import defaultdict
    raw = path.read_bytes()
    count = struct.unpack("<I", raw[80:84])[0]
    tris = [list(struct.unpack("<9f", raw[84 + 50 * i + 12:84 + 50 * i + 48])) for i in range(count)]
    key = lambda t, k: (round(t[3 * k], 4), round(t[3 * k + 1], 4), round(t[3 * k + 2], 4))  # noqa: E731
    corners = [[key(t, 0), key(t, 1), key(t, 2)] for t in tris]
    by_edge = defaultdict(list)
    for i, (a, b, c) in enumerate(corners):
        for e in ((a, b), (b, c), (c, a)):
            by_edge[frozenset(e)].append(i)
    flip, shell = [None] * count, [-1] * count
    shells, flipped = 0, 0
    for start in range(count):
        if flip[start] is not None:
            continue
        flip[start], shell[start], stack = False, shells, [start]
        while stack:
            i = stack.pop()
            a, b, c = corners[i]
            mine = ((a, b), (b, c), (c, a)) if not flip[i] else ((b, a), (c, b), (a, c))
            for e in mine:
                for j in by_edge[frozenset(e)]:
                    if flip[j] is None:
                        x, y, z = corners[j]
                        # Neighbours must traverse a shared edge in the opposite direction.
                        flip[j] = e in ((x, y), (y, z), (z, x))
                        shell[j] = shells
                        stack.append(j)
        shells += 1

    def oriented(i):
        t = tris[i]
        return (t[0:3], t[6:9], t[3:6]) if flip[i] else (t[0:3], t[3:6], t[6:9])

    def signed(i):
        a, b, c = oriented(i)
        return (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
    volumes = defaultdict(float)
    for i in range(count):
        volumes[shell[i]] += signed(i)
    outer = max(volumes, key=lambda s: abs(volumes[s]))
    # Outer skin encloses positive volume; every other shell is a cavity and must face inward.
    reverse = {s: (v < 0) if s == outer else (v > 0) for s, v in volumes.items()}
    out = bytearray(b"ScienceClaw repaired STL (mm)".ljust(80, b" ") + struct.pack("<I", count))
    for i in range(count):
        a, b, c = oriented(i)
        if reverse[shell[i]]:
            b, c = c, b
        if flip[i] != reverse[shell[i]]:
            flipped += 1
        u = [b[k] - a[k] for k in range(3)]
        v = [c[k] - a[k] for k in range(3)]
        n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
        length = math.sqrt(sum(x * x for x in n)) or 1.0
        out += struct.pack("<12fH", *(x / length for x in n), *a, *b, *c, 0)
    path.write_bytes(bytes(out))
    cavities = [round(-abs(volumes[s]), 1) for s in volumes if s != outer]
    boxes = []
    for s in volumes:
        if s != outer:
            pts = [p for i in range(count) if shell[i] == s for p in corners[i]]
            boxes.append({"min_mm": [min(p[k] for p in pts) for k in range(3)], "max_mm": [max(p[k] for p in pts) for k in range(3)]})
    bad = [e for e, tris_on_edge in by_edge.items() if len(tris_on_edge) != 2]
    open_edges = len(bad)
    bad_pts = [p for e in bad for p in e]
    bad_box = {"min_mm": [min(p[k] for p in bad_pts) for k in range(3)], "max_mm": [max(p[k] for p in bad_pts) for k in range(3)]} if bad_pts else None
    return {"triangles": count, "shells": shells, "cavity_volumes_mm3": cavities, "cavity_boxes": boxes,
            "triangles_flipped": flipped, "open_or_nonmanifold_edges": open_edges, "bad_edge_box": bad_box,
            "volume_mm3": volumes[outer] + sum(cavities)}


def edge_culprits(spec: DesignSpec, values: dict[str, float], box: dict) -> list[int]:
    """Features whose bounds reach the region of bad (knife or open) edges."""
    found = []
    for n, f in enumerate(spec.features):
        lo, hi = feature_bounds(f, values)
        if all(lo[k] <= box["max_mm"][k] + 0.05 and hi[k] >= box["min_mm"][k] - 0.05 for k in range(3)):
            found.append(n)
    return found


def edge_message(spec: DesignSpec, values: dict[str, float], report: dict) -> str:
    b = report["bad_edge_box"]
    names = [f"'{spec.features[n].label or 'feature ' + str(n + 1)}' ({spec.features[n].op})" for n in edge_culprits(spec, values, b)]
    return (f"{report['open_or_nonmanifold_edges']} knife-edge or open edge(s) at x {b['min_mm'][0]:.1f}-{b['max_mm'][0]:.1f}, "
            f"y {b['min_mm'][1]:.1f}-{b['max_mm'][1]:.1f}, z {b['min_mm'][2]:.1f}-{b['max_mm'][2]:.1f} mm: two parts of the solid touch "
            f"only along a line, which is weak and cannot print. Features there: {', '.join(names) or 'unknown'}. Overlap the pieces "
            "by at least 0.5 mm, or move or remove the cut that leaves them joined only at an edge. Solid parts need no drain slots.")


def _wedge_triangle(f: Feature, values: dict[str, float]) -> list[list[float]]:
    at = _vec(f.at, values)
    corners = [list(at)]
    for leg in f.legs:
        p = list(at)
        p["xyz".index(leg.axis)] += evaluate(leg.length, values)
        corners.append(p)
    return corners


def _wedge_thickness(f: Feature, values: dict[str, float]) -> tuple[int, float]:
    axis = ({0, 1, 2} - {"xyz".index(leg.axis) for leg in f.legs}).pop()
    return axis, evaluate(f.thickness, values)


def feature_box(f: Feature, values: dict[str, float]) -> tuple[list[float], list[float]]:
    """Axis-aligned bounds of one feature (rotation ignored: used only to name likely culprits)."""
    at = _vec(f.at, values)
    if f.shape == "wedge":
        tri = _wedge_triangle(f, values)
        axis, t = _wedge_thickness(f, values)
        pts = tri + [[p[k] + (t if k == axis else 0) for k in range(3)] for p in tri]
        return [min(p[k] for p in pts) for k in range(3)], [max(p[k] for p in pts) for k in range(3)]
    if f.shape == "box":
        size = _vec(f.size, values)
        return at, [at[k] + size[k] for k in range(3)]
    r, length = evaluate(f.radius, values), evaluate(f.length, values)
    axis = "xyz".index(f.axis)
    lo = [at[k] - r if k != axis else at[k] for k in range(3)]
    hi = [at[k] + r if k != axis else at[k] + length for k in range(3)]
    return lo, hi


def feature_bounds(f: Feature, values: dict[str, float]) -> tuple[list[float], list[float]]:
    """Axis-aligned bounds of a feature where it really ends up, rotation included."""
    lo, hi = feature_box(f, values)
    if f.rotate is None:
        return lo, hi
    at = _vec(f.at, values)
    a = math.radians(evaluate(f.rotate.degrees, values))
    c, s = math.cos(a), math.sin(a)
    i, j = {"x": (1, 2), "y": (2, 0), "z": (0, 1)}[f.rotate.axis]  # right-handed rotation in the (i, j) plane
    corners = []
    for mask in range(8):
        p = [hi[k] if mask >> k & 1 else lo[k] for k in range(3)]
        u, v = p[i] - at[i], p[j] - at[j]
        p[i], p[j] = at[i] + c * u - s * v, at[j] + s * u + c * v
        corners.append(p)
    return [min(p[k] for p in corners) for k in range(3)], [max(p[k] for p in corners) for k in range(3)]


def mount_problems(spec: DesignSpec, values: dict[str, float], part_lo, part_hi, supports) -> list[str]:
    """A held face (a thin support slab) must be an outer face: nothing may pass through the wall or desk."""
    problems = []
    for name, lo, hi in supports:
        thin = [k for k in range(3) if hi[k] - lo[k] < 0.5]
        for k in thin:
            plane = (lo[k] + hi[k]) / 2
            below, above = plane - part_lo[k], part_hi[k] - plane
            if min(below, above) <= 0.5:
                continue  # the slab sits on an outer face of the part, as it should
            # Material on both sides: the side with less of the part is the wall/desk side.
            outside_low = below < above
            culprits = []
            for n, f in enumerate(spec.features):
                if f.op != "add":
                    continue
                flo, fhi = feature_bounds(f, values)
                if (outside_low and flo[k] < plane - 0.5) or (not outside_low and fhi[k] > plane + 0.5):
                    culprits.append(f"'{f.label or 'feature ' + str(n + 1)}' (reaches {'xyz'[k]} = {flo[k] if outside_low else fhi[k]:.1f} mm)")
            depth = below if outside_low else above
            problems.append(f"The part passes {depth:.1f} mm through the held face '{name}' at {'xyz'[k]} = {plane:.1f} mm, so it "
                            f"cannot sit flat against what holds it. Culprit(s): {', '.join(culprits) or 'unknown'}. Keep every "
                            f"feature on the part side of {'xyz'[k]} = {plane:.1f} mm (check rotations: they turn about `at`).")
    return problems


def alignment_problems(spec: DesignSpec, values: dict[str, float]) -> list[str]:
    """Joined pieces of the same width along an axis that are shifted against each other.

    Pieces that join end to end (an arm leaving a plate) differ in length along that axis and are not
    flagged; a 25 mm curl on a 25 mm arm but offset by 12.5 mm almost certainly is a mistake.
    """
    added = [(n, f, *feature_bounds(f, values)) for n, f in enumerate(spec.features) if f.op == "add"]
    problems = []
    for a in range(len(added)):
        for b in range(a + 1, len(added)):
            (na, fa, la, ha), (nb, fb, lb, hb) = added[a], added[b]
            if any(ha[k] < lb[k] - 0.01 or hb[k] < la[k] - 0.01 for k in range(3)):
                continue  # these two pieces do not touch
            for k in range(3):
                wa, wb = ha[k] - la[k], hb[k] - lb[k]
                shift = abs(la[k] - lb[k])
                if min(wa, wb) > 2 and abs(wa - wb) <= 0.05 * max(wa, wb) and 1.0 < shift < 0.95 * wa:
                    problems.append(f"'{fb.label or 'feature ' + str(nb + 1)}' ({'xyz'[k]} {lb[k]:.1f} to {hb[k]:.1f} mm) has the same "
                                    f"{wb:.1f} mm width as '{fa.label or 'feature ' + str(na + 1)}' ({'xyz'[k]} {la[k]:.1f} to {ha[k]:.1f} mm) "
                                    f"but is shifted {shift:.1f} mm, so it sticks out to one side. Line them up (same {'xyz'[k]} start) "
                                    "unless the offset is intended.")
    return problems


def geometry_fixes(spec: DesignSpec, values: dict[str, float]) -> tuple[dict, list[str]]:
    """Exact fixes for the two placement mistakes code can measure: a piece through a held face
    (slide it out by the overlap) and a same-width piece shifted sideways (line it up).

    Returns the corrected raw spec and one note per change. Positions stay parametric: the shift is
    added to the existing `at` expression.
    """
    raw = spec.model_dump(mode="json")
    notes = []
    shift = lambda n, k, d: raw["features"][n]["at"].__setitem__(k, f"({raw['features'][n]['at'][k]}) + ({d:.3f})")  # noqa: E731
    added = [(n, f, *feature_bounds(f, values)) for n, f in enumerate(spec.features) if f.op == "add"]
    part_lo = [min(lo[k] for _, _, lo, _ in added) for k in range(3)]
    part_hi = [max(hi[k] for _, _, _, hi in added) for k in range(3)]
    for r in spec.supports:
        lo, hi = _vec(r.min, values), _vec(r.max, values)
        for k in (k for k in range(3) if hi[k] - lo[k] < 0.5):
            plane = (lo[k] + hi[k]) / 2
            below, above = plane - part_lo[k], part_hi[k] - plane
            if min(below, above) <= 0.5:
                continue
            outside_low = below < above
            for n, f, flo, fhi in added:
                depth = plane - flo[k] if outside_low else fhi[k] - plane
                if depth > 0.5:
                    d = depth if outside_low else -depth
                    shift(n, k, d)
                    notes.append(f"Moved '{f.label or 'feature ' + str(n + 1)}' {abs(d):.1f} mm along {'xyz'[k]} so it no longer "
                                 f"passes through the held face '{r.name}'.")
    moved = set()
    for a in range(len(added)):
        for b in range(a + 1, len(added)):
            (na, fa, la, ha), (nb, fb, lb, hb) = added[a], added[b]
            if nb in moved or any(ha[k] < lb[k] - 0.01 or hb[k] < la[k] - 0.01 for k in range(3)):
                continue
            for k in range(3):
                wa, wb = ha[k] - la[k], hb[k] - lb[k]
                gap = la[k] - lb[k]
                if min(wa, wb) > 2 and abs(wa - wb) <= 0.05 * max(wa, wb) and 1.0 < abs(gap) < 0.95 * wa:
                    shift(nb, k, gap)
                    moved.add(nb)
                    notes.append(f"Lined '{fb.label or 'feature ' + str(nb + 1)}' up with '{fa.label or 'feature ' + str(na + 1)}' "
                                 f"along {'xyz'[k]} (it was shifted {abs(gap):.1f} mm to one side).")
    return raw, notes


def cavity_culprits(spec: DesignSpec, values: dict[str, float], report: dict) -> list[int]:
    """Indices of subtracted features that overlap a sealed cavity."""
    found = []
    for k, f in enumerate(spec.features):
        if f.op != "subtract":
            continue
        lo, hi = feature_box(f, values)
        for b in report["cavity_boxes"]:
            if all(lo[i] < b["max_mm"][i] + 0.01 and hi[i] > b["min_mm"][i] - 0.01 for i in range(3)):
                found.append(k)
                break
    return found


def cavity_message(report: dict, spec: DesignSpec | None = None, values: dict | None = None, extents=None) -> str:
    where = "; ".join("x {:.1f}-{:.1f}, y {:.1f}-{:.1f}, z {:.1f}-{:.1f} mm".format(
        b["min_mm"][0], b["max_mm"][0], b["min_mm"][1], b["max_mm"][1], b["min_mm"][2], b["max_mm"][2]) for b in report["cavity_boxes"])
    text = (f"{len(report['cavity_boxes'])} sealed internal cavity(ies) at {where}: a pocket or hole does not reach the outside, "
            "so liquid resin would be trapped.")
    if spec is not None and values is not None and extents is not None:
        part_lo, part_hi = extents
        for k in cavity_culprits(spec, values, report):
            f = spec.features[k]
            lo, hi = feature_box(f, values)
            # Suggest the shortest way out: the nearest outer face of the part.
            gaps = [(lo[i] - part_lo[i], i, "min") for i in range(3)] + [(part_hi[i] - hi[i], i, "max") for i in range(3)]
            gap, axis, side = min(gaps)
            target = part_lo[axis] - 1 if side == "min" else part_hi[axis] + 1
            text += (f" Culprit: subtracted {f.shape} '{f.label or 'feature ' + str(k + 1)}' (feature {k + 1}, spans "
                     f"{'xyz'[axis]} {lo[axis]:.1f}-{hi[axis]:.1f} mm) is enclosed; extend it {gap:.1f} mm so it reaches "
                     f"{'xyz'[axis]} {'<=' if side == 'min' else '>='} {target:.1f} mm (the nearest outer face), or delete it.")
    return text + ("" if "Culprit" in text else " Extend the cut through the outer surface or remove it.")


# ------------------------------------------------------------------------------- analysis
def _read_frd(text: str) -> dict[str, dict[int, list[float]]]:
    blocks, name, current = {}, None, None
    for line in text.splitlines():
        if line.startswith(" -4"):
            name = line.split()[1]
            current = blocks.setdefault(name, {})
        elif line.startswith(" -1") and current is not None:
            node = int(line[3:13])
            body = line[13:]
            current[node] = [float(body[i:i + 12]) for i in range(0, len(body.rstrip()), 12)]
        elif line.startswith(" -3"):
            current = None
    return blocks


def _von_mises(s):
    sx, sy, sz, sxy, syz, szx = s[:6]
    return math.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2) + 3 * (sxy ** 2 + syz ** 2 + szx ** 2))


def _inside(p, lo, hi, tol):
    return all(lo[i] - tol <= p[i] <= hi[i] + tol for i in range(3))


def analyze(spec: DesignSpec, values: dict[str, float], folder: Path, *, material: str = "alpha_fast",
            modulus_scale: float = 1.0, mesh_factor: float = 1.0) -> dict:
    """Build, mesh and solve; returns results, requirement and printability checks, and a 3D field."""
    from .fea import _read_mesh
    started = time.monotonic()
    tools = find_tools()
    mat = MATERIALS[material]
    files = build(spec, values, folder)
    thickness = [v for n, v in values.items() if spec.parameters[n].role == "thickness"] or [3.0]
    mesh_mm = max(0.8, min(3.0, mesh_factor * min(thickness)))
    geo = geometry_script(spec, values) + (f'Physical Volume("BODY") = {{part()}};\nMesh.CharacteristicLengthMax = {mesh_mm:.4g};\n'
                                           "Mesh.ElementOrder = 2;\nMesh.SecondOrderLinear = 1;\n")
    (folder / "fea.geo").write_text(geo, encoding="utf-8")
    _run(tools, ["fea.geo", "-3", "-format", "inp", "-o", "mesh.inp", "-v", "2"], folder)
    nodes, groups = _read_mesh((folder / "mesh.inp").read_text(encoding="utf-8"))
    body = groups.get("BODY") or set()
    tol = max(0.05, mesh_mm * 0.05)
    regions = lambda items: [(r, _vec(r.min, values), _vec(r.max, values)) for r in items]  # noqa: E731
    fixed = sorted({i for _, lo, hi in regions(spec.supports) for i in body if _inside(nodes[i], lo, hi, tol)})
    loads = [(r, sorted(i for i in body if _inside(nodes[i], lo, hi, tol))) for r, lo, hi in regions(spec.loads)]
    problems = [f"Support region '{r.name}' does not touch the part" for r, lo, hi in regions(spec.supports)
                if not any(_inside(nodes[i], lo, hi, tol) for i in body)]
    problems += [f"Load region '{r.name}' does not touch the part" for r, ids in loads if not ids]
    if problems:
        raise BuildError("; ".join(problems))
    modulus = mat["modulus_pa"] * modulus_scale / 1e6
    deck = ["*INCLUDE, INPUT=mesh.inp\n", "*NSET, NSET=FIXED\n",
            "\n".join(",".join(map(str, fixed[k:k + 16])) for k in range(0, len(fixed), 16)) + "\n",
            f"*MATERIAL, NAME=RESIN\n*ELASTIC\n{modulus:.6g}, {mat['poisson']}\n*SOLID SECTION, ELSET=BODY, MATERIAL=RESIN\n",
            "*BOUNDARY\nFIXED,1,3\n*STEP\n*STATIC\n*CLOAD\n"]
    for r, ids in loads:
        for axis, total in enumerate(r.force_n, start=1):
            if total:
                deck.append("".join(f"{i},{axis},{total / len(ids):.10g}\n" for i in ids))
    deck.append("*NODE FILE\nU\n*EL FILE\nS\n*END STEP\n")
    (folder / "job.inp").write_text("".join(deck), encoding="utf-8")
    solved = subprocess.run([tools["ccx"], "-i", "job"], cwd=folder, capture_output=True, text=True, timeout=900)
    frd = folder / "job.frd"
    if solved.returncode or not frd.is_file():
        raise RuntimeError("CalculiX failed: " + solved.stdout[-300:])
    blocks = _read_frd(frd.read_text(encoding="utf-8", errors="replace"))
    disp, stress = blocks.get("DISP", {}), blocks.get("STRESS", {})
    vm = {n: _von_mises(s) for n, s in stress.items()}
    # Element volumes from corner nodes give the solid's volume (quadratic edges are straight here).
    elements = []
    for m in re.finditer(r"\*ELEMENT, type=C3D10, ELSET=\w+\n(.*?)(?=\n\*|\Z)", (folder / "mesh.inp").read_text(encoding="utf-8"), re.S | re.I):
        for line in m.group(1).splitlines():
            ids = [int(v) for v in line.split(",") if v.strip()]
            if len(ids) >= 5:
                elements.append(ids[1:5])
    volume = sum(abs(_det(nodes[a], nodes[b], nodes[c], nodes[d])) / 6 for a, b, c, d in elements)
    load_results = []
    for r, ids in loads:
        f = r.force_n
        norm = math.sqrt(sum(c * c for c in f)) or 1.0
        along = sum(sum(disp.get(i, [0, 0, 0])[k] * f[k] / norm for k in range(3)) for i in ids) / len(ids)
        load_results.append({"name": r.name, "force_n": list(f), "deflection_mm": along, "nodes": len(ids)})
    # Peak stress away from the idealised fixed boundary, where point singularities live.
    supports = regions(spec.supports)
    near_fixed = {i for i in body if any(_inside(nodes[i], lo, hi, tol + mesh_mm) for _, lo, hi in supports)}
    ranked = sorted((v for n, v in vm.items() if n not in near_fixed), reverse=True)
    peak = ranked[min(len(ranked) - 1, max(0, len(ranked) // 200))] if ranked else 0.0
    peak_node = max((n for n in vm if n not in near_fixed), key=lambda n: vm[n], default=None)
    req, mass = spec.requirements, volume / 1000 * mat["density_g_cm3"]
    allowable = mat["tensile_strength_pa"] / 1e6 / req.safety_factor
    worst = max(abs(l["deflection_mm"]) for l in load_results)
    xs = [nodes[i] for i in body]
    extents = [max(p[k] for p in xs) - min(p[k] for p in xs) for k in range(3)]
    checks = [
        {"name": "deflection", "label": f"Bend at the load ≤ {req.max_displacement_mm:g} mm", "value": worst,
         "limit": req.max_displacement_mm, "unit": "mm", "passed": worst <= req.max_displacement_mm, "owner": "mechanics"},
        {"name": "stress", "label": f"Peak stress ≤ {allowable:.1f} MPa (strength ÷ safety factor {req.safety_factor:g})",
         "value": peak, "limit": allowable, "unit": "MPa", "passed": peak <= allowable, "owner": "mechanics"},
    ]
    if req.max_mass_g:
        checks.append({"name": "mass", "label": f"Mass ≤ {req.max_mass_g:g} g", "value": mass, "limit": req.max_mass_g,
                       "unit": "g", "passed": mass <= req.max_mass_g, "owner": "fabrication"})
    thin = {n: v for n, v in values.items() if spec.parameters[n].role == "thickness" and v < mat["min_wall_mm"]}
    checks.append({"name": "min_wall", "label": f"Walls ≥ {mat['min_wall_mm']:g} mm (printable)", "value": min(thickness),
                   "limit": mat["min_wall_mm"], "unit": "mm", "passed": not thin, "owner": "fabrication"})
    fits = all(a <= b for a, b in zip(sorted(extents), sorted(BUILD_VOLUME_MM)))
    stl = json.loads((folder / "stl_report.json").read_text(encoding="utf-8"))
    part_lo, part_hi = [min(p[k] for p in xs) for k in range(3)], [max(p[k] for p in xs) for k in range(3)]
    mounting = mount_problems(spec, values, part_lo, part_hi, [(r.name, lo, hi) for r, lo, hi in supports])
    aligned = alignment_problems(spec, values)
    checks.append({"name": "aligned", "label": "Joined pieces line up (nothing shifted off to one side)",
                   "value": len(aligned), "limit": 0, "unit": "problems", "passed": not aligned, "owner": "fabrication",
                   **({"detail": " ".join(aligned)} if aligned else {})})
    checks.append({"name": "mount_clear", "label": "Held faces are outer faces (nothing passes through the wall or desk)",
                   "value": len(mounting), "limit": 0, "unit": "problems", "passed": not mounting, "owner": "mechanics",
                   **({"detail": " ".join(mounting)} if mounting else {})})
    checks.append({"name": "sealed_cavity", "label": "No sealed internal cavities (they trap liquid resin)",
                   "value": len(stl["cavity_volumes_mm3"]), "limit": 0, "unit": "cavities", "passed": not stl["cavity_volumes_mm3"],
                   "owner": "fabrication", **({"detail": cavity_message(stl, spec, values, (
                       [min(p[k] for p in xs) for k in range(3)], [max(p[k] for p in xs) for k in range(3)]))} if stl["cavity_volumes_mm3"] else {})})
    checks.append({"name": "watertight", "label": "Closed, watertight surface (no knife edges)", "value": stl["open_or_nonmanifold_edges"],
                   "limit": 0, "unit": "bad edges", "passed": stl["open_or_nonmanifold_edges"] == 0, "owner": "fabrication",
                   **({"detail": edge_message(spec, values, stl)} if stl["open_or_nonmanifold_edges"] and stl.get("bad_edge_box") else {})})
    checks.append({"name": "build_volume", "label": "Fits the printer's build volume (any orientation)", "value": max(extents),
                   "limit": max(BUILD_VOLUME_MM), "unit": "mm", "passed": fits, "owner": "fabrication"})
    field = _surface_field(nodes, elements, disp, vm)
    (folder / "field.json").write_text(json.dumps(field), encoding="utf-8")
    return {"files": {**files, "field": "field.json"}, "values": values, "mass_g": mass, "volume_mm3": volume,
            "extents_mm": extents, "loads": load_results, "peak_stress_mpa": peak, "allowable_stress_mpa": allowable,
            "peak_stress_at": list(nodes[peak_node]) if peak_node else None, "max_displacement_mm": max(
                (math.sqrt(sum(c * c for c in d[:3])) for d in disp.values()), default=0.0),
            "regions": {"supports": [{"name": r.name, "min": lo, "max": hi} for r, lo, hi in supports],
                        "loads": [{"name": r.name, "min": lo, "max": hi, "force_n": list(r.force_n)} for r, lo, hi in regions(spec.loads)]},
            "checks": checks, "passed": all(c["passed"] for c in checks), "mesh_mm": mesh_mm, "nodes": len(nodes),
            "modulus_pa": mat["modulus_pa"] * modulus_scale, "seconds": round(time.monotonic() - started, 1),
            "solver": "Gmsh + CalculiX, quadratic tetrahedra, linear elastic"}


def _det(a, b, c, d):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    wx, wy, wz = d[0] - a[0], d[1] - a[1], d[2] - a[2]
    return ux * (vy * wz - vz * wy) - uy * (vx * wz - vz * wx) + uz * (vx * wy - vy * wx)


def _surface_field(nodes, elements, disp, vm, limit: int = 60000) -> dict:
    """Complete outer surface for the viewer; legacy ``limit`` is ignored to avoid cutting holes."""
    faces = {}
    for tet in elements:
        for tri, opposite in (((0, 1, 2), 3), ((0, 1, 3), 2), ((0, 2, 3), 1), ((1, 2, 3), 0)):
            ids = tuple(tet[i] for i in tri)
            key = tuple(sorted(ids))
            if key in faces:
                faces[key] = None
            else:
                faces[key] = (ids, tet[opposite])
    triangles = []
    for value in faces.values():
        if value is None:
            continue
        (a, b, c), o = value
        # Orient outward: the opposite corner must lie behind the face.
        if _det(nodes[a], nodes[b], nodes[c], nodes[o]) > 0:
            b, c = c, b
        triangles.append((a, b, c))
    # Keep the complete closed surface. Slicing at a triangle count cut literal holes in the viewer.
    used = sorted({i for t in triangles for i in t})
    index = {n: k for k, n in enumerate(used)}
    return {"positions": [[round(v, 3) for v in nodes[n]] for n in used],
            "displacement": [[round(v, 6) for v in disp.get(n, [0, 0, 0])[:3]] for n in used],
            "stress": [round(vm.get(n, 0.0), 4) for n in used],
            "triangles": [[index[a], index[b], index[c]] for a, b, c in triangles]}


def scratch() -> Path:
    return Path(tempfile.mkdtemp(prefix="scienceclaw_part_"))
