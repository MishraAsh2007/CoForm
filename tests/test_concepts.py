"""Concept drafting: the agents choose tested parts and numbers, code assembles the millimetres."""
import json
import time
from pathlib import Path

import pytest

from coform import concepts
from coform import freeform as ff
from coform.designer import GEOMETRY_GATES, PROGRESS, Designer
from coform.fea import find_tools
from coform.providers import ProviderError, ProviderResponse

needs_solver = pytest.mark.skipif(find_tools() is None, reason="Gmsh and CalculiX are not installed (bundled with FreeCAD 1.x)")
BOTTLE = {"mount": "desk_edge", "holder": "cup", "name": "Desk bottle holder", "summary": "Clamp on the desk edge, cup beside it.",
          "payload": "1 L steel bottle", "numbers": {"desk_thick_mm": 25, "diameter_mm": 75, "mass_kg": 1.5},
          "requirements": {"max_displacement_mm": 2, "safety_factor": 2}}


@pytest.fixture(autouse=True)
def bounded_test_iterations(monkeypatch):
    for name, value in {"MAX_DRAFT_ATTEMPTS": 4, "MAX_ROUNDS": 2, "MAX_REVIEW_CYCLES": 2, "MAX_STALLED_ROUNDS": 1}.items():
        monkeypatch.setenv("COFORM_DESIGN_" + name, str(value))


# ---- assembly ---------------------------------------------------------------------------------
def test_a_concept_becomes_a_spec_built_from_components_with_its_numbers_fixed():
    raw, intent, notes = concepts.assemble(BOTTLE)
    params = raw["parameters"]
    assert {k: params[k]["value"] for k in ("desk_thick", "bottle_d", "payload_kg")} == {"desk_thick": 25, "bottle_d": 75, "payload_kg": 1.5}
    assert all(p["role"] == "fixed" for p in params.values())
    assert [c["type"] for c in raw["components"]] == ["desk_clamp", "cup", "join"] and raw["features"] == []
    assert "height 200 mm" in notes[0]  # what the request left out is said, not hidden
    assert "desk" in intent["load_path"] and intent["assumptions"] == notes
    spec = ff.DesignSpec.model_validate(raw)
    assert "web" in [f.label for f in spec.features]


@pytest.mark.parametrize("change, message", [
    ({"mount": "ceiling"}, "mount must be one of desk_edge, wall"),
    ({"holder": "basket"}, "holder must be one of cup, cradle, hook"),
    ({"numbers": {"diameter_mm": 140}}, r"numbers.diameter_mm = 140 is outside the tested range 20 to 105"),
    ({"numbers": {"strap_w": 25}}, r"numbers \['strap_w'\] do not apply to desk_edge \+ cup"),
    ({"numbers": {"desk_thick_mm": "thick"}}, "numbers.desk_thick_mm must be a number"),
])
def test_a_concept_outside_the_tested_parts_says_what_to_change(change, message):
    with pytest.raises(ValueError, match=message):
        concepts.assemble({**BOTTLE, **change})


def test_the_users_own_limits_win_over_the_agents_reading():
    raw, _, notes = concepts.assemble(BOTTLE, {"load_n": 30, "max_displacement_mm": 0.5, "max_mass_g": 120})
    assert raw["parameters"]["payload_kg"]["value"] == pytest.approx(30 / 9.81, rel=1e-3)
    assert raw["requirements"] == {"max_displacement_mm": 0.5, "safety_factor": 2.0, "max_mass_g": 120.0}
    assert any("30 N" in n for n in notes)


def test_the_catalogue_offers_only_tested_combinations():
    offered = concepts.catalogue()
    assert set(offered["combinations"]) == {f"{m} + {h}" for m, h in concepts.ASSEMBLIES}
    assert offered["holders"]["cup"]["numbers"]["diameter_mm"] == {"default": 75, "min": 20, "max": 105}
    assert len(concepts.CONCEPT_RULES) < 2500  # a short prompt for a short reply


