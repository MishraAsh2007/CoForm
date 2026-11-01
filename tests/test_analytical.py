import math
import os

import pytest

import analytical
import config
from schemas import GeometryParams

E = 2500.0
W, T, L, F = 40.0, 4.0, 56.0, 50.0


def test_plain_plate_matches_closed_form_cantilever():
    deflection, stress, location = analytical.beam_response(T, 0.0, 5.0, W, L, F, E)
    inertia = W * T**3 / 12
    assert deflection == pytest.approx(F * L**3 / (3 * E * inertia), rel=1e-6)
    assert stress == pytest.approx(6 * F * L / (W * T**2), rel=1e-6)
    assert location == 0.0


def test_timoshenko_shear_term_for_plain_plate():
    nu = 0.35
    bending, _, _ = analytical.beam_response(T, 0.0, 5.0, W, L, F, E)
    total, _, _ = analytical.beam_response(T, 0.0, 5.0, W, L, F, E, poisson=nu)
    shear_modulus = E / (2 * (1 + nu))
    assert total - bending == pytest.approx(F * L / (5 / 6 * W * T * shear_modulus), rel=1e-6)


def test_t_section_properties_by_hand():
    # Plate 40x4 plus rib 5 wide, 10 high on top.
    inertia, extreme = analytical.section(4.0, 10.0, 40.0, 5.0)
    zc = (160 * 2 + 50 * 9) / 210
    expected = 40 * 4**3 / 12 + 160 * (2 - zc) ** 2 + 5 * 10**3 / 12 + 50 * (9 - zc) ** 2
    assert inertia == pytest.approx(expected)
    assert extreme == pytest.approx(14 - zc)


def test_rib_reduces_deflection_and_taller_rib_reduces_more():
    plain, _, _ = analytical.beam_response(T, 0.0, 5.0, W, L, F, E, poisson=0.35)
    short, _, _ = analytical.beam_response(T, 10.0, 5.0, W, L, F, E, poisson=0.35)
    tall, _, _ = analytical.beam_response(T, 30.0, 5.0, W, L, F, E, poisson=0.35)
    assert tall < short < plain


def test_volume_by_hand(monkeypatch):
    monkeypatch.setattr(config, "RIB_FILLET_RADIUS", 0.0)   # the exact, unfilleted case
    p = GeometryParams(plate_thickness=4, rib_height=30, rib_thickness=5)
    fill = (1 - math.pi / 4) * config.INNER_FILLET_RADIUS**2
    expected = (40 * 60 * 4 + 40 * 56 * 4 + 40 * fill) + 5 * (30**2 / 2 - fill)
    assert analytical.volume_mm3(p) == pytest.approx(expected)


def test_concave_fillet_area():
    assert analytical._concave_fillet_area(1.0, math.pi / 2) == pytest.approx(1 - math.pi / 4)
    assert analytical._concave_fillet_area(1.0, math.pi) == pytest.approx(0.0, abs=1e-12)


def test_evaluate_l1_labels_provenance():
    result = analytical.evaluate_l1(GeometryParams(plate_thickness=4, rib_height=30,
                                                   rib_thickness=5))
    assert result.level == 1
    for q in (result.tip_deflection, result.max_stress):
        assert q.source == "analytical" and q.status == "completed"
        assert q.uncertainty > 0
        assert not q.counts_for_selection
    assert any("stress concentrations" in a for a in result.assumptions)


def test_l1_flags_results_outside_its_validity_range():
    result = analytical.evaluate_l1(GeometryParams(plate_thickness=1.5, rib_height=5,
                                                   rib_thickness=1.5))
    assert any("small-deflection" in w for w in result.warnings)
    assert any("exceeds tensile strength" in w for w in result.warnings)


@pytest.mark.freecad
@pytest.mark.skipif(not os.path.exists(config.FREECAD_PYTHON), reason="FreeCAD not installed")
def test_analytical_volume_matches_freecad(tmp_path):
    import bracket
    for t, h, rt in [(4, 30, 5), (3, 20, 4), (6, 45, 8), (1.5, 5, 1.5), (8, 50, 10)]:
        result = bracket.build_bracket(t, h, rt, tmp_path / "b.stl", tmp_path / "b.step")
        p = GeometryParams(plate_thickness=t, rib_height=h, rib_thickness=rt)
        # Exact apart from where rib fillets meet at corners.
        assert result["volume_mm3"] == pytest.approx(analytical.volume_mm3(p), rel=2e-4)
