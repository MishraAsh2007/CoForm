"""Regression protection with saved replies and fake agents; no live model calls."""
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from coform import freeform as ff, scene
from coform.design_geometry import axial_span, inside_feature, removed_contact, relationship_check, verify_claim
from coform.design_search import checkpoint_from, geometry_key
from coform.designer import GEOMETRY_GATES, ROLES, _example
from coform.designer_repair import apply_repair, assess_review, evidence_for, repair_from_spec
from coform.fea import find_tools
from test_designer_repair import Agent, approve, designer, project, result


def captured():
    return json.loads((Path(__file__).parent / "fixtures" / "repair_regression.json").read_text())


def clearance_review():
    return {"verdict": "revise", "issues": [{"severity": "must_fix", "problem": "Insufficient clearance",
        "evidence_ids": ["features:3"], "evidence": "Required radius is 2.7 mm",
        "claim": {"kind": "comparison", "left": "measure:features:3:radius", "op": "lt", "right": 2.7}}]}


def test_bad_repair_rolls_back_and_next_agent_edits_checkpoint(designer, monkeypatch):
    p = project(designer)
    built = []
    def analyze(spec, values, folder):
        built.append(spec.features[3].radius)
        r = result(spec, values, folder)
        if spec.features[3].radius == 2.6:
            next(c for c in r["checks"] if c["name"] == "contact").update(passed=False, value=1, detail="Contact destroyed")
        return r
    drafts = 0
    def reply(system, payload):
        nonlocal drafts
        if "request" not in payload:
            return clearance_review() if payload["part"]["features"][3]["radius_mm"] < 2.7 else approve(system, payload)
        drafts += 1
        if drafts == 1:
            return {"spec": _example()}
        assert payload["previous_spec"]["features"][3]["radius"] == 2.5
        assert payload["checkpoint"]["candidate_id"]
        if drafts == 3:
            assert payload["failure_history"][-1]["rollback_to"] == payload["checkpoint"]["candidate_id"]
        return {"repair": {"features": [{"index": 3, "changes": {"radius": 2.6 if drafts == 2 else 2.7}}]}}
    monkeypatch.setattr(ff, "analyze", analyze)
    agent = Agent(reply)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, agent))
    spec, *_ = designer._draft(p, True)
    assert spec.features[3].radius == 2.7 and built == [2.5, 2.6, 2.7]
    attempts = designer._attempts(p["id"])
    assert [a["status"] for a in attempts] == ["review_rejected", "regression_rejected", "accepted"]
    assert attempts[1]["rollback_to"] == attempts[0]["candidate_id"]
    assert attempts[0]["geometry_checkpoint"] and not attempts[1]["geometry_checkpoint"]
    assert len({a["folder"] for a in attempts}) == 3


def test_resume_recovers_historical_passing_checkpoint_not_last_failed_branch(designer, monkeypatch):
    p = project(designer)
    good = ff.DesignSpec.model_validate(_example())
    checks = result(good, ff.values_of(good))["checks"]
    base = {"candidate_id": "older_good", "attempt": 1, "spec": good.model_dump(mode="json"), "status": "review_rejected",
            "checks": checks, "reviews": [], "errors": ["Old reviewer concern"], "design_intent": {"function": "Hold"}, "folder": "old", "reply": {}}
    designer._save_attempt(p["id"], base, {}, {}, [])
    bad = apply_repair(good, {"features": [{"index": 3, "changes": {"radius": 2.6}}]})
    designer._save_attempt(p["id"], {**base, "candidate_id": "later_bad", "attempt": 2, "status": "rejected", "checks": [],
                                   "spec": bad.model_dump(mode="json"), "errors": ["Contact lost"]}, {}, {}, [])
    assert checkpoint_from(designer._attempts(p["id"]), GEOMETRY_GATES)["candidate_id"] == "older_good"
    monkeypatch.setattr(ff, "analyze", result)
    reviewer = Agent(approve)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, reviewer))
    spec, *_ = designer._draft(p, True)
    assert spec == good
    assert all("request" not in payload for payload in reviewer.payloads)
    assert len(reviewer.payloads) == 3


