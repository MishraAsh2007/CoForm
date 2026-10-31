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


class Feature(Record):
    op: Literal["add", "subtract"] = "add"
    shape: Literal["box", "cylinder"]
    at: tuple[Expr, Expr, Expr]
    size: tuple[Expr, Expr, Expr] | None = None  # box: dx, dy, dz from `at`
    axis: Literal["x", "y", "z"] = "z"            # cylinder: base centre at `at`
    radius: Expr | None = None
    length: Expr | None = None
    rotate: Rotation | None = None                # about `at`
    label: str = Field(default="", max_length=80)


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
        if f.shape == "box":
            if f.size is None:
                raise ValueError(f"Feature {k} ({f.label or 'box'}) needs a size")
            dx, dy, dz = _vec(f.size, values)
            if min(dx, dy, dz) <= 0:
                raise ValueError(f"Feature {k} ({f.label or 'box'}) has a non-positive size {dx:g} × {dy:g} × {dz:g} mm")
            out.append(f"Box({k}) = {{{x:.6g},{y:.6g},{z:.6g}, {dx:.6g},{dy:.6g},{dz:.6g}}};")
        else:
            if f.radius is None or f.length is None:
                raise ValueError(f"Feature {k} ({f.label or 'cylinder'}) needs a radius and a length")
            r, length = evaluate(f.radius, values), evaluate(f.length, values)
            if r <= 0 or length <= 0:
                raise ValueError(f"Feature {k} ({f.label or 'cylinder'}) has a non-positive radius or length")
            ax = {"x": (length, 0, 0), "y": (0, length, 0), "z": (0, 0, length)}[f.axis]
            out.append(f"Cylinder({k}) = {{{x:.6g},{y:.6g},{z:.6g}, {ax[0]:.6g},{ax[1]:.6g},{ax[2]:.6g}, {r:.6g}}};")
        if f.rotate is not None:
            axis = {"x": "1,0,0", "y": "0,1,0", "z": "0,0,1"}[f.rotate.axis]
            angle = math.radians(evaluate(f.rotate.degrees, values))
            out.append(f"Rotate {{{{{axis}}},{{{x:.6g},{y:.6g},{z:.6g}}},{angle:.8g}}} {{ Volume{{{k}}}; }}")
    out.append("part() = {1};")
    for k, f in enumerate(spec.features[1:], start=2):
        verb = "BooleanUnion" if f.op == "add" else "BooleanDifference"
        out.append(f"part() = {verb}{{ Volume{{part()}}; Delete; }}{{ Volume{{{k}}}; Delete; }};")
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
    return {"step": "part.step", "stl": "part.stl"}


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
    """Outer triangles (corner nodes) with displacement and von Mises stress, for the 3D viewer."""
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
    triangles = triangles[:limit]
    used = sorted({i for t in triangles for i in t})
    index = {n: k for k, n in enumerate(used)}
    return {"positions": [[round(v, 3) for v in nodes[n]] for n in used],
            "displacement": [[round(v, 6) for v in disp.get(n, [0, 0, 0])[:3]] for n in used],
            "stress": [round(vm.get(n, 0.0), 4) for n in used],
            "triangles": [[index[a], index[b], index[c]] for a, b, c in triangles]}


def scratch() -> Path:
    return Path(tempfile.mkdtemp(prefix="scienceclaw_part_"))
