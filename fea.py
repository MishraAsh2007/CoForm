# Level 2 structural evaluation: finite element analysis with a mesh-convergence study.
#
# Drives the gmsh.exe and ccx.exe (CalculiX) bundled with FreeCAD directly:
# STEP -> 10-node tetrahedral mesh -> linear static CalculiX deck -> parsed results.
# Each evaluation solves on 3 successively finer meshes and applies the grid
# convergence index procedure of Celik et al. (2008, J. Fluids Eng. 130:078001).
# A quantity is marked converged only if it converges monotonically with a GCI
# below the config threshold; unconverged results are never selection evidence.
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

import config
from schemas import GeometryParams, Quantity, StructuralResult

PLANE_TOL = 1e-4   # mm, for picking nodes on the fixed and loaded planar faces

# C3D10 faces as (corner, corner, corner, mid, mid, mid), 0-based local node indices.
TET10_FACES = [(0, 1, 2, 4, 5, 6), (0, 3, 1, 7, 8, 4), (1, 3, 2, 8, 9, 5), (2, 3, 0, 9, 7, 6)]
# Edge-node k sits between these corners (Abaqus/CalculiX ordering).
TET10_EDGES = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]

ASSUMPTIONS = [
    "linear elastic, isotropic, small-displacement static analysis",
    "wall back face (Y=0) fully fixed",
    "half model by mirror symmetry about X = BRACKET_WIDTH/2 (FEA_HALF_MODEL): half the "
    "load, symmetry plane restrained in X",
    "tip load applied as a uniform downward traction over the arm end face (Y=ARM_LENGTH)",
    "10-node straight-sided quadratic tetrahedra (C3D10) meshed by gmsh, refined on "
    "fillets by curvature (fillets faceted); solved by CalculiX",
    "peak von Mises stress excludes elements within FEA_SINGULARITY_EXCLUSION_MM of the "
    "fixed face, loaded face and sharp rib-to-plate edges, where idealized stresses are "
    "singular; real stress concentrations at the sharp rib edges are NOT resolved",
    "tip deflection = mean vertical displacement of the loaded end face",
]