def test_repeated_distinct_repairs_to_same_failure_stop_after_three(designer, monkeypatch):
    p = project(designer)
    def broken(spec, values, folder):
        r = result(spec, values, folder)
        next(c for c in r["checks"] if c["name"] == "contact").update(passed=False, value=1, detail="No support")
        return r
    def reply(system, payload):
        if "previous_spec" not in payload:
            return {"spec": _example()}
        return {"repair": {"features": [{"index": 3, "changes": {"radius": payload["previous_spec"]["features"][3]["radius"] + 0.1}}]}}
    monkeypatch.setattr(ff, "analyze", broken)
    agent = Agent(reply)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, agent))
    with pytest.raises(RuntimeError, match="stalled after 4"):
        designer._draft(p, True)
    # Three equivalent failures, then one request for a different mechanism, then stop: 4 calls, not 12.
    assert len(agent.payloads) == 4 and not designer.state(p["id"])["specs"]
    assert "strategy" not in agent.payloads[2] and "Return a redesign" in agent.payloads[3]["strategy"]


def test_measured_claim_catches_the_saved_review_arithmetic_error():
    saved = captured()
    spec = ff.DesignSpec.model_validate(saved["initial_spec"])
    evidence = evidence_for(spec, {"checks": saved["initial_checks"]})
    review = saved["initial_reviews"][1]
    # The legacy review merely cited existing features. That no longer authorizes an edit.
    assert assess_review("fabrication", review, None, evidence, spec)["verdict"] == "needs_evidence"
    issue = review["issues"][0]
    issue["claim"] = {"kind": "axial_span", "cut": 9, "solid": 3, "axis": "z", "expected": False}
    checked = assess_review("fabrication", review, None, evidence, spec)
    assert checked["verdict"] == "needs_evidence"
    assert checked["issues"][0]["claim_result"]["status"] == "contradicted"
    assert checked["issues"][0]["claim_result"]["facts"]["cut_interval_mm"] == [-64, -59]


def test_false_claim_is_clarified_without_redrafting(designer, monkeypatch):
    p = project(designer)
    monkeypatch.setattr(ff, "analyze", result)
    def reply(system, payload):
        if "request" in payload:
            return {"spec": _example()}
        if "clarify_review" in payload:
            proof = payload["clarify_review"]["issues"][0]["claim_result"]
            assert proof["status"] == "contradicted" and proof["facts"]["left"] == 2.5
            return approve(system, payload)
        r = clearance_review()
        r["issues"][0]["claim"]["right"] = 2
        return r
    agent = Agent(reply)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, agent))
    designer._draft(p, True)
    assert sum("request" in p for p in agent.payloads) == 1
    assert len(designer._attempts(p["id"])) == 1


@pytest.mark.parametrize("claim", [None, {}, {"kind": "comparison", "left": 1, "op": "lt", "right": 2},
    {"kind": "comparison", "left": "measure:missing", "op": "lt", "right": 2},
    {"kind": "comparison", "left": "measure:x", "op": "lt", "right": float("nan")},
    {"kind": "axial_span", "cut": -1, "solid": 0, "axis": "z", "expected": False}])
def test_unverifiable_claims_do_not_authorize_repairs(claim):
    spec = ff.DesignSpec.model_validate(_example())
    assert verify_claim(claim, {"measure:x": 1}, spec)["status"] == "unverified"


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 0, 1), (1, 2, 0)])
def test_contact_cut_diagnostic_and_through_proof_generalize_across_axes_and_names(order):
    saved = captured()
    good = ff.DesignSpec.model_validate(saved["initial_spec"])
    bad = apply_repair(good, saved["bad_repair"])
    mapping = {axis: "xyz"[order.index(i)] for i, axis in enumerate("xyz")}
    def transform(spec):
        raw = spec.model_dump(mode="json")
        for section in ("features", "environment"):
            for i, item in enumerate(raw[section]):
                for field in ("at", "size", "force_n"):
                    if item.get(field):
                        item[field] = [item[field][k] for k in order]
                item["axis"] = mapping[item["axis"]]
                if section == "features":
                    item["label"] = f"piece_{i}"
                else:
                    for field in ("touch", "retain"):
                        item[field] = [d[0] + mapping[d[1]] for d in item[field]]
                    if item.get("insert"):
                        item["insert"] = item["insert"][0] + mapping[item["insert"][1]]
                    if item["role"] == "payload":
                        item["name"] = "cargo"
        return ff.DesignSpec.model_validate(raw)
    good, bad = transform(good), transform(bad)
    axis = mapping["z"]
    assert axial_span(good, 9, 3, axis)["spans"]
    # The same relative-face constraints work on any axis and arbitrary labels.
    linked = good.model_dump(mode="json")
    linked["relationships"] = [{"left_feature": "piece_9", "left_bound": "max", "op": "ge",
                                "right_feature": "piece_3", "right_bound": "max", "axis": axis}]
    linked = ff.DesignSpec.model_validate(linked)
    assert relationship_check(linked, ff.values_of(linked))["passed"]
    with pytest.raises(ValueError, match="relationships"):
        apply_repair(linked, {"features": [{"index": 9, "changes": {"length": 2}}]})
    values = ff.values_of(bad)
    body = next(s for s in scene.resolve(bad, values) if s.body.role == "payload")
    diagnosis = removed_contact(bad, values, body, "-" + axis)
    assert diagnosis["cut_features"] == [8] and diagnosis["support_features"] == [3]
    assert diagnosis["removed_samples"] == diagnosis["samples"]
    assert "piece_8" in diagnosis["detail"] and "cargo" in diagnosis["detail"]


