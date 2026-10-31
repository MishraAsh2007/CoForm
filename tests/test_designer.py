import json
import time

import pytest

from scienceclaw import freeform as ff
from scienceclaw.designer import PROGRESS, Designer, _example, template_spec
from scienceclaw.fea import find_tools
from scienceclaw.providers import ProviderResponse

needs_solver = pytest.mark.skipif(find_tools() is None, reason="Gmsh and CalculiX are not installed (bundled with FreeCAD 1.x)")


def test_expressions_allow_arithmetic_over_parameters_only():
    assert ff.evaluate("max(a, 2) * (b - 1) / 2", {"a": 3, "b": 5}) == 6
    for bad in ("__import__('os')", "a.real", "unknown + 1", "[1][0]"):
        with pytest.raises(ValueError):
            ff.evaluate(bad, {"a": 1})


def test_spec_validation_rejects_bad_ranges_and_a_leading_cut():
    spec = _example()
    spec["parameters"]["plate"]["value"] = 99
    with pytest.raises(ValueError):
        ff.DesignSpec.model_validate(spec)
    spec = _example()
    spec["features"][0]["op"] = "subtract"
    with pytest.raises(ValueError):
        ff.DesignSpec.model_validate(spec)
    spec = ff.DesignSpec.model_validate(_example())
    with pytest.raises(ValueError):
        ff.values_of(spec, {"plate": 50})


@needs_solver
def test_disconnected_pieces_are_rejected_with_a_reason(tmp_path):
    raw = _example()
    raw["features"].append({"label": "floating block", "shape": "box", "at": [300, 0, 0], "size": [5, 5, 5]})
    spec = ff.DesignSpec.model_validate(raw)
    with pytest.raises(ff.BuildError, match="2 separate solids"):
        ff.build(spec, ff.values_of(spec), tmp_path)


@needs_solver
def test_analysis_reports_physics_checks_and_a_3d_field(tmp_path):
    spec = ff.DesignSpec.model_validate(template_spec({"load_n": 30}))
    result = ff.analyze(spec, ff.values_of(spec), tmp_path)
    assert result["loads"][0]["deflection_mm"] > 0 and result["peak_stress_mpa"] > 0
    assert {c["name"] for c in result["checks"]} >= {"deflection", "stress", "min_wall", "build_volume"}
    assert (tmp_path / "part.step").read_bytes().startswith(b"ISO-10303-21")
    assert (tmp_path / "part.stl").stat().st_size > 1000
    field = json.loads((tmp_path / "field.json").read_text())
    assert len(field["positions"]) == len(field["stress"]) == len(field["displacement"]) and field["triangles"]
    # Stiffer resin, same part: less bend (linear elasticity).
    stiffer = ff.analyze(spec, ff.values_of(spec), tmp_path / "stiff", modulus_scale=2.0)
    assert stiffer["loads"][0]["deflection_mm"] == pytest.approx(result["loads"][0]["deflection_mm"] / 2, rel=0.02)


def _wait(project_id):
    for _ in range(600):
        if not PROGRESS[project_id]["running"]:
            return
        time.sleep(0.5)
    raise TimeoutError


@needs_solver
def test_rule_based_designer_tests_adjusts_and_converges(tmp_path):
    from scienceclaw.service import ScienceClaw
    service = ScienceClaw(tmp_path)
    try:
        designer = Designer(service)
        state = designer.create("A wall bracket for a small shelf of books.", {"load_n": 30, "max_displacement_mm": 1.0}, live=False)
        pid = state["project"]["id"]
        _wait(pid)
        state = designer.state(pid)
        first, best = state["tests"][0], state["best"]
        assert not first["results"]["passed"] and best["results"]["passed"]
        assert state["rounds"][-1]["status"] == "converged" and state["progress"]["stage"] == "ready"
        assert all(t["results"]["values"] == t["values"] for t in state["tests"])
        assert designer.artifact(pid, best["id"], "step").read_bytes().startswith(b"ISO-10303-21")
        # A real part that bends more than predicted recalibrates the stiffness downward.
        predicted = best["results"]["loads"][0]["deflection_mm"]
        measured = designer.record_measurement(pid, best["id"], predicted * 1.25, 0.02, "dial gauge")
        assert measured["modulus_scale"] == pytest.approx(0.8, rel=0.02)
        assert measured["recheck"]["loads"][0]["deflection_mm"] == pytest.approx(predicted * 1.25, rel=0.03)
    finally:
        service.store.close()


class Drafter:
    """Fake live model: first a disconnected part, then a valid one; logs what it was told."""
    name, model = "fake", "drafter"

    def __init__(self):
        self.payloads = []

    def complete_json(self, system, payload, max_output_tokens):
        self.payloads.append(payload)
        if "request" in payload:
            spec = _example()
            if len(self.payloads) == 1:
                spec["features"].append({"label": "floating", "shape": "box", "at": [300, 0, 0], "size": [5, 5, 5]})
            return ProviderResponse({"spec": spec, "assumptions": ["Wall is rigid"]}, 500, 900)
        if "proposals" in payload:
            return ProviderResponse({"reviews": [{"proposal_id": p["id"], "support": True, "reason": "ok"} for p in payload["proposals"]]}, 300, 80)
        if "measurement specialist" in system:
            return ProviderResponse({"test_plan": "Clamp the wall plate, hang the load, read a dial gauge at the tip.", "concerns": []}, 300, 60)
        return ProviderResponse({"changes": None, "reason": "Already good enough"}, 300, 40)


@needs_solver
def test_live_drafting_retries_with_the_kernel_reason(tmp_path, monkeypatch):
    from scienceclaw.service import ScienceClaw
    service = ScienceClaw(tmp_path)
    drafter = Drafter()
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: drafter for r in ("mechanics", "fabrication", "measurement")} if live else None)
    try:
        designer = Designer(service)
        pid = designer.create("A wall bracket that holds a plant pot.", {}, live=True)["project"]["id"]
        _wait(pid)
        state = designer.state(pid)
        assert state["specs"][0]["source"] == "llm" and state["specs"][0]["assumptions"] == ["Wall is rigid"]
        retry = [p for p in drafter.payloads if "request" in p][1]
        assert "separate solids" in json.dumps(retry["previous_attempt_errors"])
        assert state["rounds"][0]["status"] == "converged" and "dial gauge" in state["rounds"][0]["notes"]["measurement"]
    finally:
        service.store.close()


def test_fusion_view_job_opens_a_step_without_creating_cad_revisions(tmp_path):
    from scienceclaw.formlabs import Manufacturing
    from scienceclaw.fusion import FusionLink
    from scienceclaw.service import ScienceClaw
    service = ScienceClaw(tmp_path)
    try:
        link = FusionLink(service, Manufacturing(service.store))
        with pytest.raises(ValueError, match="not connected"):
            link.request_view("Hook", "/api/designer/projects/p/tests/t/step")
        assert link.next_job({"addin_version": "0.2.0"}) is None  # the add-in polls
        job = link.request_view("Hook", "/api/designer/projects/p/tests/t/step")
        assert link.next_job()["kind"] == "view_step"
        assert link.submit_result(job["id"], {"document": "Hook"})["status"] == "done"
        assert not service.store.list("designer", "cad_revision")
    finally:
        service.store.close()
