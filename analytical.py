# Level 1 structural evaluation: closed-form / numerically integrated beam theory.
#
# Model: the arm is a cantilever of length Lc = ARM_LENGTH - t, clamped at the
# wall's inner face (the wall's back face is fully fixed, so the wall is treated
# as rigid). Its cross-section is the plate (width W, thickness t) plus the rib
# (thickness rt), whose height above the plate tapers linearly from h at the root
# to 0 at s = h. Tip deflection is integrated from M/EI with a unit-load method,
# plus a Timoshenko shear term F/(G*A_shear) integrated along the arm.
import math

import numpy as np
from scipy.integrate import quad

import config
from schemas import GeometryParams, Quantity, StructuralResult

ASSUMPTIONS = [
    "Euler-Bernoulli bending of the arm plus Timoshenko shear deflection (shear carried "
    "by the rib web over the full depth where the rib exists, 5/6 of the plate elsewhere)",
    "full plate width effective in bending (shear lag in the wide plate neglected)",
    "wall plate rigid (back face fully fixed); arm clamped at the wall's inner face",
    "linear elastic, isotropic material with nominal datasheet properties",
    "tip load treated as a point load at the arm end",
    "inner fillet stiffening neglected (conservative)",
    "stress concentrations at the fillet and rib toe NOT included; "
    "reported stress is nominal bending stress",
]
DEEP_BEAM_RATIO = 2.0   # Lc / root depth below which beam theory is doubtful
SMALL_DEFLECTION_RATIO = 0.1   # deflection / Lc above which linear theory is invalid


def section(t, rib_above, width, rib_thickness):
    """Second moment of area (mm^4) and extreme-fibre distance (mm) of the plate+rib section."""
    a1, z1 = width * t, t / 2
    a2, z2 = rib_thickness * rib_above, t + rib_above / 2
    zc = (a1 * z1 + a2 * z2) / (a1 + a2)
    inertia = (width * t**3 / 12 + a1 * (z1 - zc) ** 2
               + rib_thickness * rib_above**3 / 12 + a2 * (z2 - zc) ** 2)
    extreme = max(zc, t + rib_above - zc)
    return inertia, extreme


def shear_area(t, rib_above, width, rib_thickness):
    """Effective shear area (mm^2): the rib web over the full depth, or 5/6 of a plain plate."""
    if rib_above > 0:
        return rib_thickness * (t + rib_above)
    return 5 / 6 * width * t


def beam_response(t, h, rib_thickness, width, length, force, modulus, poisson=None):
    """Tip deflection (mm), peak nominal bending stress (MPa) and its distance from the root (mm).

    h may be 0 (plain plate). Units: mm, N, MPa. With poisson=None only bending
    deflection is computed; otherwise the Timoshenko shear deflection is added.
    """
    def rib_above(s):
        return max(h - s, 0.0)

    def inv_inertia_term(s):
        inertia, _ = section(t, rib_above(s), width, rib_thickness)
        return (length - s) ** 2 / inertia

    breaks = [h] if 0 < h < length else None
    integral, _ = quad(inv_inertia_term, 0.0, length, points=breaks, limit=200)
    deflection = force * integral / modulus
    if poisson is not None:
        shear_modulus = modulus / (2 * (1 + poisson))
        compliance, _ = quad(lambda s: 1 / shear_area(t, rib_above(s), width, rib_thickness),
                             0.0, length, points=breaks, limit=200)
        deflection += force * compliance / shear_modulus

    grid = np.unique(np.concatenate([np.linspace(0.0, length, 2001), [min(h, length)]]))
    stresses = []
    for s in grid:
        inertia, extreme = section(t, rib_above(s), width, rib_thickness)
        stresses.append(force * (length - s) * extreme / inertia)
    i = int(np.argmax(stresses))
    return deflection, float(stresses[i]), float(grid[i])


def _concave_fillet_area(radius, free_angle):
    """Cross-section area a fillet adds in a concave corner whose free-space angle is free_angle (rad)."""
    return radius**2 * (1 / math.tan(free_angle / 2) - (math.pi - free_angle) / 2)


def volume_mm3(params: GeometryParams):
    """Solid volume of the bracket, mm^3.

    Exact for plates + inner fillet + rib. The rib-junction fillets are added
    edge by edge, neglecting where fillets meet at corners (~0.01% of volume).
    """
    t, h, rt = params.plate_thickness, params.rib_height, params.rib_thickness
    r_in, r_rib = config.INNER_FILLET_RADIUS, config.RIB_FILLET_RADIUS
    fillet_fill = _concave_fillet_area(r_in, math.pi / 2)
    plates = config.BRACKET_WIDTH * (config.ARM_LENGTH * t
                                     + (config.WALL_HEIGHT - t) * t + fillet_fill)
    rib = rt * (h**2 / 2 - fillet_fill)   # fillet region inside the rib is already counted
    # Rib side edges meet the plates at 90 deg along two straight runs and the
    # inner-fillet arc, on both sides; the toe and top edges meet them at 135 deg.
    side_length = 2 * (2 * (h - r_in) + math.pi * r_in / 2)
    rib_fillets = (_concave_fillet_area(r_rib, math.pi / 2) * side_length
                   + _concave_fillet_area(r_rib, 3 * math.pi / 4) * 2 * rt)
    return plates + rib + rib_fillets


def mass(params: GeometryParams):
    return Quantity(value=volume_mm3(params) * config.DENSITY_G_PER_MM3, unit="g",
                    source="analytical", status="completed")


def depth_ratio(params: GeometryParams):
    """Root depth / cantilever length: the geometric driver of L1's model-form error."""
    return (params.plate_thickness + params.rib_height) / (config.ARM_LENGTH - params.plate_thickness)


def evaluate_l1(params: GeometryParams):
    t, h, rt = params.plate_thickness, params.rib_height, params.rib_thickness
    length = config.ARM_LENGTH - t
    deflection, stress, location = beam_response(t, h, rt, config.BRACKET_WIDTH, length,
                                                 config.TIP_FORCE_N, config.YOUNGS_MODULUS_MPA,
                                                 config.POISSON_RATIO)
    warnings = []
    if length / (t + h) < DEEP_BEAM_RATIO:
        warnings.append(f"deep-beam regime (Lc/depth = {length / (t + h):.2f} < "
                        f"{DEEP_BEAM_RATIO}); beam theory is approximate here")
    if deflection / length > SMALL_DEFLECTION_RATIO:
        warnings.append(f"deflection is {deflection / length:.0%} of the arm length; "
                        "small-deflection linear theory is invalid (the design is far too flexible)")
    if stress > config.TENSILE_STRENGTH_MPA:
        warnings.append("nominal stress exceeds tensile strength; linear-elastic result invalid")
    warnings.append(f"peak nominal stress at {location:.1f} mm from the root")

    return StructuralResult(
        level=1,
        tip_deflection=Quantity(value=deflection, unit="mm", source="analytical",
                                status="completed",
                                uncertainty=deflection * config.L1_DEFLECTION_REL_UNCERTAINTY),
        max_stress=Quantity(value=stress, unit="MPa", source="analytical", status="completed",
                            uncertainty=stress * config.L1_STRESS_REL_UNCERTAINTY),
        load_case={"tip_force_n": config.TIP_FORCE_N, "direction": "-Z",
                   "fixed": "wall back face (Y=0)"},
        material={"youngs_modulus_mpa": config.YOUNGS_MODULUS_MPA},
        assumptions=ASSUMPTIONS,
        warnings=warnings,
    )