def test_contact_diagnostic_does_not_blame_a_cut_that_was_filled_again():
    saved = captured()
    good = ff.DesignSpec.model_validate(saved["initial_spec"])
    bad = apply_repair(good, saved["bad_repair"]).model_dump(mode="json")
    bad["features"].append(deepcopy(bad["features"][3]))
    restored = ff.DesignSpec.model_validate(bad)
    values = ff.values_of(restored)
    body = next(s for s in scene.resolve(restored, values) if s.body.role == "payload")
    assert removed_contact(restored, values, body, "-z") is None


def test_point_membership_handles_rotation_and_signed_wedges():
    box = ff.Feature(shape="box", at=[0, 0, 0], size=[10, 2, 2], rotate={"axis": "z", "degrees": 90})
    assert inside_feature(box, {}, np.array([[-1, 5, 1], [5, 1, 1]])).tolist() == [True, False]
    wedge = ff.Feature(shape="wedge", at=[0, 0, 0], legs=[{"axis": "x", "length": -10}, {"axis": "z", "length": 10}], thickness=-2)
    assert inside_feature(wedge, {}, np.array([[-2, -1, 2], [-9, -1, 9]])).tolist() == [True, False]


def test_evaluated_geometry_deduplication_ignores_expression_spelling():
    spec = ff.DesignSpec.model_validate(_example())
    raw = spec.model_dump(mode="json")
    raw["features"][0]["at"] = ["0", "1-1", 0]
    raw["features"][0]["label"] = "Cosmetic rename"
    assert geometry_key(spec) == geometry_key(ff.DesignSpec.model_validate(raw))


def test_axis_names_are_not_evaluated_as_parameter_names():
    raw = _example()
    raw["parameters"].update(x={"value": 5, "role": "fixed"}, y={"value": 5, "role": "fixed"})
    first = ff.DesignSpec.model_validate(raw)
    raw["features"][3]["axis"] = "y"
    assert geometry_key(first) != geometry_key(ff.DesignSpec.model_validate(raw))


def test_a_genuine_short_cut_is_verified_but_rotated_proofs_remain_unknown():
    raw = captured()["initial_spec"]
    raw["features"][9]["length"] = 2
    spec = ff.DesignSpec.model_validate(raw)
    claim = {"kind": "axial_span", "cut": 9, "solid": 3, "axis": "z", "expected": False}
    assert verify_claim(claim, {}, spec)["status"] == "verified"
    raw["features"][9]["rotate"] = {"axis": "y", "degrees": 30}
    assert verify_claim(claim, {}, ff.DesignSpec.model_validate(raw))["status"] == "unverified"


@pytest.mark.skipif(find_tools() is None, reason="Requires local Gmsh and CalculiX")
def test_saved_passing_candidate_and_destructive_repair_with_real_cad(tmp_path):
    saved = captured()
    spec = ff.DesignSpec.model_validate(saved["initial_spec"])
    good = ff.analyze(spec, ff.values_of(spec), tmp_path / "checkpoint")
    assert good["passed"]
    assert next(b for b in good["scene"] if b["role"] == "payload")["touched_faces"]["-z"] > 0
    broken = apply_repair(spec, saved["bad_repair"])
    with pytest.raises(ff.BuildError, match="remove material") as exc:
        ff.analyze(broken, ff.values_of(broken), tmp_path / "failed_branch")
    evidence = exc.value.evidence
    assert next(c for c in evidence["checks"] if c["name"] == "contact")["passed"] is False
    d = next(b for b in evidence["scene"] if b["role"] == "payload")["contact_diagnostics"][0]
    assert d["cut_features"] == [8] and d["support_features"] == [3]


