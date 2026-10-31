"""Numerical mechanics, fabrication templates, and the adaptive baseline.

Geometry is an idealized U section: a base b by t and two walls h by t
above the base. Fold radii and section distortion are not represented.
The fitted elastic modulus is effective for this specimen family.
"""
from __future__ import annotations

from html import escape
import math
from typing import Iterable

import numpy as np
from scipy.optimize import nnls

from .domain import Geometry, ModelParameters, Observation, Protocol


def section_properties(geometry: Geometry) -> dict:
    """Return actual component-area properties, not a solid bounding box."""
    b, h, t = geometry.base_width_m, geometry.wall_height_m, geometry.thickness_m
    if geometry.section_family != "u_channel":
        height = h + t
        inner_b, inner_h = (b - 2 * t, height - 2 * t) if geometry.section_family == "rectangular_tube" else (0.0, 0.0)
        area = b * height - inner_b * inner_h
        return {"area_m2": area, "second_moment_m4": (b * height**3 - inner_b * inner_h**3) / 12,
                "neutral_axis_m": height / 2, "developed_width_m": None, "developed_area_m2": None,
                "volume_m3": area * geometry.length_m,
                "assumptions": ["prismatic section", "sharp corners", "nominal solid material"]}
    if h > 0 and b <= 2 * t:
        raise ValueError("U section base width must exceed twice the wall thickness")
    base_area, wall_area = b * t, h * t
    area = base_area + 2 * wall_area
    base_y, wall_y = t / 2, t + h / 2
    neutral_axis = (base_area * base_y + 2 * wall_area * wall_y) / area
    inertia = (
        b * t**3 / 12 + base_area * (base_y - neutral_axis) ** 2
        + 2 * (t * h**3 / 12 + wall_area * (wall_y - neutral_axis) ** 2)
    )
    return {
        "area_m2": area,
        "second_moment_m4": inertia,
        "neutral_axis_m": neutral_axis,
        "developed_width_m": b + 2 * h,
        "developed_area_m2": geometry.length_m * (b + 2 * h),
        "volume_m3": area * geometry.length_m,
        "assumptions": ["idealized sharp folds", "uniform thickness", "walls above base"],
    }


def predict_deflection(
    geometry: Geometry, parameters: ModelParameters, force_n: float, load_position_m: float
) -> dict:
    if not math.isfinite(force_n) or force_n <= 0:
        raise ValueError("Force must be finite and positive")
    if not math.isfinite(load_position_m) or not 0 < load_position_m <= geometry.free_length_m:
        raise ValueError("Load position must lie on the free span")
    inertia = section_properties(geometry)["second_moment_m4"]
    bending = force_n * load_position_m**3 / (3 * parameters.youngs_modulus_pa * inertia)
    mounting = force_n * parameters.rotational_compliance_rad_per_nm * load_position_m**2
    displacement = bending + mounting
    return {
        "displacement_m": displacement,
        "bending_m": bending,
        "mount_rotation_m": mounting,
        "validity_flags": ["small_deflection_screen_exceeded"] if displacement > 0.05 * load_position_m else [],
        "prediction_only": True,
    }


