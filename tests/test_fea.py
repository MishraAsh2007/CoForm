import subprocess

import pytest

from coform import fea
from coform.domain import Geometry, ModelParameters, Protocol
from coform.mechanics import predict_deflection

TOOLS = fea.find_tools()
pytestmark = pytest.mark.skipif(TOOLS is None, reason="Gmsh and CalculiX are not installed (bundled with FreeCAD 1.x)")
STATIONS = [0.06, 0.12]


def beam(g, a, E=2.5e9):
    return predict_deflection(g, ModelParameters(youngs_modulus_pa=E), 1.0, a)["displacement_m"]


def rig(fixture, tmp_path, g=Geometry(), **kw):
    return fea.solve(g, modulus_pa=2.5e9, poisson=0.35, fixture=fixture, stations_m=STATIONS, cache_dir=tmp_path, **kw)


def test_rigid_clamp_agrees_with_beam_theory_plus_shear(tmp_path):
    result = rig("rigid_vise", tmp_path)
    ratio = fea.compliance_at(result, 0.12) / beam(Geometry(), 0.12)
    assert 1.0 < ratio < 1.08  # 3D solid: slightly more compliant than Euler-Bernoulli
    assert rig("rigid_vise", tmp_path) == result  # second call comes from the cache


def test_clamp_looseness_emerges_from_jaw_material(tmp_path):
    rigid, printed, rubber = (rig(f, tmp_path) for f in ("rigid_vise", "printed_jaws", "rubber_lined"))
    for a in STATIONS:
        assert fea.compliance_at(rigid, a) < fea.compliance_at(printed, a) < fea.compliance_at(rubber, a)
    # Mount rotation grows as a^2 and bending as a^3, so the extra share is larger near the clamp.
    extra = {a: fea.compliance_at(rubber, a) / fea.compliance_at(rigid, a) for a in STATIONS}
    assert extra[0.06] > extra[0.12] > 1.1


def test_a_turned_cad_step_is_reoriented_and_matches_the_generated_section(tmp_path):
    geo = tmp_path / "turned.geo"
    geo.write_text('SetFactory("OpenCASCADE");\nBox(1) = {0,0,0, 160,25,1.2};\nBox(2) = {0,0,1.2, 160,1.2,10};\n'
                   'Box(3) = {0,23.8,1.2, 160,1.2,10};\nBooleanUnion{ Volume{1}; Delete; }{ Volume{2,3}; Delete; }\n'
                   'Rotate {{1,0,0},{0,0,0},Pi/2} { Volume{1}; }\nTranslate {7,-3,40} { Volume{1}; }\nSave "turned.step";\n')
    subprocess.run([TOOLS["gmsh"], "turned.geo", "-0", "-v", "1"], cwd=tmp_path, check=True, capture_output=True)
    from_cad = rig("rigid_vise", tmp_path, step=tmp_path / "turned.step")
    generated = rig("rigid_vise", tmp_path)
    assert from_cad["geometry_source"] == "cad_step"
    assert fea.compliance_at(from_cad, 0.12) == pytest.approx(fea.compliance_at(generated, 0.12), rel=0.01)


def test_a_step_of_a_different_part_is_refused(tmp_path):
    geo = tmp_path / "other.geo"
    geo.write_text('SetFactory("OpenCASCADE");\nBox(1) = {0,0,0, 160,30,12};\nSave "other.step";\n')
    subprocess.run([TOOLS["gmsh"], "other.geo", "-0", "-v", "1"], cwd=tmp_path, check=True, capture_output=True)
    with pytest.raises(fea.GeometryMismatch):
        rig("rigid_vise", tmp_path, step=tmp_path / "other.step")


def test_simulated_study_takes_measurements_from_the_rig_and_hides_the_truth(tmp_path):
    from coform.service import CoForm
    from coform.validation import RIG_SCENARIOS
    scenario = RIG_SCENARIOS[0]
    service = CoForm(tmp_path)
    try:
        run = service.create_run(scenario=scenario, protocol=Protocol(**scenario.protocol_overrides), seed=3)["run"]["id"]
        final = service.demo(run)
        notes = {o["human_notes"] for o in final["observations"]}
        assert all("Virtual test rig" in n for n in notes)
        modulus = fea.draw_modulus(scenario.material, 3)
        assert not any(f"{modulus:.4g}" in n or f"{modulus / 1e9:.3g}" in n for n in notes)
        low, high = fea.MATERIALS[scenario.material]["tensile_modulus_pa"]
        assert low <= modulus <= high
        fitted = final["model"]["parameters"]["youngs_modulus_pa"]
        assert 0.7 * modulus < fitted < 1.3 * modulus  # the beam model recovers the rig's stiffness
    finally:
        service.store.close()