def _keep_full_speed(process_handle=None):
    """Windows only: opt a process out of power throttling (EcoQoS) and keep the system awake.

    Overnight batches showed ccx alive but nearly idle for 30-45 min while the PC was
    unattended (Balanced power plan). Exempting the solver from execution-speed
    throttling and holding ES_SYSTEM_REQUIRED prevents that. Timing only: results are
    unaffected. Best effort; failures are ignored.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class PowerThrottlingState(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                        ("StateMask", wintypes.ULONG)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                   wintypes.DWORD]
        # ProcessPowerThrottling = 4; control EXECUTION_SPEED | IGNORE_TIMER_RESOLUTION, state 0 = off.
        state = PowerThrottlingState(1, 0x1 | 0x4, 0)
        handle = process_handle if process_handle is not None else kernel32.GetCurrentProcess()
        kernel32.SetProcessInformation(handle, 4, ctypes.byref(state), ctypes.sizeof(state))
        kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    except Exception:
        pass


def _run(cmd, cwd, timeout=600):
    env = {**os.environ, "OMP_NUM_THREADS": str(config.FEA_THREADS)}
    _keep_full_speed()
    with subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, env=env) as proc:
        _keep_full_speed(int(proc._handle) if os.name == "nt" else None)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
    if proc.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed:\n{(stdout + stderr)[-2000:]}")
    return stdout


def mesh(step_path, size, curvature_elements, workdir):
    """Mesh a STEP solid with quadratic tets. Returns (node_ids, coords Nx3, tets Mx10 as row indices).

    size is the maximum element size (mm); curvature_elements is the number of
    elements per full circle on curved surfaces (refines the fillets).
    """
    geo = workdir / "mesh.geo"
    half = ""
    if config.FEA_HALF_MODEL:
        # Keep only X <= BRACKET_WIDTH/2: the bracket, rib and load are mirror-symmetric.
        w, big = config.BRACKET_WIDTH, 2 * max(config.ARM_LENGTH, config.WALL_HEIGHT)
        half = ("v() = Volume{:};\n"
                f"Box(1000) = {{-1, -1, -1, {w / 2 + 1}, {big}, {big}}};\n"
                "BooleanIntersection{ Volume{v()}; Delete; }{ Volume{1000}; Delete; }\n")
    geo.write_text('SetFactory("OpenCASCADE");\n'
                   f'Merge "{Path(step_path).resolve().as_posix()}";\n' + half +
                   f"Mesh.MeshSizeMax = {size};\n"
                   f"Mesh.MeshSizeFromCurvature = {curvature_elements};\n"
                   # Straight-sided quadratic elements: curved ones invert on the
                   # 1 mm fillets at coarse sizes. Fillets are faceted instead.
                   "Mesh.ElementOrder = 2;\nMesh.SecondOrderLinear = 1;\nMesh.Optimize = 1;\n"
                   'Physical Volume("solid") = Volume{:};\n')
    inp = workdir / "mesh.inp"
    _run([config.GMSH_EXE, str(geo), "-3", "-format", "inp", "-o", str(inp),
          "-nt", str(config.FEA_THREADS)], workdir)

    node_rows, elem_rows, mode = [], [], None
    for line in inp.read_text().splitlines():
        if line.startswith("*"):
            upper = line.upper()
            mode = "node" if upper.startswith("*NODE") else "elem" if "C3D10" in upper else None
        elif mode == "node":
            node_rows.append([float(v) for v in line.split(",")[:4]])
        elif mode == "elem":
            elem_rows.append([int(v) for v in line.split(",") if v.strip()][1:])
    nodes = np.array(node_rows)
    node_ids = nodes[:, 0].astype(int)
    coords = nodes[:, 1:4]
    index = {nid: i for i, nid in enumerate(node_ids)}
    tets = np.array([[index[n] for n in row] for row in elem_rows])

    # Verify the edge-node ordering instead of trusting the exporter.
    corners = coords[tets[:, :4]]
    for k, (a, b) in enumerate(TET10_EDGES):
        gap = np.linalg.norm((corners[:, a] + corners[:, b]) / 2 - coords[tets[:, 4 + k]], axis=1)
        edge = np.linalg.norm(corners[:, a] - corners[:, b], axis=1)
        if np.any(gap > 0.25 * edge):
            raise RuntimeError("unexpected C3D10 node ordering in gmsh output")
    return node_ids, coords, tets


def _load_vector(coords, tets, force):
    """Consistent nodal forces for a uniform traction on the arm end face.

    For a 6-node triangle under uniform traction, corner nodes carry 0 and each
    mid-side node carries 1/3 of the face's share.
    """
    on_face = np.abs(coords[:, 1] - config.ARM_LENGTH) < PLANE_TOL
    faces = []
    for face in TET10_FACES:
        tri = tets[:, face]
        hit = on_face[tri[:, :3]].all(axis=1)
        faces.extend(tri[hit])
    if not faces:
        raise RuntimeError("no element faces found on the loaded end face")
    faces = np.array(faces)
    p = coords[faces[:, :3]]
    areas = 0.5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
    traction = force / areas.sum()
    loads = {}
    for face, area in zip(faces, areas):
        for node in face[3:]:
            loads[node] = loads.get(node, 0.0) + traction * area / 3
    return loads, np.flatnonzero(on_face), areas.sum()


def _nset(name, ids):
    rows = [", ".join(map(str, ids[i:i + 10])) for i in range(0, len(ids), 10)]
    return f"*NSET, NSET={name}\n" + "\n".join(rows) + "\n"


def solve(params: GeometryParams, step_path, size, curvature_elements, workdir, exclusion_mm):
    """Mesh and solve once. Returns dict with tip deflection, filtered peak stress and mesh stats.

    exclusion_mm must be the same for every mesh in a convergence study so the
    filtered peak stress always refers to the same physical region.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    node_ids, coords, tets = mesh(step_path, size, curvature_elements, workdir)
    if len(tets) > config.FEA_MAX_ELEMENTS:
        raise RuntimeError(f"mesh has {len(tets)} elements (> FEA_MAX_ELEMENTS)")

    fixed = np.flatnonzero(coords[:, 1] < PLANE_TOL)
    # A half model carries half the load and cannot move across its symmetry plane.
    force = config.TIP_FORCE_N / 2 if config.FEA_HALF_MODEL else config.TIP_FORCE_N
    loads, face_nodes, face_area = _load_vector(coords, tets, force)
    symmetric = np.flatnonzero(np.abs(coords[:, 0] - config.BRACKET_WIDTH / 2) < PLANE_TOL)
    if config.FEA_HALF_MODEL and not len(symmetric):
        raise RuntimeError("no nodes found on the symmetry plane")

    deck = ["*NODE, NSET=NALL"]
    deck += [f"{nid}, {x:.9g}, {y:.9g}, {z:.9g}" for nid, (x, y, z) in zip(node_ids, coords)]
    deck.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    deck += [f"{e + 1}, " + ", ".join(map(str, node_ids[row])) for e, row in enumerate(tets)]
    text = "\n".join(deck) + "\n"
    text += _nset("NFIX", node_ids[fixed]) + _nset("NFACE", node_ids[face_nodes])
    if config.FEA_HALF_MODEL:
        text += _nset("NSYM", node_ids[symmetric]) + "*BOUNDARY\nNSYM, 1, 1\n"
    text += ("*BOUNDARY\nNFIX, 1, 3\n"
             f"*MATERIAL, NAME=RESIN\n*ELASTIC\n{config.YOUNGS_MODULUS_MPA}, {config.POISSON_RATIO}\n"
             "*SOLID SECTION, ELSET=EALL, MATERIAL=RESIN\n*STEP\n"
             "*STATIC, SOLVER=ITERATIVE CHOLESKY\n*CLOAD\n")
    text += "".join(f"{node_ids[n]}, 3, {-f:.9g}\n" for n, f in loads.items())
    text += "*NODE PRINT, NSET=NFACE\nU\n*EL PRINT, ELSET=EALL\nS\n*END STEP\n"
    (workdir / "job.inp").write_text(text)

    out = _run([config.CCX_EXE, "-i", "job"], workdir)
    if "*ERROR" in out:
        raise RuntimeError(f"CalculiX reported an error:\n{out[-2000:]}")
    uz, stress_rows, mode = [], [], None
    for line in (workdir / "job.dat").read_text().splitlines():
        if "displacements" in line:
            mode = "u"
        elif "stresses" in line:
            mode = "s"
        elif line.strip() and mode == "u":
            uz.append(float(line.split()[3]))
        elif line.strip() and mode == "s":
            stress_rows.append([float(v) for v in line.split()])
    if len(uz) != len(face_nodes) or not stress_rows:
        raise RuntimeError("CalculiX output is missing displacements or stresses")

    s = np.array(stress_rows)   # elem, ip, sxx, syy, szz, sxy, sxz, syz
    sxx, syy, szz, sxy, sxz, syz = s[:, 2:8].T
    vm = np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                 + 3 * (sxy**2 + sxz**2 + syz**2))
    elem_vm = np.zeros(len(tets))
    np.maximum.at(elem_vm, s[:, 0].astype(int) - 1, vm)

    centroids = coords[tets[:, :4]].mean(axis=1)
    keep = _away_from_singularities(centroids, params, exclusion_mm)
    peak = int(np.argmax(np.where(keep, elem_vm, -np.inf)))

    for big in ("mesh.inp", "job.inp", "job.dat", "job.frd"):
        (workdir / big).unlink(missing_ok=True)
    return {
        "element_size_mm": size, "curvature_elements": curvature_elements,
        "elements": int(len(tets)), "nodes": int(len(node_ids)),
        "tip_deflection_mm": -float(np.mean(uz)),
        "peak_von_mises_mpa": float(elem_vm[peak]),
        "peak_location_mm": [round(float(v), 2) for v in centroids[peak]],
        "unfiltered_peak_von_mises_mpa": float(elem_vm.max()),
        "loaded_face_area_mm2": float(face_area),
        "seconds": round(time.perf_counter() - started, 2),
    }