@needs_solver
@pytest.mark.parametrize("mount, holder, numbers", [
    *[(m, h, {}) for m, h in sorted(concepts.ASSEMBLIES)],
    ("desk_edge", "cup", {"desk_thick_mm": 60, "diameter_mm": 105}),
    ("desk_edge", "hook", {"desk_thick_mm": 10, "width_mm": 60, "loop_depth_mm": 60}),
    ("wall", "cradle", {"diameter_mm": 80, "length_mm": 300}),  # its saddle once reached the plate's lower screw
])
def test_every_offered_combination_passes_the_geometry_gates(tmp_path, mount, holder, numbers):
    raw, _, _ = concepts.assemble({"mount": mount, "holder": holder, "numbers": numbers})
    spec = ff.DesignSpec.model_validate(raw)
    result = ff.analyze(spec, ff.values_of(spec), tmp_path)
    assert all(c["passed"] for c in result["checks"] if c["name"] in GEOMETRY_GATES), \
        [(c["name"], c.get("detail")) for c in result["checks"] if not c["passed"]]


# ---- the agents, faked ------------------------------------------------------------------------
class Team:
    """Fake live models. Mechanics answers the concept call; fabrication objects once if told to."""
    name, model = "fake", "team"

    def __init__(self, concepts_to_send=(BOTTLE,), objection=False):
        self.concepts, self.objection, self.calls = list(concepts_to_send), objection, []

    def complete_json(self, system, payload, max_output_tokens):
        if "concept" in payload:  # a concept review
            role = "fabrication" if "fabrication specialist" in system else "measurement"
            self.calls.append(("concept_review", role, payload))
            if role == "fabrication" and self.objection and not any(c[0] == "concept_review" and c[1] == role for c in self.calls[:-1]):
                return ProviderResponse({"verdict": "revise", "issues": [{"severity": "must_fix", "problem": "A 105 mm cup will not drain",
                                                                          "change": "Use the bottle's real 75 mm diameter"}]}, 200, 60)
            return ProviderResponse({"verdict": "approve", "issues": [{"severity": "note", "problem": "Orient the cup upright"}]}, 200, 40)
        if "design_request" in payload:
            self.calls.append(("concept", "mechanics", payload))
            reply = self.concepts.pop(0) if self.concepts else BOTTLE
            if isinstance(reply, Exception):
                raise reply
            return ProviderResponse(reply if reply is None or "concept" in reply or "reason" in reply
                                    else {"concept": reply, "function": "Holds a bottle at the desk edge", "assumptions": ["Desk is solid wood"]}, 300, 250)
        if "request" in payload:
            self.calls.append(("freehand", "mechanics", payload))
            return ProviderResponse({"spec": json.loads((Path(ff.__file__).with_name("templates") / "shelf_bracket.json").read_text()),
                                     "assumptions": ["Freehand"]}, 500, 900)
        if "part" in payload:
            self.calls.append(("draft_review", None, payload))
            return ProviderResponse({"verdict": "approve", "issues": []}, 300, 40)
        if "proposals" in payload:
            return ProviderResponse({"reviews": [{"proposal_id": p["id"], "support": True, "reason": "ok"} for p in payload["proposals"]]}, 300, 80)
        if "measurement specialist" in system:
            return ProviderResponse({"test_plan": "Clamp it to a desk, hang the bottle, read a dial gauge.", "concerns": []}, 300, 60)
        return ProviderResponse({"changes": None, "reason": "Already good enough"}, 300, 40)

    def kinds(self):
        return [c[0] for c in self.calls]


def _wait(project_id):
    for _ in range(1200):
        if not PROGRESS[project_id]["running"]:
            return
        time.sleep(0.5)
    raise TimeoutError


def _run(tmp_path, monkeypatch, team, prompt="Hold my 1 L water bottle on the edge of my 25 mm desk."):
    from coform.service import CoForm
    service = CoForm(tmp_path)
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: team for r in ("mechanics", "fabrication", "measurement")} if live else None)
    designer = Designer(service)
    pid = designer.create(prompt, {}, live=True)["project"]["id"]
    _wait(pid)
    return service, designer.state(pid)