def test_component_relationships_preserve_floor_and_drain_with_parameter_changes():
    from test_components import bottle_holder
    spec = ff.DesignSpec.model_validate(bottle_holder())
    for value in (2, 4, 6):
        repaired = apply_repair(spec, {"parameters": {"cup_floor_t": value}})
        assert relationship_check(repaired, ff.values_of(repaired))["passed"]
    index = next(i for i, f in enumerate(spec.features) if f.label == "cup void")
    floor = next(f for f in spec.features if f.label == "cup floor")
    with pytest.raises(ValueError, match="relationships"):
        apply_repair(spec, {"features": [{"index": index, "changes": {"at": list(floor.at)}}]})
    raw = spec.model_dump(mode="json")
    raw["relationships"] = []
    with pytest.raises(ValueError, match="preserve component relationships"):
        repair_from_spec(spec, raw)



def test_a_worse_repair_never_replaces_a_better_draft_before_any_checkpoint(designer, monkeypatch):
    p = project(designer)
    failing = {2.5: ["contact"], 2.6: ["contact", "fit"], 2.7: []}  # the first repair makes things worse
    def analyze(spec, values, folder):
        r = result(spec, values, folder)
        for name in failing[spec.features[3].radius]:
            next(c for c in r["checks"] if c["name"] == name).update(passed=False, value=1, detail=f"{name} lost")
        return r
    def reply(system, payload):
        if "request" not in payload:
            return approve(system, payload)
        if "previous_spec" not in payload:
            return {"spec": _example()}
        assert payload["previous_spec"]["features"][3]["radius"] == 2.5  # never the worse branch
        drafts = sum("request" in x for x in agent.payloads)
        return {"repair": {"features": [{"index": 3, "changes": {"radius": 2.6 if drafts == 2 else 2.7}}]}}
    monkeypatch.setattr(ff, "analyze", analyze)
    agent = Agent(reply)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, agent))
    spec, *_ = designer._draft(p, True)
    attempts = designer._attempts(p["id"])
    assert spec.features[3].radius == 2.7 and [a["status"] for a in attempts] == ["rejected", "rejected", "accepted"]
    assert attempts[1]["rollback_to"] == attempts[0]["candidate_id"] and "rollback_to" not in attempts[0]


def test_the_checkpoint_with_fewer_verified_problems_is_kept(designer, monkeypatch):
    p = project(designer)
    def review(system, payload):
        radius = payload["part"]["features"][3]["radius_mm"]
        if radius >= 2.7:
            return approve(system, payload)
        r = clearance_review()
        if radius == 2.55:  # this repair passes the checks but reviewers verify a second problem
            extra = deepcopy(r["issues"][0])
            extra.update(problem="Wall too close", claim={"kind": "comparison", "left": "measure:features:3:radius", "op": "lt", "right": 2.6})
            r["issues"].append(extra)
        return r
    def reply(system, payload):
        if "request" not in payload:
            return review(system, payload)
        if "previous_spec" not in payload:
            return {"spec": _example()}
        assert payload["previous_spec"]["features"][3]["radius"] == 2.5  # the better checkpoint, both times
        drafts = sum("request" in x for x in agent.payloads)
        return {"repair": {"features": [{"index": 3, "changes": {"radius": 2.55 if drafts == 2 else 2.7}}]}}
    monkeypatch.setattr(ff, "analyze", result)
    agent = Agent(reply)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, agent))
    spec, *_ = designer._draft(p, True)
    attempts = designer._attempts(p["id"])
    assert spec.features[3].radius == 2.7
    assert attempts[1]["kept_checkpoint"] == attempts[0]["candidate_id"] and attempts[1]["geometry_checkpoint"]
    # On resume, the better checkpoint is chosen too, although a later one also passed the checks.
    assert checkpoint_from(attempts[:2], GEOMETRY_GATES)["candidate_id"] == attempts[0]["candidate_id"]


def test_a_support_that_stops_short_is_named_with_its_distance():
    spec = ff.DesignSpec.model_validate(_example())
    values = ff.values_of(spec)
    shelf = next(s for s in scene.resolve(spec, values) if s.name == "shelf")
    below = np.array([[x, y, 77.0] for x in range(5, 100, 10) for y in range(2, 24, 5)], dtype=float)  # 3 mm under the shelf
    found = scene.nearest_material(spec, values, shelf, "-z", below)
    assert found["gap_mm"] == 3.0 and "under 'shelf'" in found["detail"] and "3.0 mm away" in found["detail"]
    assert scene.nearest_material(spec, values, shelf, "-z", below + [0, 0, 200])["gap_mm"] is None