def generate_template(geometry: Geometry, load_positions_m: Iterable[float] = (0.06, 0.09, 0.12)) -> str:
    """A print-at-100%-scale SVG, including clamp and centerline load marks."""
    if geometry.section_family != "u_channel":
        b, height = 1000 * geometry.base_width_m, 1000 * (geometry.wall_height_m + geometry.thickness_m)
        t = 1000 * geometry.thickness_m
        inner = f'<rect x="{10+t:g}" y="{20+t:g}" width="{b-2*t:g}" height="{height-2*t:g}" fill="white" stroke="black"/>' if geometry.section_family == "rectangular_tube" else ""
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="70mm" viewBox="0 0 100 70"><text x="5" y="8" font-size="4">{escape(geometry.section_family)} / section drawing</text><rect x="10" y="20" width="{b:g}" height="{height:g}" fill="#bdd" stroke="black"/>{inner}<text x="5" y="60" font-size="3">Nominal dimensions in mm. Use STL for printing.</text></svg>'
    length, base, wall, clamp = [1000 * x for x in (
        geometry.length_m, geometry.base_width_m, geometry.wall_height_m, geometry.clamp_length_m
    )]
    width = base + 2 * wall
    x0, y0 = 18.0, 34.0
    page_width, page_height = max(210.0, length + 36), width + 105
    def text(x: float, y: float, label: str, size: float = 3.0) -> str:
        return f'<text x="{x:g}" y="{y:g}" font-size="{size:g}">{escape(label)}</text>'
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_width:g}mm" height="{page_height:g}mm" viewBox="0 0 {page_width:g} {page_height:g}">',
        '<g font-family="sans-serif" fill="#182631">',
        text(x0, 10, "ScienceClaw / folded U support", 5),
        text(x0, 17, f"Batch: {geometry.material_batch}"),
        text(x0, 23, f"L {length:g} mm · base {base:g} mm · walls {wall:g} mm · sheet {geometry.thickness_m * 1000:g} mm"),
        f'<rect x="{x0:g}" y="{y0:g}" width="{clamp:g}" height="{width:g}" fill="#eee"/>',
        f'<rect x="{x0:g}" y="{y0:g}" width="{length:g}" height="{width:g}" fill="none" stroke="#182631" stroke-width="0.25"/>',
    ]
    if wall > 0:
        for yy in (y0 + wall, y0 + wall + base):
            parts.append(f'<path d="M{x0:g},{yy:g} H{x0 + length:g}" fill="none" stroke="#246fa8" stroke-width="0.2" stroke-dasharray="2 1"/>')
    parts.extend([
        f'<path d="M{x0 + clamp:g},{y0 - 3:g} V{y0 + width + 3:g}" stroke="#bd5e27" stroke-width="0.3"/>',
        text(x0, y0 - 4, "Clamp"),
    ])
    for position in load_positions_m:
        if not math.isfinite(position) or position <= 0 or position > geometry.free_length_m:
            continue
        xx, yy = x0 + clamp + position * 1000, y0 + wall + base / 2
        parts.extend([
            f'<circle cx="{xx:g}" cy="{yy:g}" r="1.2" stroke="#182631" stroke-width="0.2" fill="none"/>',
            f'<path d="M{xx - 2:g},{yy:g} H{xx + 2:g} M{xx:g},{yy - 2:g} V{yy + 2:g}" stroke="#182631" stroke-width="0.15"/>',
            text(xx - 5, y0 + width + 8, f"{position * 1000:g} mm"),
        ])
    footer = y0 + width + 17
    parts.extend([
        text(x0, footer, "Solid outline: cut. Dashed blue: fold both walls upward 90°."),
        text(x0, footer + 6, "Load stations are measured from the orange clamp line."),
        text(x0, footer + 12, "Idealized sharp folds. Verify actual dimensions after fabrication."),
        text(x0, footer + 18, "Print at 100% / actual size. Verify the scale bar with a ruler."),
        f'<path d="M{x0:g},{footer + 25:g} h50 m-50,-2 v4 m50,-4 v4" stroke="#182631" stroke-width="0.3"/>',
        text(x0 + 18, footer + 31, "50 mm"),
        '</g></svg>',
    ])
    return "\n".join(parts)


def _invalid_reasons(observation: Observation, protocol: Protocol) -> list[str]:
    reasons = list(observation.quality_flags)
    if observation.purpose in ("reserved", "acceptance"):
        reasons.append("held_out")
    if abs(observation.residual_m) > protocol.max_residual_m:
        reasons.append("permanent_set")
    if abs(observation.fixture_motion_m) > protocol.max_fixture_motion_m:
        reasons.append("fixture_translation")
    if observation.uncertainty_m > protocol.max_uncertainty_m:
        reasons.append("measurement_uncertainty")
    if abs(observation.displacement_m) > protocol.max_deflection_ratio * observation.load_position_m:
        reasons.append("small_deflection_screen_exceeded")
    if observation.displacement_m < -3 * observation.uncertainty_m:
        reasons.append("negative_load_response")
    if observation.dwell_s < protocol.dwell_s or observation.recovery_s < protocol.recovery_s:
        reasons.append("timing_incomplete")
    return reasons


def _design_row(observation: Observation) -> list[float]:
    inertia = section_properties(observation.geometry)["second_moment_m4"]
    a, force = observation.load_position_m, observation.force_n
    return [force * a**3 / (3 * inertia), force * a**2]