def _away_from_singularities(points, params: GeometryParams, e):
    """Mask of points farther than distance e (mm) from idealization singularities."""
    t, h, rt = params.plate_thickness, params.rib_height, params.rib_thickness
    x0, x1 = (config.BRACKET_WIDTH - rt) / 2, (config.BRACKET_WIDTH + rt) / 2
    keep = (points[:, 1] > e) & (points[:, 1] < config.ARM_LENGTH - e)
    if config.RIB_FILLET_RADIUS > 0:
        return keep   # rib junctions are filleted, so they are not singular
    # Sharp re-entrant rib edges, each a straight segment (start, end).
    segments = []
    for x in (x0, x1):
        segments.append(((x, t, t), (x, t + h, t)))   # rib side meets arm top (along Y)
        segments.append(((x, t, t), (x, t, t + h)))   # rib side meets wall face (along Z)
    segments.append(((x0, t + h, t), (x1, t + h, t)))  # rib toe on the arm (along X)
    segments.append(((x0, t, t + h), (x1, t, t + h)))  # rib top on the wall (along X)
    for a, b in segments:
        a, b = np.array(a), np.array(b)
        u = np.clip((points - a) @ (b - a) / np.dot(b - a, b - a), 0.0, 1.0)
        keep &= np.linalg.norm(points - (a + u[:, None] * (b - a)), axis=1) > e
    return keep


