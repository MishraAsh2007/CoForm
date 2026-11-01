"""Repair orchestration tests: deterministic fake agents, no network or model credits."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from coform import freeform as ff
from coform.designer import Designer, PROGRESS, ROLES, _example
from coform.designer_limits import DesignerLimits
from coform.designer_repair import apply_repair, repair_from_spec, assess_review, evidence_for
from coform.providers import ProviderResponse
from coform.store import Store


@pytest.fixture
def designer(tmp_path):
    PROGRESS.pop("part_repair", None)
    store = Store(tmp_path)
    d = Designer(SimpleNamespace(store=store))
    d.limits = DesignerLimits(max_draft_attempts=6, max_rounds=6, max_review_cycles=2, max_stalled_rounds=2)
    yield d
    store.close()
    PROGRESS.pop("part_repair", None)


def project(d, prompt="A clamp that holds a cable and allows insertion from above"):
    return d.store.put("design_project", "designer", {"id": "part_repair", "prompt": prompt,
        "material": "alpha_fast", "constraints": {"safety_factor": 2}, "live": True, "created_at": "now"})


def result(spec, values, folder=None, *, modulus_scale=1):
    bend = (6 / values["plate"]) ** 3
    checks = [{"name": "deflection", "label": "Bend", "passed": bend <= 1, "value": bend, "limit": 1}]
    checks += [{"name": n, "label": n, "passed": True, "value": 0, "limit": 0}
               for n in ("sealed_cavity", "watertight", "mount_clear", "aligned", "fit", "contact", "insertion", "retention")]
    if folder:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "part.step").write_text(str(values))
    return {"values": dict(values), "passed": all(c["passed"] for c in checks), "checks": checks,
            "mass_g": values["plate"] * 10, "loads": [{"deflection_mm": bend}], "peak_stress_mpa": 10,
            "peak_stress_at": [0, 0, 0], "allowable_stress_mpa": 23, "regions": {"supports": [], "loads": []},
            "extents_mm": [100, 25, 80], "files": {"step": "part.step"},
            "modulus_pa": ff.MATERIALS["alpha_fast"]["modulus_pa"] * modulus_scale}


class Agent:
    name, model = "fake", "repair-test"

    def __init__(self, fn):
        self.fn, self.payloads, self.systems = fn, [], []

    def complete_json(self, system, payload, max_output_tokens):
        self.payloads.append(deepcopy(payload))
        self.systems.append(system)
        return ProviderResponse(self.fn(system, payload), 100, 100)


def approve(system, payload):
    return {"verdict": "approve", "issues": []}


def test_patch_preserves_loads_requirements_and_functional_features():
    spec = ff.DesignSpec.model_validate(_example())
    repaired = apply_repair(spec, {"features": [{"index": 3, "changes": {"length": "plate + 4", "at": [-2, "width/2", "height/3"]}}]})
    assert repaired.loads == spec.loads and repaired.requirements == spec.requirements
    assert [f.label for f in repaired.features] == [f.label for f in spec.features]
    assert spec.features[3].length == "plate + 2"


@pytest.mark.parametrize("patch", [
    {"requirements": {"safety_factor": 1}}, {"loads": [{"index": 0, "changes": {"force_n": [0, 0, -1]}}]},
    {"features": [{"index": 3, "changes": {"op": "add"}}]}, {"parameters": {"plate": 100}},
    {"features": [{"index": -1, "changes": {"at": [0, 0, 0]}}]}, {"parameters": {"plate": 4}},
])
def test_invalid_or_ineffective_repairs_are_rejected(patch):
    with pytest.raises(ValueError):
        apply_repair(ff.DesignSpec.model_validate(_example()), patch)


def test_saved_backpack_replies_count_only_effective_changes():
    saved = json.loads((Path(__file__).parent / "fixtures" / "backpack_repair.json").read_text())
    spec = ff.DesignSpec.model_validate(saved["initial_spec"])
    # This real patch listed 15 entries, but changed only four parameters and one feature.
    patched = apply_repair(spec, saved["patch"])
    recovered = apply_repair(spec, repair_from_spec(spec, saved["replacement_spec"]))
    assert patched == recovered
    assert patched.parameters["plate_thick"].value == 6
    assert patched.features[3].size == ("arm_length - plate_thick", "fillet_r", "fillet_r")
    assert patched.requirements == spec.requirements and patched.loads == spec.loads
    # Validating the repair format is NOT approval: this saved edit still crosses the wall.
    rib = Designer._feature_geometry(patched, ff.values_of(patched))[3]
    assert rib["occupies_mm"]["min"][0] == -2.5


def test_more_than_six_effective_changes_still_rejected():
    spec = ff.DesignSpec.model_validate(_example())
    patch = {"parameters": {n: p.value + 0.1 for n, p in spec.parameters.items() if p.role != "fixed"},
             "features": [{"index": 3, "changes": {"radius": 2.7}}, {"index": 0, "changes": {"at": [0, 0, 1]}}]}
    with pytest.raises(ValueError, match="at most six"):
        apply_repair(spec, patch)


@pytest.mark.parametrize("change", ["requirements", "force", "feature_count", "feature_identity",
                                    "operation", "shape", "parameter_range", "parameter_role",
                                    "parameter_names", "support_identity", "load_count", "world_size",
                                    "fixed_dimension"])
def test_full_spec_recovery_rejects_changes_outside_the_repair_contract(change):
    spec = ff.DesignSpec.model_validate(_example())
    raw = spec.model_dump(mode="json")
    if change == "requirements":
        raw["requirements"]["max_displacement_mm"] = 100
    elif change == "force":
        raw["environment"][1]["force_n"] = [0, 0, -1]
    elif change == "feature_count":
        raw["features"].pop()
    elif change == "feature_identity":
        raw["features"][3]["label"] = "A different function"
    elif change == "operation":
        raw["features"][3]["op"] = "add"
    elif change == "shape":
        raw["features"][3]["shape"] = "box"
    elif change == "parameter_range":
        raw["parameters"]["plate"]["max"] = 100
    elif change == "parameter_role":
        raw["parameters"]["plate"]["role"] = "size"
    elif change == "parameter_names":
        raw["parameters"].pop("plate")
    elif change == "support_identity":
        raw["environment"][0]["name"] = "different_attachment"
    elif change == "load_count":
        raw["environment"].append({**deepcopy(raw["environment"][1]), "name": "second_shelf"})
    elif change == "world_size":  # the wall or shelf is the world, not the design
        raw["environment"][1]["size"] = [150, 225, 30]
    elif change == "fixed_dimension":
        raw["parameters"]["shelf_height"].update(value=90, min=90, max=90)
    with pytest.raises(ValueError, match="Repair"):
        repair_from_spec(spec, raw)


def test_cosmetic_or_constant_expression_edits_do_not_count_as_repairs():
    spec = ff.DesignSpec.model_validate(_example())
    raw = spec.model_dump(mode="json")
    raw["name"], raw["summary"] = "Renamed part", "New description"
    raw["parameters"]["plate"]["note"] = "New note"
    raw["features"][0]["at"] = ["0", "0.0", "1 - 1"]
    with pytest.raises(ValueError, match="no change"):
        apply_repair(spec, repair_from_spec(spec, raw))
    with pytest.raises(ValueError, match="no change"):
        apply_repair(spec, {"features": [{"index": 0, "changes": {"at": ["0", 0, 0]}}]})


def test_contradictory_review_is_clarified_without_redrafting(designer):
    spec = ff.DesignSpec.model_validate(_example())
    r = result(spec, ff.values_of(spec))
    def reply(system, payload):
        if "clarify_review" in payload:
            assert payload["evidence"]["check:sealed_cavity"]["passed"]
            return approve(system, payload)
        return {"verdict": "revise", "issues": [{"severity": "must_fix", "problem": "Sealed pocket",
            "evidence_ids": ["check:sealed_cavity"], "evidence": "The cavity check failed", "fix": "Delete pocket"}]}
    fab, other = Agent(reply), Agent(approve)
    reviews = designer._review_draft(spec, r, {"mechanics": other, "fabrication": fab, "measurement": other},
                                    dict(input_tokens=0, output_tokens=0, total_tokens=0, reserved_tokens=0, calls=0))
    assert all(x["verdict"] == "approve" for x in reviews)
    assert len(fab.payloads) == 2 and len(other.payloads) == 2
    assert reviews[1]["previous_review"]["issues"][0]["verification"] == "contradicts_check"


def test_a_claim_code_contradicts_twice_is_overruled_instead_of_stopping_the_run(designer):
    # Live: fabrication twice called the cup's drain blind while its span check showed it through the floor.
    spec = ff.DesignSpec.model_validate(_example())
    r = result(spec, ff.values_of(spec))
    def reply(system, payload):
        return {"verdict": "revise", "issues": [
            {"severity": "must_fix", "problem": "Hole far too small", "evidence_ids": ["features:3"], "evidence": "radius under 0.1 mm",
             "claim": {"kind": "comparison", "left": "measure:features:3:radius", "op": "lt", "right": 0.1}},
            {"severity": "should_fix", "problem": "Add clearance", "evidence_ids": ["features:3"], "evidence": "M6"}]}
    fab, other = Agent(reply), Agent(approve)
    reviews = designer._review_draft(spec, r, {"mechanics": other, "fabrication": fab, "measurement": other},
                                    dict(input_tokens=0, output_tokens=0, total_tokens=0, reserved_tokens=0, calls=0))
    assert len(fab.payloads) == 2  # it had its clarification round first
    fabrication = reviews[1]
    assert fabrication["verdict"] == "approve" and "overruled 1 claim" in fabrication["note"]
    overruled = fabrication["issues"][0]
    assert overruled["severity"] == "note" and overruled["verification"] == "overruled_by_measurement" and "left" in overruled["overruled"]
    assert fabrication["issues"][1]["severity"] == "should_fix"  # the rest of the review stands


def test_a_review_cut_off_at_the_token_cap_is_asked_once_more_compactly(designer):
    from coform.providers import ProviderError
    spec = ff.DesignSpec.model_validate(_example())
    r = result(spec, ff.values_of(spec))
    def reply(system, payload):
        if "previous_reply_lost" not in payload:
            raise ProviderError("nvidia reply was cut off at the 1600-token output limit before the JSON ended", output_tokens=1600, truncated=True)
        assert "at most 4 issues" in system and "cut off" in payload["previous_reply_lost"]
        return approve(system, payload)
    mech, other = Agent(reply), Agent(approve)
    reviews = designer._review_draft(spec, r, {"mechanics": mech, "fabrication": other, "measurement": other},
                                    dict(input_tokens=0, output_tokens=0, total_tokens=0, reserved_tokens=0, calls=0))
    assert reviews[0]["verdict"] == "approve" and "cut off" in reviews[0]["retried_after"]
    assert len(mech.systems) == 2 and len(other.payloads) == 2


def test_missing_and_uncited_reviews_cannot_approve():
    spec = ff.DesignSpec.model_validate(_example())
    evidence = evidence_for(spec, result(spec, ff.values_of(spec)))
    assert assess_review("mechanics", {"changes": None}, None, evidence)["verdict"] == "no_reply"
    reply = {"verdict": "revise", "issues": [{"severity": "must_fix", "problem": "Bad shape"}]}
    assert assess_review("mechanics", reply, None, evidence)["verdict"] == "needs_evidence"


@pytest.mark.parametrize("prompt,intent", [
    ("A wall hook for a backpack", {"function": "Hold the strap", "interfaces": ["Back face at x=0"], "keep_clear": ["x<0 and screw access"]}),
    ("An electronics enclosure with a removable lid", {"function": "Protect the board", "interfaces": ["Lid seating rim"], "keep_clear": ["Board cavity and connector insertion path"]}),
    ("A clamp that grips a cable", {"function": "Retain cable", "interfaces": ["Cable contact bore"], "keep_clear": ["Cable insertion path"]}),
])
@pytest.mark.parametrize("full_spec_reply", [False, True])
def test_targeted_repair_carries_general_brief_and_failure_evidence(designer, monkeypatch, prompt, intent, full_spec_reply):
    p = project(designer, prompt)
    calls = []
    def analyze(spec, values, folder):
        calls.append(folder)
        r = result(spec, values, folder)
        if spec.features[3].radius == 2.5:
            r["checks"][1].update(passed=False, value=1, detail="Open the functional pocket through the outside face")
        return r
    monkeypatch.setattr(ff, "analyze", analyze)
    def reply(system, payload):
        if "request" not in payload:
            assert payload["part"]["design_intent"] == intent
            return approve(system, payload)
        assert "mating" in system and "moving-part travel" in system and "assembly" in system
        if "previous_spec" not in payload:
            assert 'Return {"spec": SPEC' in system and 'Return {"repair"' not in system
            return {"spec": _example(), "design_intent": intent}
        assert 'Return {"repair"' in system and 'Return {"spec": SPEC' not in system
        assert "SIX" in system
        assert payload["design_intent"] == intent
        assert "functional pocket" in payload["failure_history"][-1]["errors"][0]
        assert payload["previous_geometry"]["features"][3]["occupies_mm"]["min"][0] == -1
        assert payload["previous_geometry"]["code_checks"][1]["passed"] is False
        if full_spec_reply:
            raw = deepcopy(payload["previous_spec"])
            raw["features"][3]["radius"] = 2.7
            return {"spec": raw}
        return {"repair": {"features": [{"index": 3, "changes": {"radius": 2.7}}]}}
    agent = Agent(reply)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, agent))
    spec, r, *_ = designer._draft(p, True)
    assert spec.features[3].radius == 2.7 and r["design_intent"] == intent
    attempts = designer.state(p["id"])["attempts"]
    assert [a["status"] for a in attempts] == ["rejected", "accepted"]
    expected = {"features": [{"index": 3, "changes": {"radius": 2.7}}]}
    if full_spec_reply:
        expected["parameters"] = {}
        assert attempts[1]["repair_source"] == "full_spec"
    assert attempts[1]["applied_repair"] == expected
    assert all(r["verdict"] == "approve" for r in attempts[1]["reviews"])
    assert calls[0] != calls[1] and all((f / "part.step").exists() for f in calls)


def test_failed_job_and_attempts_survive_reopening_store(designer, monkeypatch):
    p = project(designer)
    agent = Agent(lambda s, p: {"spec": {}})
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, agent))
    with pytest.raises(RuntimeError, match="stalled after 3"):
        designer._draft(p, True)
    designer._log(p["id"], "No acceptable design", stage="error")
    PROGRESS[p["id"]]["running"] = False
    designer._save_progress(p["id"])
    PROGRESS.pop(p["id"])
    other_store = Store(designer.store.directory)
    try:
        other = Designer(SimpleNamespace(store=other_store))
        state = other.state(p["id"])
        assert len(state["attempts"]) == 3
        assert state["progress"]["stage"] == "error" and state["progress"]["log"][-1]["message"] == "No acceptable design"
        assert other.store.verify()
    finally:
        other_store.close()


def seed(designer, plate=8):
    p = project(designer)
    raw = _example()
    raw["parameters"]["plate"]["value"] = plate
    spec = ff.DesignSpec.model_validate(raw)
    sr = designer.store.put("design_spec", p["id"], {"id": "spec_test", "spec": raw})
    folder = designer._folder(p["id"], "draft")
    r = result(spec, ff.values_of(spec), folder)
    t = designer._record_test(p["id"], sr, 0, "draft", "fake", {}, "Initial", r, chosen=True, folder=folder.name)
    return p, t


def test_failed_proposals_feed_smaller_correction_and_reviews_see_results(designer, monkeypatch):
    p, initial = seed(designer)
    simulations = []
    def analyze(spec, values, folder, **kw):
        simulations.append(values["plate"])
        return result(spec, values, folder, **kw)
    monkeypatch.setattr(ff, "analyze", analyze)
    def reply(system, payload):
        if "proposals" in payload:
            assert all("test_results" in x for x in payload["proposals"])
            return {"reviews": [{"proposal_id": x["id"], "support": True, "reason": "Supported"} for x in payload["proposals"]]}
        if "measurement specialist" in system:
            return {"test_plan": "Measure the loaded tip"}
        history = payload["context"]["failure_history"]
        if not history:
            return {"changes": {"plate": 4}, "reason": "Too aggressive"}
        assert any(h.get("test_results", {}).get("passed") is False for h in history)
        if "fabrication specialist" in system:
            return {"changes": {"plate": 4}, "reason": "Repeated bad proposal"}
        return {"changes": {"plate": 6}, "reason": "Halfway between 8 and failed 4"}
    agent = Agent(reply)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, agent))
    designer._optimize(p["id"], True, 2)
    state = designer.state(p["id"])
    assert [r["status"] for r in state["rounds"]] == ["retrying", "improved"]
    assert simulations == [4, 6]  # duplicate within a round and repeat in a later round are not simulated
    assert state["best"]["values"]["plate"] == 6
    assert "Already attempted" in state["rounds"][1]["proposals"][1]["rejected"]
    assert all(not v["support"] and v.get("evidence_conflict") for v in state["rounds"][0]["reviews"])
    assert designer.artifact(p["id"], initial["id"], "step").read_text() == str(initial["values"])


def test_resume_uses_new_round_numbers_and_preserves_existing_files(designer, monkeypatch):
    p, initial = seed(designer)
    monkeypatch.setattr(ff, "analyze", result)
    monkeypatch.setattr(designer, "_providers", lambda live: None)
    designer._optimize(p["id"], False, 1)
    tests = designer.state(p["id"])["tests"]
    saved = {t["id"]: designer.artifact(p["id"], t["id"], "step").read_bytes() for t in tests}
    designer._optimize(p["id"], False, 1)
    assert [r["iteration"] for r in designer.state(p["id"])["rounds"]] == [1, 2]
    assert all(designer.artifact(p["id"], tid, "step").read_bytes() == raw for tid, raw in saved.items())


def test_resume_with_no_accepted_spec_restarts_drafting(designer, monkeypatch):
    p = project(designer)
    called = []
    monkeypatch.setattr(designer, "_start", lambda pid, job: job())
    monkeypatch.setattr(designer, "_draft_and_run", lambda pid, live, **kw: called.append((pid, live)))
    designer.run_more(p["id"], live=False)
    assert called == [(p["id"], False)]


def test_must_fix_does_not_become_approval_when_revision_limit_expires(designer, monkeypatch):
    p = project(designer)
    monkeypatch.setattr(ff, "analyze", result)
    def reply(system, payload):
        if "request" in payload:
            if "previous_spec" not in payload:
                return {"spec": _example()}
            radius = payload["previous_spec"]["features"][3]["radius"]
            return {"repair": {"features": [{"index": 3, "changes": {"radius": radius + 0.1}}]}}
        return {"verdict": "revise", "issues": [{"severity": "must_fix", "problem": "Assembly access remains blocked",
                "evidence_ids": ["features:3"], "evidence": "Hole radius remains below required 4 mm",
                "claim": {"kind": "comparison", "left": "measure:features:3:radius", "op": "lt", "right": 4}}]}
    agent = Agent(reply)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, agent))
    with pytest.raises(RuntimeError, match="Must-fix review issues remain"):
        designer._draft(p, True)
    reviews = designer.state(p["id"])["attempts"][-1]["reviews"]
    open_issues = [i for r in reviews for i in r["issues"] if i["severity"] == "must_fix"]
    assert open_issues and all(r["verdict"] == "revise" for r in reviews)
    state = designer.state(p["id"])
    assert [a["status"] for a in state["attempts"]] == ["review_rejected", "review_rejected", "review_blocked"]


def test_unverified_review_resume_rechecks_same_geometry_before_any_edits(designer, monkeypatch):
    p = project(designer)
    monkeypatch.setattr(ff, "analyze", result)
    def reply(system, payload):
        if "request" in payload:
            return {"spec": _example()}
        return {"verdict": "revise", "issues": [{"severity": "must_fix", "problem": "An unsupported claim"}]}
    agent = Agent(reply)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, agent))
    with pytest.raises(RuntimeError, match="unverified"):
        designer._draft(p, True)
    reviews = designer.state(p["id"])["attempts"][-1]["reviews"]
    assert any(r["verdict"] == "needs_evidence" for r in reviews)  # flagged, not approved
    assert designer.state(p["id"])["attempts"][-1]["status"] == "review_blocked"
    assert len([x for x in agent.payloads if "request" in x]) == 1
    reviewer = Agent(approve)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, reviewer))
    spec, *_ = designer._draft(p, True)
    assert spec == ff.DesignSpec.model_validate(_example())
    assert all("request" not in x for x in reviewer.payloads)
    assert designer.state(p["id"])["attempts"][-1]["status"] == "accepted"


def test_build_errors_are_saved_and_used_in_the_next_round(designer, monkeypatch):
    p, _ = seed(designer)
    def analyze(spec, values, folder, **kw):
        raise ff.BuildError("Load region misses the new surface")
    monkeypatch.setattr(ff, "analyze", analyze)
    def reply(system, payload):
        if "measurement specialist" in system:
            return {"test_plan": "Measure displacement"}
        if payload["context"]["failure_history"]:
            assert "misses" in payload["context"]["failure_history"][0]["rejected"]
        return {"changes": {"plate": 5}, "reason": "Try thinner plate"}
    agent = Agent(reply)
    monkeypatch.setattr(designer, "_providers", lambda live: dict.fromkeys(ROLES, agent))
    designer._optimize(p["id"], True, 2)
    state = designer.state(p["id"])
    assert len(state["tests"]) == 1
    assert state["rounds"][-1]["status"] == "stalled"
    assert "Already attempted" in state["rounds"][-1]["proposals"][0]["rejected"]
    assert not any("proposals" in x for x in agent.payloads)  # no review calls spent on unbuilt parts


def test_existing_designs_with_open_issues_are_repaired_before_printing(designer, monkeypatch):
    p, initial = seed(designer)
    saved = designer.store.get(initial["spec_id"], "design_spec")
    designer.store.put("design_spec", p["id"], {**saved, "id": "spec_legacy", "reviews": [{
        "agent": "fabrication", "verdict": "revise", "issues": [{"severity": "must_fix", "problem": "Hole needs clearance"}]}]})
    assert designer.state(p["id"])["best"] is None
    monkeypatch.setattr(ff, "analyze", result)
    monkeypatch.setattr(designer, "_start", lambda pid, job: job())
    monkeypatch.setattr(designer, "_optimize", lambda *a: None)
    def reply(system, payload):
        if "request" in payload:
            assert "previous_spec" in payload
            return {"repair": {"features": [{"index": 3, "changes": {"radius": 2.7}}]}}
        if 'fabrication specialist' in system and payload['part']['features'][3]['radius_mm'] == 2.5:
            assert payload['prior_design_review']['issues'][0]['problem'] == 'Hole needs clearance'
            return {'verdict': 'revise', 'issues': [{'severity': 'must_fix', 'problem': 'Hole needs clearance',
                'evidence_ids': ['features:3'], 'evidence': 'Radius 2.5 equals nominal fastener radius', 'fix': 'Increase to 2.7',
                'claim': {'kind': 'comparison', 'left': 'measure:features:3:radius', 'op': 'lt', 'right': 2.7}}]}
        return approve(system, payload)
    agent = Agent(reply)
    monkeypatch.setattr(designer, '_providers', lambda live: dict.fromkeys(ROLES, agent))
    state = designer.run_more(p['id'], live=True)
    assert state['specs'][-1]['spec']['features'][3]['radius'] == 2.7
    assert not designer.unresolved_review(p['id'])
    assert state['best']['spec_id'] == state['specs'][-1]['id']
    assert state['best']['id'] != initial['id']


def test_surface_export_keeps_a_closed_shell_even_with_legacy_triangle_limit():
    from collections import Counter
    nodes = {1: [0, 0, 0], 2: [1, 0, 0], 3: [0, 1, 0], 4: [0, 0, 1]}
    field = ff._surface_field(nodes, [(1, 2, 3, 4)], {}, {}, limit=1)
    edges = Counter(tuple(sorted((t[i], t[(i + 1) % 3]))) for t in field['triangles'] for i in range(3))
    assert len(field['triangles']) == 4 and all(n == 2 for n in edges.values())


def test_default_limits_allow_a_seventh_drafting_attempt(designer, monkeypatch):
    designer.limits = DesignerLimits()
    p = project(designer)
    def improving(spec, values, folder):
        r = result(spec, values, folder)
        remaining = max(0, 6 - round((spec.features[3].radius - 2.5) * 10))
        r['checks'][1].update(passed=not remaining, value=remaining, detail='Remaining geometry failures')
        return r
    monkeypatch.setattr(ff, 'analyze', improving)
    drafts = 0
    def reply(system, payload):
        nonlocal drafts
        if 'request' in payload:
            drafts += 1
            if drafts == 1:
                return {'spec': _example()}
            return {'repair': {'features': [{'index': 3, 'changes': {'radius': round(2.5 + (drafts - 1) * 0.1, 2)}}]}}
        return approve(system, payload)
    agent = Agent(reply)
    monkeypatch.setattr(designer, '_providers', lambda live: dict.fromkeys(ROLES, agent))
    designer._draft(p, True)
    attempts = designer.state(p['id'])['attempts']
    assert len(attempts) == 7 and attempts[-1]['status'] == 'accepted'


def test_default_optimization_survives_six_unproductive_rounds(designer, monkeypatch):
    designer.limits = DesignerLimits()
    p, _ = seed(designer)
    agent = Agent(lambda system, payload: {'changes': None, 'reason': 'No change', 'test_plan': 'Measure the tip'})
    monkeypatch.setattr(designer, '_providers', lambda live: dict.fromkeys(ROLES, agent))
    monkeypatch.setattr(designer, '_start', lambda pid, job: job())
    state = designer.run_more(p['id'], live=True)
    assert len(state['rounds']) == 8
    assert state['rounds'][6]['status'] == 'retrying'
    assert state['rounds'][7]['status'] == 'stalled'
    assert state['limits']['max_rounds'] == 12


def test_default_review_limit_allows_six_repairs_then_accepts_seventh_draft(designer, monkeypatch):
    designer.limits = DesignerLimits()
    p = project(designer)
    monkeypatch.setattr(ff, 'analyze', result)
    def reply(system, payload):
        if 'request' in payload:
            if 'previous_spec' not in payload:
                return {'spec': _example()}
            radius = round(payload['previous_spec']['features'][3]['radius'] + 0.1, 2)
            return {'repair': {'features': [{'index': 3, 'changes': {'radius': radius}}]}}
        if payload['part']['features'][3]['radius_mm'] < 3.1:
            return {'verdict': 'revise', 'issues': [{'severity': 'must_fix', 'problem': 'Insufficient hole clearance',
                'evidence_ids': ['features:3'], 'evidence': 'Required radius is 3.1 mm; the current radius is smaller',
                'claim': {'kind': 'comparison', 'left': 'measure:features:3:radius', 'op': 'lt', 'right': 3.1}}]}
        return approve(system, payload)
    agent = Agent(reply)
    monkeypatch.setattr(designer, '_providers', lambda live: dict.fromkeys(ROLES, agent))
    spec, *_ = designer._draft(p, True)
    assert spec.features[3].radius == 3.1
    assert len(designer.state(p['id'])['attempts']) == 7


def test_iteration_configuration_and_api_overrides(monkeypatch, designer):
    from dataclasses import fields
    from coform.app import DesignRunRequest
    for field in fields(DesignerLimits):
        # Both the current name and the SCIENCECLAW_ one it still falls back to.
        for prefix in ('COFORM_DESIGN_', 'SCIENCECLAW_DESIGN_'):
            monkeypatch.delenv(prefix + field.name.upper(), raising=False)
    assert DesignerLimits.from_environment() == DesignerLimits(12, 12, 8, 8)
    monkeypatch.setenv('COFORM_DESIGN_MAX_ROUNDS', '20')
    monkeypatch.setenv('COFORM_DESIGN_MAX_DRAFT_ATTEMPTS', '18')
    designer.limits = DesignerLimits.from_environment()
    assert designer.limits.max_draft_attempts == 18
    assert DesignRunRequest().rounds is None
    assert DesignRunRequest(rounds=25).rounds == 25
    p, _ = seed(designer)
    called = []
    monkeypatch.setattr(designer, '_start', lambda pid, job: job())
    monkeypatch.setattr(designer, '_optimize', lambda pid, live, rounds: called.append(rounds))
    designer.run_more(p['id'], live=False)
    designer.run_more(p['id'], live=False, rounds=25)
    assert called == [20, 25]
    for bad in ('0', '51', 'not a number'):
        monkeypatch.setenv('COFORM_DESIGN_MAX_ROUNDS', bad)
        with pytest.raises(ValueError, match='integer from 1 to 50'):
            DesignerLimits.from_environment()