def fit_model(observations: list[Observation], protocol: Protocol) -> dict:
    """Weighted nonnegative compliance fit, with a dimensionless condition check.

    uncertainty_m is the supplied one-standard-deviation displacement uncertainty.
    Normal confidence estimates are local approximations, especially at C_theta=0.
    Held-out records are excluded even if a caller accidentally supplies them.
    """
    valid: list[Observation] = []
    excluded = []
    seen = set()
    for observation in observations:
        reasons = _invalid_reasons(observation, protocol)
        if observation.id in seen:
            reasons.append("duplicate_observation")
        seen.add(observation.id)
        if reasons:
            excluded.append({"id": observation.id, "reasons": reasons})
        else:
            valid.append(observation)
    result = {
        "status": "insufficient", "parameters": None, "condition_number": None,
        "residuals_m": [], "uncertainty": None,
        "source_observation_ids": [o.id for o in valid],
        "excluded_observations": excluded, "warnings": [],
        "assumptions": ["elastic beam response", "rotational mount; no fixture translation", "effective stiffness for tested section family"],
    }
    if len(valid) < 2 or len({round(o.load_position_m, 10) for o in valid}) < 2:
        result["warnings"].append("Two well-separated load positions are required; repeating frames or force levels at one station cannot identify both parameters.")
        return result
    if len({o.source for o in valid}) > 1:
        result.update(status="inadequate")
        result["warnings"].append("Physical and simulated observations must not be pooled in one fit.")
        return result
    if len({o.geometry.material_batch for o in valid}) > 1:
        result.update(status="inadequate")
        result["warnings"].append("A single effective stiffness cannot be fitted across material batches.")
        return result
    if len({o.geometry.section_family for o in valid}) > 1:
        result.update(status="inadequate")
        result["warnings"].append("Do not pool effective stiffness across unvalidated section families.")
        return result
    x = np.asarray([_design_row(o) for o in valid], dtype=float)
    y = np.asarray([o.displacement_m for o in valid])
    sigma = np.asarray([o.uncertainty_m for o in valid])
    weighted_x, weighted_y = x / sigma[:, None], y / sigma
    scales = np.linalg.norm(weighted_x, axis=0)
    normalized_x = weighted_x / scales
    condition = float(np.linalg.cond(normalized_x))
    result["condition_number"] = condition if math.isfinite(condition) else None
    if not math.isfinite(condition) or condition > 1000 or np.linalg.matrix_rank(normalized_x) < 2:
        result["status"] = "ill_conditioned"
        result["warnings"].append("The experiment cannot reliably separate bending and mount rotation; choose a more distant station.")
        return result
    normalized_coefficients, _ = nnls(normalized_x, weighted_y)
    coefficients = normalized_coefficients / scales
    reciprocal_e, compliance = coefficients
    if reciprocal_e <= 0:
        result["status"] = "inadequate"
        result["warnings"].append("The optimum has zero beam compliance; finite effective stiffness is unresolved.")
        return result
    residuals = y - x @ coefficients
    dof = len(valid) - 2
    chi_square = float(np.sum((residuals / sigma) ** 2))
    covariance_normalized = np.linalg.inv(normalized_x.T @ normalized_x)
    covariance = covariance_normalized / np.outer(scales, scales)
    # Known measurement uncertainty supplies variance even for an exactly fitted pair.
    # Inflate uncertainty when repeat residuals exceed that supplied measurement model.
    variance_factor = max(1.0, chi_square / dof) if dof > 0 else 1.0
    covariance *= variance_factor
    e = float(1 / reciprocal_e)
    e_sd = float(math.sqrt(max(0.0, covariance[0, 0])) / reciprocal_e**2)
    c_sd = float(math.sqrt(max(0.0, covariance[1, 1])))
    result.update(
        status="identifiable",
        parameters=ModelParameters(youngs_modulus_pa=e, rotational_compliance_rad_per_nm=float(compliance)).model_dump(),
        residuals_m=[{"observation_id": o.id, "residual_m": float(r)} for o, r in zip(valid, residuals)],
        uncertainty={
            "youngs_modulus_pa_std": e_sd,
            "rotational_compliance_rad_per_nm_std": c_sd,
            "compliance_covariance": covariance.tolist(),
            "weighted_chi_square": chi_square, "degrees_of_freedom": dof,
            "interpretation": "Local linear 1-sigma approximation; not a validated transfer interval",
        },
    )
    if len({o.specimen_id for o in valid}) > 1:
        result["warnings"].append("Pooled specimens assume equal mounting compliance; inspect fabrication and reclamping variation.")
    if dof == 0:
        result["warnings"].append("Only two observations: fit residuals cannot validate the model; use the reserved station.")
    if compliance <= 1e-12:
        result["warnings"].append("Mount compliance is on its nonnegative boundary; normal uncertainty is approximate.")
    if dof > 0 and (np.max(np.abs(residuals) / sigma) > 3 or chi_square / dof > 9):
        result["status"] = "inadequate"
        result["warnings"].append("Residuals exceed stated measurement uncertainty; check slip, nonlinearity, or model error.")
        result["parameters"] = None
    elif e_sd / e > 0.5:
        result["status"] = "ill_conditioned"
        result["warnings"].append("Stiffness uncertainty exceeds 50%; collect more precise or better-separated measurements.")
        result["parameters"] = None
    return result