def grid_convergence(fine, medium, coarse, n_fine, n_medium, n_coarse, safety=1.25,
                     formal_order=None):
    """Celik et al. (2008) GCI for three unstructured 3D meshes (element counts n_*).

    Non-monotonic sequences are handled as oscillatory convergence (see below).
    The observed order is capped at formal_order (default config.FEA_FORMAL_ORDER)
    when computing the GCI, following Roache: an observed order far above the
    element's theoretical order (e.g. two meshes agreeing by coincidence) would
    otherwise drive the GCI to ~0. Returns dict(order, order_used, extrapolated,
    gci, monotonic); gci is relative to the fine value.
    """
    formal_order = config.FEA_FORMAL_ORDER if formal_order is None else formal_order
    r21 = (n_fine / n_medium) ** (1 / 3)
    r32 = (n_medium / n_coarse) ** (1 / 3)
    e21, e32 = medium - fine, coarse - medium
    if e21 == 0:
        return {"order": None, "order_used": None, "extrapolated": fine, "gci": 0.0,
                "monotonic": True}
    if e32 == 0 or e32 / e21 < 0:
        # Oscillatory (non-monotonic) convergence: no observed order exists, so
        # following Eca & Hoekstra (2014) the uncertainty is a multiple of the
        # spread of the three solutions, reported here in place of the GCI.
        spread = max(fine, medium, coarse) - min(fine, medium, coarse)
        return {"order": None, "order_used": None, "extrapolated": None,
                "gci": config.FEA_OSCILLATORY_FACTOR * spread / abs(fine),
                "monotonic": False, "oscillatory": True}
    s = 1.0  # monotonic
    p = 2.0
    for _ in range(100):
        q = math.log((r21**p - s) / (r32**p - s))
        p_new = abs(math.log(abs(e32 / e21)) + q) / math.log(r21)
        if abs(p_new - p) < 1e-8:
            break
        p = p_new
    rp = r21 ** min(p, formal_order)
    return {"order": p, "order_used": min(p, formal_order),
            "extrapolated": (rp * fine - medium) / (rp - 1),
            "gci": safety * abs(e21 / fine) / (rp - 1), "monotonic": True}