@needs_solver
def test_the_team_agrees_a_concept_and_code_builds_it_without_a_freehand_draft(tmp_path, monkeypatch):
    team = Team(objection=True)
    service, state = _run(tmp_path, monkeypatch, team)
    try:
        log = [e["message"] for e in state["progress"]["log"]]
        assert "freehand" not in team.kinds()  # nobody wrote coordinates
        # Fabrication objected before anything was built; mechanics revised once with that objection in hand.
        concept_calls = [c for c in team.calls if c[0] == "concept"]
        assert len(concept_calls) == 2 and concept_calls[1][2]["objections"][0]["agent"] == "fabrication"
        assert [c["status"] for c in state["concepts"]] == ["revise", "accepted"]
        assert state["concepts"][-1]["usage"]["calls"] == 3  # one concept call and two short reviews
        first = state["attempts"][0]
        assert first["repair_source"] == "concept" and first["concept_id"] == state["concepts"][-1]["id"]
        assert first["status"] == "accepted" and "draft_review" in team.kinds()  # steps 2 and 3 ran unchanged
        spec = state["specs"][0]
        assert spec["source"] == "llm" and spec["concept_id"] == state["concepts"][-1]["id"]
        assert "Desk is solid wood" in spec["assumptions"] and "height 200 mm" in " ".join(spec["assumptions"])
        assert state["tests"][0]["results"]["passed"] is not None and state["rounds"]  # optimisation ran on it
        assert any(m.startswith("Concept: desk_edge + cup") for m in log)
    finally:
        service.store.close()


@needs_solver
def test_a_concept_code_rejects_goes_back_as_an_objection_not_coordinates(tmp_path, monkeypatch):
    team = Team(concepts_to_send=[{**BOTTLE, "numbers": {"desk_thick_mm": 25, "diameter_mm": 140}}, BOTTLE])
    service, state = _run(tmp_path, monkeypatch, team)
    try:
        second = [c for c in team.calls if c[0] == "concept"][1][2]
        assert second["objections"][0]["agent"] == "code" and "outside the tested range" in second["objections"][0]["problem"]
        assert second["previous_concept"]["numbers"]["diameter_mm"] == 140
        assert [c["status"] for c in state["concepts"]] == ["rejected", "accepted"] and "freehand" not in team.kinds()
    finally:
        service.store.close()


@needs_solver
def test_when_no_tested_part_fits_the_part_is_drawn_freehand(tmp_path, monkeypatch):
    team = Team(concepts_to_send=[{"concept": None, "reason": "A cable tray needs a long U channel"}])
    monkeypatch.setattr(Designer, "_optimize", lambda self, *a, **k: None)
    service, state = _run(tmp_path, monkeypatch, team, prompt="A tray under my desk for cables.")
    try:
        assert team.kinds()[:2] == ["concept", "freehand"]
        assert state["concepts"][0]["status"] == "declined" and "U channel" in state["concepts"][0]["errors"][0]
        assert "repair_source" not in state["attempts"][0] or state["attempts"][0]["repair_source"] != "concept"
    finally:
        service.store.close()


@needs_solver
def test_a_lost_concept_call_is_asked_once_more_then_falls_back_to_freehand(tmp_path, monkeypatch):
    timeout = ProviderError("nvidia request failed (ReadTimeout); no retry was made")
    team = Team(concepts_to_send=[timeout, timeout])
    monkeypatch.setattr(Designer, "_optimize", lambda self, *a, **k: None)
    service, state = _run(tmp_path, monkeypatch, team)
    try:
        assert team.kinds()[:3] == ["concept", "concept", "freehand"]
        assert [c["status"] for c in state["concepts"]] == ["provider_error", "provider_error"]
        assert "ReadTimeout" in state["concepts"][0]["errors"][0]
    finally:
        service.store.close()