def _grid(low: float, high: float, step: float) -> list[float]:
    values = [low + i * step for i in range(int(math.floor((high - low) / step + 1e-9)) + 1)]
    return [round(v, 12) for v in values]


def optimize_geometry(reference: Geometry, parameters: ModelParameters, protocol: Protocol) -> dict:
    """Enumerate the same reproducible geometry menu available to the agents.

    The two-sigma measurement margin is conservative against readout noise only;
    it does not establish a physical pass or cover model transfer uncertainty.
    """
    if protocol.proof_position_m > reference.free_length_m:
        raise ValueError("Proof station lies outside the reference free span")
    width_count = math.floor((protocol.base_width_max_m - protocol.base_width_min_m) / protocol.geometry_step_m + 1e-9) + 1
    height_count = math.floor((protocol.wall_height_max_m - protocol.wall_height_min_m) / protocol.geometry_step_m + 1e-9) + 1
    if width_count * height_count > 100000:
        raise ValueError("Geometry grid exceeds 100000 candidates; increase geometry_step_m")
    widths = _grid(protocol.base_width_min_m, protocol.base_width_max_m, protocol.geometry_step_m)
    heights = _grid(protocol.wall_height_min_m, protocol.wall_height_max_m, protocol.geometry_step_m)
    evaluations = 0
    best = None
    margin = 2 * protocol.max_uncertainty_m
    for width in widths:
        for height in heights:
            if width <= reference.thickness_m or (height > 0 and width <= 2 * reference.thickness_m):
                continue
            try:
                geometry = Geometry(**{**reference.model_dump(), "base_width_m": width, "wall_height_m": height})
            except ValueError:
                continue
            prediction = predict_deflection(geometry, parameters, protocol.proof_force_n, protocol.proof_position_m)
            evaluations += 1
            if not all(r["passed"] for r in requirement_checks(geometry, parameters, protocol)):
                continue
            volume = section_properties(geometry)["volume_m3"]
            score = (volume, prediction["displacement_m"], width, height)
            if best is None or score < best[0]:
                best = (score, geometry, prediction)
    return {
        "status": "predicted_feasible" if best else "no_predicted_feasible_design",
        "geometry": best[1].model_dump() if best else None,
        "evaluations": evaluations, "prediction": best[2] if best else None,
        "developed_area_m2": section_properties(best[1])["developed_area_m2"] if best else None,
        "volume_m3": section_properties(best[1])["volume_m3"] if best else None,
        "measurement_margin_m": margin,
        "metric": "geometric_material_volume", "physical_pass": False,
    }


def root_stress(geometry: Geometry, force_n: float, position_m: float) -> float:
    """Elastic bending stress at the clamp line, at the fiber farthest from the neutral axis."""
    props = section_properties(geometry)
    height = max(y for _, y in _section_polygon_mm(geometry)) / 1000
    fiber = max(props["neutral_axis_m"], height - props["neutral_axis_m"])
    return force_n * position_m * fiber / props["second_moment_m4"]


