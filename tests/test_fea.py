import os
import subprocess

import pytest

import config
import fea
from schemas import GeometryParams

needs_freecad = pytest.mark.skipif(not (os.path.exists(config.GMSH_EXE)
                                        and os.path.exists(config.CCX_EXE)),
                                   reason="gmsh/ccx from FreeCAD not installed")


def _synthetic(f0, c, p, sizes):
    """Values f = f0 + c*h^p on 3D meshes whose element counts scale as h^-3."""
    values = [f0 + c * h**p for h in sizes]
    counts = [round(1e6 / h**3) for h in sizes]
    return values, counts


def test_gci_recovers_known_order_and_limit():
    (coarse, medium, fine), (nc, nm, nf) = _synthetic(10.0, 0.5, 2.0, [4.0, 4 / 1.5, 4 / 2.25])
    study = fea.grid_convergence(fine, medium, coarse, nf, nm, nc)
    assert study["monotonic"]
    assert study["order"] == pytest.approx(2.0, rel=1e-3)
    assert study["extrapolated"] == pytest.approx(10.0, rel=1e-3)
    # With the order recovered exactly, the GCI is the safety factor times the
    # fine mesh's true relative error.
    assert study["gci"] == pytest.approx(1.25 * abs(fine - 10.0) / fine, rel=1e-3)


def test_gci_caps_implausibly_high_observed_order():
    # Medium and fine agree almost exactly, as in a real study of a 3.7/49.1/3.2 bracket.
    study = fea.grid_convergence(0.27622449, 0.27621826, 0.27479887, 73555, 27147, 10102,
                                 formal_order=2.0)
    assert study["order"] > 10 and study["order_used"] == 2.0
    r21 = (73555 / 27147) ** (1 / 3)
    assert study["gci"] == pytest.approx(1.25 * (0.27622449 - 0.27621826) / 0.27622449 / (r21**2 - 1))


def test_fea_uncertainty_has_a_floor():
    levels = [{"elements": 10102, "v": 0.27479887}, {"elements": 27147, "v": 0.27621826},
              {"elements": 73555, "v": 0.27622449}]
    q, _ = fea._converged_quantity(levels, "v", "mm", threshold=0.02)
    assert q.uncertainty == pytest.approx(0.27622449 * config.FEA_MIN_REL_UNCERTAINTY)


def test_oscillating_values_get_spread_based_uncertainty():
    # A real thin-plate study: medium and fine agree to 0.02% but the sequence wiggles.
    study = fea.grid_convergence(0.40975, 0.40982, 0.40815, 103030, 35349, 12448)
    assert study["oscillatory"] and not study["monotonic"]
    spread = (0.40982 - 0.40815) / 0.40975
    assert study["gci"] == pytest.approx(config.FEA_OSCILLATORY_FACTOR * spread)
    levels = [{"elements": 12448, "v": 0.40815}, {"elements": 35349, "v": 0.40982},
              {"elements": 103030, "v": 0.40975}]
    q, _ = fea._converged_quantity(levels, "v", "mm", threshold=0.02)
    assert q.converged and q.uncertainty == pytest.approx(0.40975 * study["gci"])
    # A wide oscillation is still rejected.
    wide = [{"elements": 700, "v": 0.98}, {"elements": 2400, "v": 1.05}, {"elements": 8000, "v": 1.00}]
    assert not fea._converged_quantity(wide, "v", "mm", threshold=0.02)[0].converged


def test_unconverged_fea_is_labelled_and_not_selectable():
    levels = [{"elements": 1000, "v": 1.0}, {"elements": 3400, "v": 1.3}, {"elements": 11500, "v": 1.45}]
    q, study = fea._converged_quantity(levels, "v", "mm", threshold=0.02)
    assert q.source == "fea" and q.status == "completed" and q.converged is False
    assert not q.counts_for_selection
    assert q.value == 1.45   # the finest mesh, not the extrapolation


def test_converged_fea_counts_for_selection():
    levels = [{"elements": 1000, "v": 1.2063}, {"elements": 3400, "v": 1.2114},
              {"elements": 11500, "v": 1.2133}]
    q, _ = fea._converged_quantity(levels, "v", "mm", threshold=0.02)
    assert q.converged is True and q.counts_for_selection and q.uncertainty > 0


def test_singularity_mask_excludes_boundary_layers():
    import numpy as np
    p = GeometryParams(plate_thickness=4, rib_height=30, rib_thickness=5)
    pts = np.array([[20, 0.5, 2], [20, 30, 2], [20, config.ARM_LENGTH - 0.5, 2]])
    assert fea._away_from_singularities(pts, p, 1.0).tolist() == [False, True, False]


@pytest.mark.freecad
@needs_freecad
def test_plain_plate_cantilever_matches_theory(tmp_path):
    """FEA of a 40x60x4 plate clamped at Y=0, loaded at Y=60, against closed forms.

    A wide clamped plate lies between Euler-Bernoulli beam theory and the fully
    restrained plate bound (beam * (1 - nu^2)).
    """
    step = tmp_path / "plate.step"
    subprocess.run([config.FREECAD_PYTHON, "-c",
                    "import sys, os; sys.path.append(os.path.dirname(sys.executable)); "
                    f"import FreeCAD, Part; Part.makeBox(40, 60, 4).exportStep(r'{step}')"],
                   check=True, capture_output=True)
    p = GeometryParams(plate_thickness=4, rib_height=30, rib_thickness=5)   # mask only
    result = fea.solve(p, step, 2.0, 8, tmp_path / "solve", exclusion_mm=4.0)
    beam = config.TIP_FORCE_N * 60**3 / (3 * config.YOUNGS_MODULUS_MPA * 40 * 4**3 / 12)
    assert result["loaded_face_area_mm2"] == pytest.approx(80.0 if config.FEA_HALF_MODEL else 160.0)
    assert beam * (1 - config.POISSON_RATIO**2) < result["tip_deflection_mm"] < beam


@pytest.mark.slow
@needs_freecad
@pytest.mark.skipif(os.getenv("RUN_SLOW_TESTS") != "1", reason="set RUN_SLOW_TESTS=1 (~2-3 min)")
def test_full_l2_study_on_default_bracket(tmp_path):
    import bracket
    stl, step = tmp_path / "b.stl", tmp_path / "b.step"
    bracket.build_bracket(4, 30, 5, stl, step)
    result = fea.evaluate_l2(GeometryParams(plate_thickness=4, rib_height=30, rib_thickness=5), step)
    assert result.level == 2 and len(result.details["levels"]) == 3
    assert result.tip_deflection.source == "fea" and result.tip_deflection.converged
