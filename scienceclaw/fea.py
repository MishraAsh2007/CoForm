"""Virtual test rig: finite-element deflection of the support's actual 3D geometry.

Simulated studies can take their "measurements" from here instead of from the beam
formula the agents use. Gmsh meshes the CAD geometry (a Fusion or FreeCAD STEP when one
is recorded, otherwise the same section generated directly), CalculiX solves 3D linear
elasticity with the clamp modelled as elastic jaw pads, and the resin stiffness is drawn
from its datasheet range. The agents' beam model is therefore checked against an
independent, higher-fidelity model. None of these values is shown to the agents.

All solver units are mm, N and MPa.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .domain import Geometry

# Tensile modulus ranges from the manufacturers' technical data sheets (ASTM D638-14):
# green parts at the low end, fully post-cured at the high end. Poisson ratios are typical
# values for unfilled and glass-filled photopolymers (assumed; not on the data sheets).
MATERIALS = {
    # The resin this project prints in. Formlabs TDS FLAPFA01 V1 (2026-09-28), Form Studio 1, 100 um:
    # 1.67 GPa green, 2.23 GPa default post-cure, 2.54 GPa max-strength post-cure.
    "alpha_fast": {"label": "Formlabs Alpha Fast Resin",
                   "tensile_modulus_pa": (1.67e9, 2.54e9), "poisson": 0.35,
                   "source": "Formlabs Alpha Fast Resin TDS FLAPFA01 V1: 1.67 GPa green, 2.23 default, 2.54 max post-cure"},
    "standard_resin": {"label": "Standard photopolymer (Formlabs Clear/White/Grey/Black V4)",
                       "tensile_modulus_pa": (1.6e9, 2.8e9), "poisson": 0.35,
                       "source": "Formlabs Standard Resins TDS: 1.6 GPa green, 2.8 GPa post-cured"},
    "rigid_10k": {"label": "Formlabs Rigid 10K (glass-filled)",
                  "tensile_modulus_pa": (7.5e9, 10.0e9), "poisson": 0.30,
                  "source": "Formlabs Rigid 10K TDS: 7.5 GPa green, 10 GPa post-cured"},
}
# Clamp jaws are bonded pads whose outer faces are held fixed; mount rotation emerges from
# their stiffness and the clamp length instead of from an assumed compliance number.
FIXTURES = {
    "rigid_vise": {"label": "Steel vise, no pads (clamped length fully fixed)", "pad_modulus_pa": None},
    "printed_jaws": {"label": "Resin-printed jaws, 3 mm", "pad_modulus_pa": 2.2e9, "pad_thickness_m": 0.003, "poisson": 0.35},
    "rubber_lined": {"label": "Rubber-lined clamp, 1.5 mm neoprene (typical 5 MPa)", "pad_modulus_pa": 5e6,
                     "pad_thickness_m": 0.0015, "poisson": 0.45},
}
MESH_MM = 2.0  # Within 1.5% of a 0.8 mm mesh on the default U-channel; below measurement noise.


class GeometryMismatch(ValueError):
    """The CAD STEP is not oriented or sized like the analysed geometry (length X, width Y, height Z)."""


def find_tools() -> dict | None:
    """GMSH_EXE/CCX_EXE, then the copies bundled with FreeCAD, then PATH."""
    gmsh, ccx = os.getenv("GMSH_EXE"), os.getenv("CCX_EXE")
    from .cad import find_freecad_cmd
    freecad = find_freecad_cmd()
    if freecad:
        folder = Path(freecad).parent
        gmsh = gmsh or str(folder / "gmsh.exe")
        ccx = ccx or str(folder / "ccx.exe")
    gmsh = gmsh if gmsh and Path(gmsh).is_file() else shutil.which("gmsh")
    ccx = ccx if ccx and Path(ccx).is_file() else (shutil.which("ccx") or shutil.which("ccx_static"))
    return {"gmsh": gmsh, "ccx": ccx} if gmsh and ccx else None


def draw_modulus(material: str, seed: int, cure_fraction: float | None = None) -> float:
    """One resin batch per study: somewhere between green and fully post-cured."""
    low, high = MATERIALS[material]["tensile_modulus_pa"]
    if cure_fraction is None:
        cure_fraction = random.Random(f"{material}:{seed}").random()
    return low + (high - low) * cure_fraction


def _plane(tag: int, x: float, z0: float, z1: float, y0: float, y1: float) -> str:
    # A rectangle in the YZ plane at x: drawn in XY, rotated about Y, then moved to x.
    return (f"Rectangle({tag}) = {{{z0:.6g},{y0:.6g},0, {z1 - z0:.6g},{y1 - y0:.6g}}};\n"
            f"Rotate {{{{0,1,0}},{{0,0,0}},-Pi/2}} {{ Surface{{{tag}}}; }}\n"
            f"Translate {{{x:.6g},0,0}} {{ Surface{{{tag}}}; }}\n")


def _geo(g: Geometry, fixture: dict, stations_mm: list[float], step: Path | None, mesh_mm: float,
         turn: str = "0") -> tuple[str, dict]:
    L, c, b, t, h = (v * 1000 for v in (g.length_m, g.clamp_length_m, g.base_width_m, g.thickness_m, g.wall_height_m))
    H = h + t  # outside height for every family
    out = ['SetFactory("OpenCASCADE");', 'Geometry.OCCTargetUnit = "MM";']
    if step is not None:
        # Length runs along X in both CAD paths, but the section may be turned about X (Fusion sketches
        # on the YZ plane). Turn it by `turn`, then move it to the origin. One solid, so Volume 1.
        out += [f'Merge "{step.resolve().as_posix()}";', f"Rotate {{{{1,0,0}},{{0,0,0}},{turn}}} {{ Volume{{1}}; }}",
                "bb() = BoundingBox Volume{1};", "Translate {-bb(0), -bb(1), -bb(2)} { Volume{1}; }"]
    elif g.section_family == "u_channel":
        out += [f"Box(1) = {{0,0,0, {L},{b},{t}}};", f"Box(2) = {{0,0,{t}, {L},{t},{h}}};",
                f"Box(3) = {{0,{b - t},{t}, {L},{t},{h}}};",
                "BooleanUnion{ Volume{1}; Delete; }{ Volume{2,3}; Delete; }"]
    elif g.section_family == "solid_bar":
        out.append(f"Box(1) = {{0,0,0, {L},{b},{H}}};")
    else:
        out += [f"Box(1) = {{0,0,0, {L},{b},{H}}};", f"Box(2) = {{0,{t},{t}, {L},{b - 2 * t},{h - t}}};",
                "BooleanDifference{ Volume{1}; Delete; }{ Volume{2}; Delete; }"]
    pad = fixture.get("pad_modulus_pa") is not None
    tp = fixture.get("pad_thickness_m", 0) * 1000
    top_y0, top_w, top_z = (t, b - 2 * t, t) if g.section_family == "u_channel" else (0, b, H)
    if pad:
        out += [f"Box(1001) = {{0,0,{-tp}, {c},{b},{tp}}};", f"Box(1002) = {{0,{top_y0},{top_z}, {c},{top_w},{tp}}};"]
    planes = [c] + [c + s for s in stations_mm]
    for k, x in enumerate(planes):
        out.append(_plane(2001 + k, x, -tp - 2, H + tp + 2, -2, b + 2))
    out.append("BooleanFragments{ Volume{:}; Delete; }{ Surface{" + ",".join(str(2001 + k) for k in range(len(planes))) + "}; Delete; }")
    e = 1e-3
    if pad:
        out += [f"pad() = Volume In BoundingBox{{{-e},{-e},{-tp - e}, {c + e},{b + e},{e}}};",
                f"pad() += Volume In BoundingBox{{{-e},{top_y0 - e},{top_z - e}, {c + e},{top_y0 + top_w + e},{top_z + tp + e}}};",
                "body() = Volume{:};", "body() -= pad();", 'Physical Volume("PAD") = {pad()};']
    else:
        out.append("body() = Volume{:};")
    out += ['Physical Volume("BODY") = {body()};', f"Mesh.CharacteristicLengthMax = {mesh_mm};",
            "Mesh.ElementOrder = 2;", "Mesh.SecondOrderLinear = 1;"]
    return "\n".join(out) + "\n", {"clamp_mm": c, "pad_mm": tp, "top_mm": top_z, "height_mm": H, "length_mm": L, "width_mm": b}


def _read_mesh(text: str) -> tuple[dict, dict]:
    nodes = {}
    block = re.search(r"\*NODE[^\n]*\n(.*?)(?=\n\*)", text, re.S | re.I).group(1)
    for line in block.splitlines():
        parts = line.split(",")
        if len(parts) >= 4:
            nodes[int(parts[0])] = tuple(float(v) for v in parts[1:4])
    # Gmsh writes one element block per entity and an *ELSET of element numbers per physical group.
    elements = {}
    for m in re.finditer(r"\*ELEMENT, type=C3D10, ELSET=\w+\n(.*?)(?=\n\*|\Z)", text, re.S | re.I):
        for line in m.group(1).splitlines():
            values = [int(v) for v in line.split(",") if v.strip()]
            if values:
                elements[values[0]] = values[1:]
    groups = {}
    for m in re.finditer(r"\*ELSET,ELSET=(\w+)\n(.*?)(?=\n\*|\Z)", text, re.S | re.I):
        members = (int(v) for line in m.group(2).splitlines() for v in line.split(",") if v.strip())
        groups[m.group(1).upper()] = {n for e in members for n in elements.get(e, ())}
    return nodes, groups


def _check_orientation(geometry: Geometry, dims: dict, nodes: dict, body: set) -> None:
    """The meshed part must be the analysed one: length X, width Y, height Z, channel opening upward."""
    for axis, name in enumerate(("length_mm", "width_mm", "height_mm")):
        values = [nodes[i][axis] for i in body]
        if abs(max(values) - min(values) - dims[name]) > 0.01 * dims[name] + 0.05:
            raise GeometryMismatch(f"meshed {name[:-3]} {max(values) - min(values):.2f} mm differs from {dims[name]:.2f} mm")
    if geometry.section_family == "u_channel":
        t, b, H = geometry.thickness_m * 1000, dims["width_mm"], dims["height_mm"]
        if any(nodes[i][2] > H - 0.05 and t + 0.5 < nodes[i][1] < b - t - 0.5 for i in body):
            raise GeometryMismatch("channel opens downward; expected walls up")


def _nset(name: str, ids: list[int]) -> str:
    return f"*NSET, NSET={name}\n" + "\n".join(",".join(map(str, ids[k:k + 16])) for k in range(0, len(ids), 16)) + "\n"


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def solve(geometry: Geometry, *, modulus_pa: float, poisson: float, fixture: str, stations_m: list[float],
          step: Path | None = None, cache_dir: Path | None = None, mesh_mm: float = MESH_MM) -> dict:
    """Tip-load compliance (m/N) at each station, measured from the clamp line, from a 3D FE solve."""
    tools = find_tools()
    if tools is None:
        raise RuntimeError("Gmsh and CalculiX not found; install FreeCAD 1.x or set GMSH_EXE and CCX_EXE")
    spec = FIXTURES[fixture]
    stations_mm = sorted({round(s * 1000, 6) for s in stations_m})
    step_hash = hashlib.sha256(step.read_bytes()).hexdigest() if step else None
    key = _key(geometry.model_dump(), modulus_pa, poisson, fixture, stations_mm, step_hash, mesh_mm)
    cached = cache_dir / f"{key}.json" if cache_dir else None
    if cached and cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8"))
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="scienceclaw_fea_") as tmp:
        work = Path(tmp)
        mismatch = None
        for turn in (("0", "Pi/2", "-Pi/2", "Pi") if step else ("0",)):
            geo, dims = _geo(geometry, spec, stations_mm, step, mesh_mm, turn)
            (work / "part.geo").write_text(geo, encoding="utf-8")
            meshed = subprocess.run([tools["gmsh"], "part.geo", "-3", "-format", "inp", "-o", "mesh.inp", "-v", "2"],
                                    cwd=work, capture_output=True, text=True, timeout=300)
            if meshed.returncode or not (work / "mesh.inp").is_file():
                raise RuntimeError("Gmsh failed: " + (meshed.stderr or meshed.stdout)[-400:])
            nodes, groups = _read_mesh((work / "mesh.inp").read_text(encoding="utf-8"))
            body = groups.get("BODY") or set()
            if not body:
                raise RuntimeError("Mesh has no body elements")
            try:
                _check_orientation(geometry, dims, nodes, body)
                break
            except GeometryMismatch as exc:
                mismatch = exc
        else:
            raise mismatch
        tol = 1e-4
        if spec.get("pad_modulus_pa") is not None:
            pad = groups.get("PAD") or set()
            top = dims["top_mm"] + dims["pad_mm"]
            fixed = sorted(i for i in pad if nodes[i][2] <= -dims["pad_mm"] + tol or nodes[i][2] >= top - tol)
        else:
            fixed = sorted(i for i in body if nodes[i][0] <= dims["clamp_mm"] + tol)
        loads = {s: sorted(i for i in body if abs(nodes[i][0] - (dims["clamp_mm"] + s)) < tol) for s in stations_mm}
        if not fixed or not all(loads.values()):
            raise RuntimeError("Could not locate clamp or load-station nodes in the mesh")
        deck = ["*INCLUDE, INPUT=mesh.inp\n", _nset("FIXED", fixed)]
        deck += [_nset(f"S{k}", ids) for k, ids in enumerate(loads.values())]
        deck.append(f"*MATERIAL, NAME=RESIN\n*ELASTIC\n{modulus_pa / 1e6:.6g}, {poisson}\n*SOLID SECTION, ELSET=BODY, MATERIAL=RESIN\n")
        if spec.get("pad_modulus_pa") is not None:
            deck.append(f"*MATERIAL, NAME=JAW\n*ELASTIC\n{spec['pad_modulus_pa'] / 1e6:.6g}, {spec['poisson']}\n"
                        "*SOLID SECTION, ELSET=PAD, MATERIAL=JAW\n")
        deck.append("*BOUNDARY\nFIXED,1,3\n")
        for k, ids in enumerate(loads.values()):
            # A unit vertical load spread over the station's cross-section.
            deck.append("*STEP\n*STATIC\n*CLOAD, OP=NEW\n" + "".join(f"{i},3,{-1.0 / len(ids):.10g}\n" for i in ids)
                        + f"*NODE PRINT, NSET=S{k}\nU\n*END STEP\n")
        (work / "job.inp").write_text("".join(deck), encoding="utf-8")
        solved = subprocess.run([tools["ccx"], "-i", "job"], cwd=work, capture_output=True, text=True, timeout=600,
                                env={**os.environ, "OMP_NUM_THREADS": os.getenv("OMP_NUM_THREADS", "4")})
        dat = work / "job.dat"
        if solved.returncode or not dat.is_file():
            raise RuntimeError("CalculiX failed: " + solved.stdout[-400:])
        compliance = {}
        sections = re.split(r"displacements \(vx,vy,vz\) for set (\w+)", dat.read_text(encoding="utf-8"))
        for name, body_text in zip(sections[1::2], sections[2::2]):
            uz = [float(line.split()[3]) for line in body_text.splitlines() if re.match(r"\s*\d+\s+\S+\s+\S+\s+\S+\s*$", line)]
            compliance[name.upper()] = -sum(uz) / len(uz)
    result = {"compliance_m_per_n": {f"{s / 1000:.6g}": compliance[f"S{k}"] / 1000 for k, s in enumerate(stations_mm)},
              "nodes": len(nodes), "fixed_nodes": len(fixed), "geometry_source": "cad_step" if step else "generated_section",
              "fixture": fixture, "modulus_pa": modulus_pa, "poisson": poisson, "mesh_mm": mesh_mm,
              "solver": "Gmsh + CalculiX (quadratic tetrahedra, linear elastic)", "seconds": round(time.monotonic() - started, 2)}
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(result), encoding="utf-8")
    return result


def compliance_at(solution: dict, position_m: float) -> float:
    try:
        return solution["compliance_m_per_n"][f"{position_m:.6g}"]
    except KeyError:
        raise ValueError(f"No FE solution at {position_m * 1000:g} mm") from None