def requirement_checks(geometry: Geometry, parameters: ModelParameters, protocol: Protocol) -> list[dict]:
    """Every frozen design requirement for one geometry. Model predictions, not measurements."""
    force, a = protocol.proof_force_n, protocol.proof_position_m
    delta = predict_deflection(geometry, parameters, force, a)["displacement_m"]
    margin = 2 * protocol.max_uncertainty_m
    rows = [
        {"name": "deflection", "label": f"Deflection at {force:g} N, {a * 1000:g} mm (+2σ margin)", "unit": "length",
         "value": delta + margin, "limit": protocol.max_deflection_m, "passed": delta + margin <= protocol.max_deflection_m},
        {"name": "small_deflection", "label": "Within the small-deflection beam model", "unit": "ratio",
         "value": delta / a, "limit": protocol.max_deflection_ratio, "passed": delta <= protocol.max_deflection_ratio * a},
        {"name": "base_width", "label": "Base width within allowed range", "unit": "length", "value": geometry.base_width_m,
         "range": [protocol.base_width_min_m, protocol.base_width_max_m],
         "passed": protocol.base_width_min_m - 1e-12 <= geometry.base_width_m <= protocol.base_width_max_m + 1e-12},
        {"name": "wall_height", "label": "Wall height within allowed range", "unit": "length", "value": geometry.wall_height_m,
         "range": [protocol.wall_height_min_m, protocol.wall_height_max_m],
         "passed": protocol.wall_height_min_m - 1e-12 <= geometry.wall_height_m <= protocol.wall_height_max_m + 1e-12},
    ]
    if protocol.max_volume_m3 is not None:
        volume = section_properties(geometry)["area_m2"] * geometry.length_m
        rows.append({"name": "material", "label": "Material volume within budget", "unit": "volume",
                     "value": volume, "limit": protocol.max_volume_m3, "passed": volume <= protocol.max_volume_m3})
    if protocol.max_root_stress_pa is not None:
        stress = root_stress(geometry, force, a)
        rows.append({"name": "root_stress", "label": "Root bending stress within allowable", "unit": "stress",
                     "value": stress, "limit": protocol.max_root_stress_pa, "passed": stress <= protocol.max_root_stress_pa})
    return rows


def choose_diagnostic_position(observations: list[Observation], geometry: Geometry, protocol: Protocol) -> float:
    """Choose the station maximizing the determinant of information after one test.

    Columns use fixed parameter scales to compare actions fairly. Already measured
    conditions remain available when repeats are the most useful legal action.
    """
    positions = sorted(p for p in set(protocol.diagnostic_positions_m) if p <= geometry.free_length_m and p != protocol.reserved_position_m)
    if not positions:
        raise ValueError("No diagnostic position lies on the free span")
    usable = [o for o in observations if not _invalid_reasons(o, protocol)]
    parameter_scales = np.array([1e-9, 1.0])
    rows = [np.asarray(_design_row(o)) * parameter_scales / o.uncertainty_m for o in usable]
    information = sum((np.outer(row, row) for row in rows), np.zeros((2, 2)))
    inertia = section_properties(geometry)["second_moment_m4"]
    def score(a: float) -> tuple[float, float, float]:
        row = np.array([protocol.proof_force_n * a**3 / (3 * inertia), protocol.proof_force_n * a**2])
        row = row * parameter_scales / protocol.max_uncertainty_m
        updated = information + np.outer(row, row)
        determinant = max(0.0, float(np.linalg.det(updated)))
        # A single rank-one test has zero determinant: choose larger signal first.
        return determinant, float(np.trace(updated)), -a
    return max(positions, key=score)


def _section_polygon_mm(geometry: Geometry) -> list[tuple[float, float]]:
    section_properties(geometry)  # Validate nonoverlapping wall geometry.
    b, h, t = (1000 * value for value in (geometry.base_width_m, geometry.wall_height_m, geometry.thickness_m))
    if geometry.section_family != "u_channel":
        return [(0, 0), (b, 0), (b, t+h), (0, t+h)]
    if h == 0:
        return [(0, 0), (b, 0), (b, t), (0, t)]
    return [(0, 0), (b, 0), (b, t + h), (b - t, t + h), (b - t, t), (t, t), (t, t + h), (0, t + h)]


