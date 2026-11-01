import math
from collections import Counter
from xml.etree import ElementTree

import pytest

from coform.domain import Geometry, ModelParameters, Observation, Protocol
from coform.mechanics import (
    choose_diagnostic_position, fit_model, generate_template, generate_stl, generate_scad, optimize_geometry,
    predict_deflection, section_properties,
)


def observation(position, parameters=None, *, geometry=None, **changes):
    geometry = geometry or Geometry()
    parameters = parameters or ModelParameters(youngs_modulus_pa=1.5e9, rotational_compliance_rad_per_nm=0.2)
    force = changes.pop("force_n", 0.3)
    data = dict(
        candidate_id="c1", specimen_id="s1", experiment_id="e1", geometry=geometry,
        force_n=force, load_position_m=position,
        displacement_m=predict_deflection(geometry, parameters, force, position)["displacement_m"],
        uncertainty_m=0.000001, dwell_s=3, recovery_s=3, source="simulated",
    )
    data.update(changes)
    return Observation(**data)


def test_flat_strip_and_u_section_against_independent_area_integral():
    flat = Geometry(wall_height_m=0)
    assert section_properties(flat)["second_moment_m4"] == pytest.approx(flat.base_width_m * flat.thickness_m**3 / 12)
    g = Geometry()
    props = section_properties(g)
    # Integral y² dA about bottom, minus A * ybar².
    bottom_second = g.base_width_m * g.thickness_m**3 / 3 + 2 * g.thickness_m * ((g.thickness_m + g.wall_height_m)**3 - g.thickness_m**3) / 3
    expected = bottom_second - props["area_m2"] * props["neutral_axis_m"]**2
    assert props["second_moment_m4"] == pytest.approx(expected)
    assert props["developed_area_m2"] == pytest.approx(0.16 * 0.045)


def test_cantilever_solution_and_load_validation():
    g = Geometry(wall_height_m=0)
    model = ModelParameters(youngs_modulus_pa=2e9, rotational_compliance_rad_per_nm=0)
    expected = 0.01 * 0.06**3 / (3 * 2e9 * (g.base_width_m * g.thickness_m**3 / 12))
    assert predict_deflection(g, model, 0.01, 0.06)["displacement_m"] == pytest.approx(expected)
    for force, position in [(0, 0.06), (1, 1), (math.nan, 0.06)]:
        with pytest.raises(ValueError):
            predict_deflection(g, model, force, position)


def test_single_station_is_unidentifiable_even_with_force_repeats():
    fit = fit_model([observation(0.12, force_n=f) for f in (0.1, 0.1, 0.3, 0.3)], Protocol())
    assert fit["status"] == "insufficient"
    assert fit["parameters"] is None
    assert choose_diagnostic_position([observation(0.12)], Geometry(), Protocol()) == 0.06


def test_fit_recovers_parameters_and_does_not_consume_reserved_measurements():
    observations = [observation(a, force_n=f) for a in (0.06, 0.12) for f in (0.15, 0.3)]
    observations += [observation(0.09, purpose="reserved", displacement_m=0.004)]
    fit = fit_model(observations, Protocol())
    assert fit["status"] == "identifiable"
    assert fit["parameters"]["youngs_modulus_pa"] == pytest.approx(1.5e9)
    assert fit["parameters"]["rotational_compliance_rad_per_nm"] == pytest.approx(0.2)
    assert observations[-1].id not in fit["source_observation_ids"]
    assert fit["uncertainty"]["youngs_modulus_pa_std"] > 0


def test_nearby_stations_rejected_by_dimensionless_condition_check():
    fit = fit_model([observation(0.12), observation(0.120001)], Protocol())
    assert fit["status"] == "ill_conditioned"
    assert fit["parameters"] is None


@pytest.mark.parametrize("bad", [
    {"fixture_motion_m": 0.001}, {"residual_m": 0.001}, {"quality_flags": ("tracking_lost",)},
    {"uncertainty_m": 0.01}, {"dwell_s": 1},
])
def test_invalid_evidence_cannot_calibrate_model(bad):
    invalid = observation(0.06, **bad)
    fit = fit_model([observation(0.12), invalid], Protocol())
    assert fit["status"] == "insufficient"
    assert invalid.id not in fit["source_observation_ids"]
    assert fit["excluded_observations"][0]["reasons"]


def test_nonproportional_response_marks_model_inadequate():
    data = [observation(0.06), observation(0.12), observation(0.12, displacement_m=0.003)]
    fit = fit_model(data, Protocol())
    assert fit["status"] == "inadequate"
    assert fit["parameters"] is None