def _converged_quantity(levels, key, unit, threshold):
    """FEA Quantity from a 3-mesh study; uncertainty is max(GCI, FEA_MIN_REL_UNCERTAINTY) x value."""
    fine, medium, coarse = (lv[key] for lv in levels[::-1])
    n = [lv["elements"] for lv in levels[::-1]]
    study = grid_convergence(fine, medium, coarse, *n)
    # Monotonic studies use the GCI; oscillatory ones the spread-based estimate.
    converged = study["gci"] is not None and study["gci"] < threshold
    if study["gci"] is not None:
        uncertainty = abs(fine) * max(study["gci"], config.FEA_MIN_REL_UNCERTAINTY)
    else:
        uncertainty = abs(fine - medium)
    return Quantity(value=fine, unit=unit, source="fea", status="completed",
                    converged=converged, uncertainty=uncertainty), {**study, "threshold": threshold}


def evaluate_l2(params: GeometryParams, step_path, workdir=None):
    """Run the 3-mesh convergence study on the STEP solid built from `params`."""
    coarse = config.FEA_COARSE_SIZE_FACTOR * min(params.plate_thickness, params.rib_thickness)
    sizes = [coarse / config.FEA_REFINEMENT_RATIO**k for k in range(3)]
    curvature = [round(config.FEA_CURVATURE_ELEMENTS * config.FEA_REFINEMENT_RATIO**k)
                 for k in range(3)]
    # Coarse-mesh elements touching a singular edge have centroids up to ~one
    # element size away, so the exclusion must be at least that wide.
    exclusion = max(config.FEA_SINGULARITY_EXCLUSION_MM, coarse)
    load_case = {"tip_force_n": config.TIP_FORCE_N, "direction": "-Z",
                 "fixed": "wall back face (Y=0)", "load": "uniform traction on arm end face"}
    material = {"youngs_modulus_mpa": config.YOUNGS_MODULUS_MPA,
                "poisson_ratio": config.POISSON_RATIO}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(workdir) if workdir else Path(tmp)
        try:
            # Finest first: an oversized mesh then fails in seconds, not after the coarse solves.
            levels = [solve(params, step_path, h, n, root / f"level{k}", exclusion)
                      for k, (h, n) in reversed(list(enumerate(zip(sizes, curvature))))][::-1]
        except Exception as e:
            failed = {"unit": "", "source": "fea", "status": "failed", "error": f"{type(e).__name__}: {e}"}
            return StructuralResult(level=2, load_case=load_case, material=material,
                                    assumptions=ASSUMPTIONS,
                                    tip_deflection=Quantity(**{**failed, "unit": "mm"}),
                                    max_stress=Quantity(**{**failed, "unit": "MPa"}),
                                    warnings=[f"FEA failed: {type(e).__name__}: {e}"])

    deflection, d_study = _converged_quantity(levels, "tip_deflection_mm", "mm",
                                              config.FEA_GCI_MAX_DEFLECTION)
    stress, s_study = _converged_quantity(levels, "peak_von_mises_mpa", "MPa",
                                          config.FEA_GCI_MAX_STRESS)
    warnings = [f"{name} did not converge (GCI {study['gci']}, monotonic={study['monotonic']})"
                for name, q, study in (("tip deflection", deflection, d_study),
                                       ("peak stress", stress, s_study)) if not q.converged]
    return StructuralResult(level=2, tip_deflection=deflection, max_stress=stress,
                            load_case=load_case, material=material, assumptions=ASSUMPTIONS,
                            warnings=warnings,
                            details={"levels": levels, "deflection_study": d_study,
                                     "stress_study": s_study, "exclusion_mm": exclusion})