def _triangulate_polygon(points: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Ear clipping of the small counterclockwise, simple cross-section polygon."""
    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    def in_triangle(p, a, b, c):
        return min(cross(a, b, p), cross(b, c, p), cross(c, a, p)) >= -1e-10
    remaining = list(range(len(points)))
    triangles = []
    while len(remaining) > 3:
        for index, middle in enumerate(remaining):
            previous, following = remaining[index - 1], remaining[(index + 1) % len(remaining)]
            a, b, c = points[previous], points[middle], points[following]
            if cross(a, b, c) <= 1e-10:
                continue
            if any(in_triangle(points[p], a, b, c) for p in remaining if p not in (previous, middle, following)):
                continue
            triangles.append((previous, middle, following))
            remaining.pop(index)
            break
        else:
            raise ValueError("Cannot triangulate degenerate section")
    triangles.append(tuple(remaining))
    return triangles


def generate_stl(geometry: Geometry) -> str:
    """Watertight outward-oriented U channel, with STL coordinates in millimeters.

    This is nominal solid geometry, not a slicer setup or a strength qualification.
    The first clamp_length_m of the extrusion is reserved for the fixture.
    """
    points = _section_polygon_mm(geometry)
    cap = _triangulate_polygon(points)
    loops = [list(range(len(points)))]
    if geometry.section_family == "rectangular_tube":
        b, h, t = (1000 * v for v in (geometry.base_width_m, geometry.wall_height_m + geometry.thickness_m, geometry.thickness_m))
        points += [(t,t), (b-t,t), (b-t,h-t), (t,h-t)]
        # Four nonoverlapping strips form an annular cap. Inner side loop is clockwise.
        cap = []
        for i in range(4):
            j = (i + 1) % 4
            cap.extend([(i,j,j+4), (i,j+4,i+4)])
        loops = [[0,1,2,3], [7,6,5,4]]
    length = geometry.length_m * 1000
    vertices = [(x, y, z) for x in (0.0, length) for y, z in points]
    n = len(points)
    faces = [(c, b, a) for a, b, c in cap] + [(a + n, b + n, c + n) for a, b, c in cap]
    for loop in loops:
        for k, i in enumerate(loop):
            j = loop[(k + 1) % len(loop)]
            faces.extend([(i, j, j + n), (i, j + n, i + n)])
    output = [f"solid scienceclaw_{geometry.section_family}_mm"]
    for face in faces:
        a, b, c = [np.asarray(vertices[index]) for index in face]
        normal = np.cross(b - a, c - a)
        normal /= np.linalg.norm(normal)
        output.append("  facet normal " + " ".join(f"{value:.12g}" for value in normal))
        output.append("    outer loop")
        output.extend("      vertex " + " ".join(f"{value:.12g}" for value in vertices[index]) for index in face)
        output.extend(["    endloop", "  endfacet"])
    output.append(f"endsolid scienceclaw_{geometry.section_family}_mm")
    return "\n".join(output) + "\n"


def generate_scad(geometry: Geometry) -> str:
    """Editable OpenSCAD source matching STL and mechanics section dimensions."""
    section_properties(geometry)
    values = {name: getattr(geometry, name) * 1000 for name in (
        "length_m", "clamp_length_m", "base_width_m", "wall_height_m", "thickness_m"
    )}
    if geometry.section_family != "u_channel":
        length, width, height, thickness = values["length_m"], values["base_width_m"], values["wall_height_m"]+values["thickness_m"], values["thickness_m"]
        hollow = f'translate([-1, thickness, thickness]) cube([length+2, width-2*thickness, height-2*thickness]);' if geometry.section_family == "rectangular_tube" else ""
        return f'// ScienceClaw {geometry.section_family}; nominal millimeters. Printer process requires validation.\nlength = {length:g};\nwidth = {width:g};\nheight = {height:g};\nthickness = {thickness:g};\ndifference() {{ cube([length,width,height]); {hollow} }}\n'
    return f'''// ScienceClaw nominal U-channel. All dimensions in millimeters.
// Clamp the first clamp_length along x. No holes or clamp reinforcement.
// Print parameters, layer orientation, material batch, and actual dimensions
// must be recorded separately; this file does not prescribe a qualified process.
length = {values["length_m"]:.12g};
clamp_length = {values["clamp_length_m"]:.12g};
base_width = {values["base_width_m"]:.12g};
wall_height = {values["wall_height_m"]:.12g};
thickness = {values["thickness_m"]:.12g};
assert(length > clamp_length && clamp_length > 0);
assert(base_width > 2 * thickness && thickness > 0 && wall_height >= 0);
union() {{
    cube([length, base_width, thickness]);
    if (wall_height > 0) {{
        translate([0, 0, thickness]) cube([length, thickness, wall_height]);
        translate([0, base_width - thickness, thickness]) cube([length, thickness, wall_height]);
    }}
}}
'''