def test_simulation_and_physical_records_cannot_be_pooled():
    fit = fit_model([observation(0.06), observation(0.12, source="physical")], Protocol())
    assert fit["status"] == "inadequate"


def test_optimizer_checks_grid_and_does_not_claim_physical_pass():
    reference, protocol, parameters = Geometry(), Protocol(), ModelParameters()
    result = optimize_geometry(reference, parameters, protocol)
    assert result["geometry"]
    candidate = Geometry(**result["geometry"])
    assert result["evaluations"] == 26 * 11
    assert candidate.length_m == reference.length_m
    assert candidate.thickness_m == reference.thickness_m
    assert result["prediction"]["displacement_m"] + result["measurement_margin_m"] <= protocol.max_deflection_m
    assert result["physical_pass"] is False
    assert result["developed_area_m2"] < section_properties(reference)["developed_area_m2"]


def test_template_preserves_physical_scale_and_escapes_material_text():
    svg = generate_template(Geometry(material_batch='<test & "batch">'))
    root = ElementTree.fromstring(svg)
    assert root.attrib["width"].endswith("mm")
    assert "50 mm" in svg
    assert "&lt;test &amp;" in svg
    assert "stroke-dasharray" in svg


@pytest.mark.parametrize("family,height", [(f,h) for f in ("u_channel","solid_bar","rectangular_tube") for h in (0.005,0.01,0.015)] + [("u_channel",0)])
def test_print_mesh_is_watertight_oriented_and_has_correct_volume(family, height):
    g = Geometry(section_family=family, wall_height_m=height, thickness_m=0.0012)
    stl = generate_stl(g)
    vertices = [tuple(map(float, line.split()[1:])) for line in stl.splitlines() if line.strip().startswith("vertex ")]
    faces = [vertices[i:i+3] for i in range(0, len(vertices), 3)]
    edges = Counter()
    oriented_edges = Counter()
    volume_mm3 = 0
    for a, b, c in faces:
        for p, q in ((a, b), (b, c), (c, a)):
            edges[tuple(sorted((p, q)))] += 1
            oriented_edges[(p, q)] += 1
        cross = (b[1] * c[2] - b[2] * c[1], b[2] * c[0] - b[0] * c[2], b[0] * c[1] - b[1] * c[0])
        volume_mm3 += sum(x*y for x, y in zip(a, cross)) / 6
    assert all(count == 2 for count in edges.values())
    assert all(oriented_edges[(p, q)] == oriented_edges[(q, p)] == 1 for p, q in oriented_edges)
    assert volume_mm3 > 0
    assert volume_mm3 * 1e-9 == pytest.approx(section_properties(g)["volume_m3"])
    assert max(v[0] for v in vertices) == pytest.approx(g.length_m * 1000)
    assert "thickness = 1.2;" in generate_scad(g)


@pytest.mark.parametrize("family", ["solid_bar", "rectangular_tube"])
def test_rectangular_sections_against_closed_form(family):
    g = Geometry(section_family=family)
    b,h,t = g.base_width_m,g.wall_height_m+g.thickness_m,g.thickness_m
    expected_area = b*h
    expected_i = b*h**3/12
    if family == "rectangular_tube":
        expected_area -= (b-2*t)*(h-2*t)
        expected_i -= (b-2*t)*(h-2*t)**3/12
    actual = section_properties(g)
    assert actual["area_m2"] == pytest.approx(expected_area)
    assert actual["second_moment_m4"] == pytest.approx(expected_i)


@pytest.mark.parametrize("family", ["u_channel", "solid_bar", "rectangular_tube"])
def test_optimizer_minimizes_actual_volume_across_full_grid(family):
    g,p,m = Geometry(section_family=family),Protocol(),ModelParameters()
    result = optimize_geometry(g,m,p)
    volumes = []
    for width_mm in range(15,41):
        for height_mm in range(5,16):
            c = g.model_copy(update={"base_width_m":width_mm/1000,"wall_height_m":height_mm/1000})
            d = predict_deflection(c,m,p.proof_force_n,p.proof_position_m)["displacement_m"]
            if d + 2*p.max_uncertainty_m <= p.max_deflection_m:
                volumes.append(section_properties(c)["volume_m3"])
    assert result["volume_m3"] == pytest.approx(min(volumes))


def test_cannot_pool_different_section_families():
    data = [observation(.06),observation(.12,geometry=Geometry(section_family="solid_bar"))]
    assert fit_model(data,Protocol())["status"] == "inadequate"
