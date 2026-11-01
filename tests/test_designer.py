import json
import time

import pytest

from coform import freeform as ff
from coform.designer import PROGRESS, Designer, _example, template_spec
from coform.fea import find_tools
from coform.providers import ProviderError, ProviderResponse

needs_solver = pytest.mark.skipif(find_tools() is None, reason="Gmsh and CalculiX are not installed (bundled with FreeCAD 1.x)")


@pytest.fixture(autouse=True)
def bounded_test_iterations(monkeypatch):
    # Exercise the same termination behavior with short, deterministic test runs.
    for name, value in {"MAX_DRAFT_ATTEMPTS": 6, "MAX_ROUNDS": 6, "MAX_REVIEW_CYCLES": 2, "MAX_STALLED_ROUNDS": 2}.items():
        monkeypatch.setenv("COFORM_DESIGN_" + name, str(value))


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
    from coform.service import CoForm
    service = CoForm(tmp_path)
    try:
        designer = Designer(service)
        state = designer.create("A wall bracket for a small shelf of books.", {"load_n": 150, "max_displacement_mm": 1.0}, live=False)
        pid = state["project"]["id"]
        _wait(pid)
        state = designer.state(pid)
        first, best = state["tests"][0], state["best"]
        assert not first["results"]["passed"] and best["results"]["passed"]
        assert state["rounds"][-1]["status"] == "stalled" and state["progress"]["stage"] == "ready"
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
            if "previous_spec" in payload:
                if any(a.get("review_issues") for a in payload["failure_history"]):
                    repair = {"features": [{"index": 3, "changes": {"radius": 2.7}}]}
                else:
                    repair = {"features": [{"index": 4, "changes": {"at": [0, 0, 0]}}]}
                return ProviderResponse({"repair": repair, "assumptions": ["Wall is rigid"]}, 500, 300)
            spec = _example()
            if len(self.payloads) == 1:
                spec["features"].append({"label": "floating", "shape": "box", "at": [300, 0, 0], "size": [5, 5, 5]})
            return ProviderResponse({"spec": spec, "assumptions": ["Wall is rigid"]}, 500, 900)
        if "part" in payload:
            return ProviderResponse({"verdict": "approve", "issues": []}, 300, 40)
        if "proposals" in payload:
            return ProviderResponse({"reviews": [{"proposal_id": p["id"], "support": True, "reason": "ok"} for p in payload["proposals"]]}, 300, 80)
        if "measurement specialist" in system:
            return ProviderResponse({"test_plan": "Clamp the wall plate, hang the load, read a dial gauge at the tip.", "concerns": []}, 300, 60)
        return ProviderResponse({"changes": None, "reason": "Already good enough"}, 300, 40)


class FlakyDrafter(Drafter):
    """The first draft replies fail the way live Nemotron drafts did: cut off at the token cap, or timed out."""

    def __init__(self, failures, truncated=True):
        super().__init__()
        self.failures, self.truncated, self.drafts = failures, truncated, []

    def complete_json(self, system, payload, max_output_tokens):
        if "request" in payload:
            self.drafts.append(payload)
            if len(self.drafts) <= self.failures:
                if self.truncated:
                    raise ProviderError(f"nvidia reply was cut off at the {max_output_tokens}-token output limit before the JSON ended",
                                        input_tokens=1945, output_tokens=max_output_tokens, truncated=True)
                raise ProviderError("nvidia request failed (ReadTimeout); no retry was made")
        return super().complete_json(system, payload, max_output_tokens)


@needs_solver
def test_a_cut_off_draft_reply_is_retried_with_the_reason_instead_of_stopping_the_run(tmp_path, monkeypatch):
    from coform.designer import DRAFT_CUT_OFF
    from coform.service import CoForm
    service = CoForm(tmp_path)
    drafter = FlakyDrafter(failures=1)
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: drafter for r in ("mechanics", "fabrication", "measurement")} if live else None)
    try:
        designer = Designer(service)
        pid = designer.create("A hook that hangs a backpack from a desk edge.", {}, live=True)["project"]["id"]
        _wait(pid)
        state = designer.state(pid)
        assert state["specs"] and state["specs"][0]["source"] == "llm"  # the run went on to a real draft
        assert DRAFT_CUT_OFF in json.dumps(drafter.drafts[1]["previous_attempt_errors"])  # and the drafter was told why
        assert state["attempts"][0]["status"] == "provider_error"
        assert any("Asking again" in e["message"] for e in state["progress"]["log"])
        assert not any(e["message"].startswith("Stopped: ") for e in state["progress"]["log"])  # no crashed run
    finally:
        service.store.close()


def test_repeated_draft_call_failures_stop_after_three_instead_of_every_attempt(tmp_path, monkeypatch):
    from coform.designer import MAX_DRAFT_PROVIDER_FAILURES
    from coform.service import CoForm
    service = CoForm(tmp_path)
    drafter = FlakyDrafter(failures=99, truncated=False)
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: drafter for r in ("mechanics", "fabrication", "measurement")} if live else None)
    try:
        designer = Designer(service)
        pid = designer.create("A hook that hangs a backpack from a desk edge.", {}, live=True)["project"]["id"]
        _wait(pid)
        log = [e["message"] for e in designer.state(pid)["progress"]["log"]]
        assert len(drafter.drafts) == MAX_DRAFT_PROVIDER_FAILURES < 6  # 6 = this file's MAX_DRAFT_ATTEMPTS
        assert "ReadTimeout" in log[-1] and f"{MAX_DRAFT_PROVIDER_FAILURES} times in a row" in log[-1]
        # The part's lock is released, so Resume works without restarting the server.
        drafter.failures = 0
        designer.run_more(pid, live=True, rounds=1)
        _wait(pid)
        assert len(drafter.drafts) > MAX_DRAFT_PROVIDER_FAILURES
    finally:
        service.store.close()


@needs_solver
def test_live_drafting_retries_with_the_kernel_reason(tmp_path, monkeypatch):
    from coform.service import CoForm
    service = CoForm(tmp_path)
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
        assert state["rounds"][-1]["status"] == "stalled" and "dial gauge" in state["rounds"][0]["notes"]["measurement"]
    finally:
        service.store.close()


def test_fusion_view_job_opens_a_step_without_creating_cad_revisions(tmp_path):
    from coform.formlabs import Manufacturing
    from coform.fusion import FusionLink
    from coform.service import CoForm
    service = CoForm(tmp_path)
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


def _winding_problems(path):
    import collections
    import struct
    raw = path.read_bytes()
    n = struct.unpack("<I", raw[80:84])[0]
    directed = collections.Counter()
    for i in range(n):
        f = struct.unpack("<12f", raw[84 + 50 * i:84 + 50 * i + 48])
        a, b, c = (tuple(round(x, 4) for x in f[k:k + 3]) for k in (3, 6, 9))
        for e in ((a, b), (b, c), (c, a)):
            directed[e] += 1
    return sum(1 for count in directed.values() if count > 1)


@needs_solver
def test_sealed_pocket_fails_printability_and_the_stl_is_consistently_wound(tmp_path):
    # A real live draft hid a nut trap inside a jaw: a sealed cavity that traps resin, and an STL
    # with inside-out patches that slicers showed as broken.
    from coform.designer import _check_spec
    raw = _example()
    raw["features"].append({"label": "sealed pocket", "op": "subtract", "shape": "box", "at": [1, 8, 50], "size": [2, 6, 6]})
    spec = ff.DesignSpec.model_validate(raw)
    result = ff.analyze(spec, ff.values_of(spec), tmp_path / "part")
    sealed = next(c for c in result["checks"] if c["name"] == "sealed_cavity")
    assert not sealed["passed"] and "x 1.0-3.0" in sealed["detail"] and not result["passed"]
    assert _winding_problems(tmp_path / "part" / "part.stl") == 0
    report = json.loads((tmp_path / "part" / "stl_report.json").read_text())
    assert report["open_or_nonmanifold_edges"] == 0 and report["cavity_volumes_mm3"][0] == pytest.approx(-72, rel=0.05)
    # A draft with a resin trap goes back to the drafting agent instead of into the rounds.
    _, _, errors = _check_spec(raw, tmp_path / "draft")
    assert errors and "sealed internal cavity" in errors[0]


@needs_solver
def test_clean_part_has_no_cavity_and_correct_winding(tmp_path):
    spec = ff.DesignSpec.model_validate(_example())
    result = ff.analyze(spec, ff.values_of(spec), tmp_path)
    assert all(c["passed"] for c in result["checks"] if c["name"] in ("sealed_cavity", "watertight"))
    assert _winding_problems(tmp_path / "part.stl") == 0


class Reviewer(Drafter):
    """Fabrication demands one change on the first reviewed draft, then approves."""

    def __init__(self):
        super().__init__()
        self.reviews = 0

    def complete_json(self, system, payload, max_output_tokens):
        if "part" in payload and "reviewing a newly drafted" in system:
            self.payloads.append(payload)
            if "fabrication specialist" in system:
                self.reviews += 1
                if self.reviews == 1:
                    return ProviderResponse({"verdict": "revise", "issues": [{"severity": "must_fix", "feature": "screw hole",
                                             "problem": "M5 hole has no print clearance", "fix": "radius 2.7 mm",
                                             "evidence_ids": ["features:3"], "evidence": "Radius 2.5 mm equals the nominal M5 radius",
                                             "claim": {"kind": "comparison", "left": "measure:features:3:radius", "op": "lt", "right": 2.7}}]}, 300, 60)
            return ProviderResponse({"verdict": "approve", "issues": [{"severity": "note", "feature": "", "problem": "Looks fine"}]}, 300, 40)
        return super().complete_json(system, payload, max_output_tokens)


@needs_solver
def test_specialists_review_the_draft_and_must_fix_issues_go_back_to_the_drafter(tmp_path, monkeypatch):
    from coform.service import CoForm
    service = CoForm(tmp_path)
    agent = Reviewer()
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: agent for r in ("mechanics", "fabrication", "measurement")} if live else None)
    try:
        designer = Designer(service)
        pid = designer.create("A wall bracket that holds a plant pot.", {}, live=True)["project"]["id"]
        _wait(pid)
        state = designer.state(pid)
        drafts = [p for p in agent.payloads if "request" in p]
        review_payloads = [p for p in agent.payloads if "part" in p]
        # Draft 1 is rejected by the code (two solids); draft 2 by fabrication's review; draft 3 is approved.
        assert len(drafts) == 3 and "M5 hole" in json.dumps(drafts[2]["previous_attempt_errors"])
        assert review_payloads[0]["part"]["features"][0]["size_mm"] == [4.0, 25.0, 80.0]  # evaluated, not expressions
        reviews = state["specs"][0]["reviews"]
        assert {r["agent"] for r in reviews} == {"mechanics", "fabrication", "measurement"}
        assert all(r["verdict"] == "approve" for r in reviews)
    finally:
        service.store.close()


class FakePreForm:
    def add_asset(self, filename, raw, **kw):
        assert raw[:80].startswith(b"CoForm repaired STL")
        return {"id": "asset_1"}

    def prepare(self, asset_id, profile):
        return {"id": "build_1", "validation": {"grade": "PASS", "warnings": []}, "estimate": {"total_print_time_s": 3600}}


@needs_solver
def test_unresolved_must_fix_review_issues_require_repair_before_printing(tmp_path):
    from coform.formlabs import PrintProfile
    from coform.service import CoForm
    service = CoForm(tmp_path)
    try:
        designer = Designer(service, FakePreForm())
        pid = designer.create("A wall bracket for a small shelf of books.", {"load_n": 30, "max_displacement_mm": 1.0}, live=False)["project"]["id"]
        _wait(pid)
        best = designer.state(pid)["best"]
        profile = PrintProfile(machine_type="FRML-4-0", material_code="FLAPFA01", layer_thickness_mm=0.1, minimum_wall_mm=1.0)
        review = designer.prepare_print(pid, best["id"], profile, live=False)
        assert review["status"] == "approved" and review["blocked_by"] == [] and review["votes_needed"] == 2
        votes = {v["agent"]: v for v in review["votes"]}
        assert all(v["status"] == "approves" for v in votes.values())
        assert "dial indicator" in votes["measurement"]["suggestions"][0]  # how to test the printed part
        spec = service.store.list(pid, "design_spec")[-1]
        service.store.put("design_spec", pid, {**spec, "id": "spec_reviewed", "reviews": [
            {"agent": "fabrication", "verdict": "revise", "issues": [{"severity": "must_fix", "feature": "hole", "problem": "Blind hole traps resin", "fix": "Open it"}]}]})
        assert designer.state(pid)["best"] is None
        for accepted in (False, True):
            with pytest.raises(ValueError, match="Repair design review issues"):
                designer.prepare_print(pid, best["id"], profile, live=False, accept_review_issues=accepted)
    finally:
        service.store.close()


class PrintVoter:
    """Fake live specialists at the print vote: mechanics is cut off once, fabrication objects, measurement approves."""
    name, model = "fake", "voter"

    def __init__(self):
        self.calls = []

    def complete_json(self, system, payload, max_output_tokens):
        role = next(r for r in ("mechanics", "fabrication", "measurement") if f"the {r} specialist" in system)
        self.calls.append((role, payload))
        if role == "mechanics" and "previous_reply_lost" not in payload:
            raise ProviderError("nvidia reply was cut off at the 800-token output limit before the JSON ended", output_tokens=800, truncated=True)
        if role == "fabrication":
            return ProviderResponse({"support": False, "reason": "Large flat underside will peel.", "suggestions": ["Tilt it 20 degrees", "", 7]}, 300, 60)
        return ProviderResponse({"support": True, "reason": "Passes with margin.", "suggestions": ["Load it at the cup centre"]}, 300, 60)


def test_print_votes_keep_suggestions_and_ask_again_when_a_reply_is_cut_off(tmp_path, monkeypatch):
    from coform.service import CoForm
    service = CoForm(tmp_path)
    voter = PrintVoter()
    monkeypatch.setattr(Designer, "_providers", lambda self, live: dict.fromkeys(("mechanics", "fabrication", "measurement"), voter))
    try:
        test = {"results": {"passed": True, "mass_g": 125, "loads": [{"deflection_mm": 0.08, "force_n": [0, 0, -14.7]}],
                            "peak_stress_mpa": 0.76, "allowable_stress_mpa": 23, "checks": []}}
        votes = {v["agent"]: v for v in Designer(service)._print_votes(test, {"validation": {"grade": "PASS", "warnings": []}}, [], True)}
        assert [r for r, _ in voter.calls] == ["mechanics", "mechanics", "fabrication", "measurement"]
        assert "cut off" in voter.calls[1][1]["previous_reply_lost"]
        assert votes["mechanics"]["status"] == "approves" and votes["mechanics"]["support"] is True
        assert votes["fabrication"]["status"] == "objects" and votes["fabrication"]["suggestions"] == ["Tilt it 20 degrees"]
        assert votes["measurement"]["suggestions"] == ["Load it at the cup centre"]
    finally:
        service.store.close()


def test_a_lost_print_vote_says_so_instead_of_reading_as_an_objection():
    from coform.designer import _print_vote
    assert _print_vote("measurement", None, "provider_error")["status"] == "no_reply"
    assert _print_vote("measurement", {"support": "yes"}, None)["support"] is False  # only an explicit true approves


@needs_solver
def test_cavity_message_names_the_culprit_and_the_way_out(tmp_path):
    raw = _example()
    raw["features"].append({"label": "nut trap", "op": "subtract", "shape": "box", "at": [1, 8, 50], "size": [2, 6, 6]})
    spec = ff.DesignSpec.model_validate(raw)
    detail = next(c for c in ff.analyze(spec, ff.values_of(spec), tmp_path)["checks"] if c["name"] == "sealed_cavity")["detail"]
    # The pocket sits 1 mm inside the 4 mm wall plate: the nearest way out is the back face at x = 0.
    assert "'nut trap'" in detail and "extend it 1.0 mm" in detail and "x <= -1.0" in detail


class StubbornDrafter(Drafter):
    """Always returns the same sealed nut trap, like the live run that failed six times."""

    def complete_json(self, system, payload, max_output_tokens):
        if "request" in payload:
            self.payloads.append(payload)
            spec = _example()
            spec["features"].append({"label": "nut trap", "op": "subtract", "shape": "box", "at": [1, 8, 50], "size": [2, 6, 6]})
            return ProviderResponse({"spec": spec, "assumptions": []}, 500, 900)
        return super().complete_json(system, payload, max_output_tokens)


@needs_solver
def test_a_repeated_sealed_pocket_is_saved_without_deleting_its_function(tmp_path, monkeypatch):
    from coform.service import CoForm
    service = CoForm(tmp_path)
    agent = StubbornDrafter()
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: agent for r in ("mechanics", "fabrication", "measurement")} if live else None)
    try:
        designer = Designer(service)
        pid = designer.create("A wall bracket with a nut trap.", {}, live=True)["project"]["id"]
        _wait(pid)
        state = designer.state(pid)
        assert not state["specs"] and not state["tests"]
        assert len(state["attempts"]) == 4 and "Return a redesign" in state["attempts"][-1]["strategy"]
        assert "stalled after 4" in state["progress"]["log"][-1]["message"]
        assert "nut trap" in [f["label"] for f in state["attempts"][0]["spec"]["features"]]
        assert "sealed internal cavity" in state["attempts"][0]["errors"][0]
        assert "no change" in state["attempts"][-1]["errors"][0]
        assert state["progress"]["stage"] == "error"
    finally:
        service.store.close()


@needs_solver
def test_a_rib_rotated_through_the_wall_fails_the_mount_check_and_names_it(tmp_path):
    # A live draft rotated a gusset about its wall-side corner, swinging it 28 mm behind the wall.
    raw = _example()
    raw["features"].append({"label": "rib through the wall", "shape": "box", "at": [0, 10, 20], "size": [40, 4, 20],
                            "rotate": {"axis": "y", "degrees": -45}})
    spec = ff.DesignSpec.model_validate(raw)
    lo, hi = ff.feature_bounds(spec.features[-1], ff.values_of(spec))
    assert lo[0] == pytest.approx(-20 / 2 ** 0.5, abs=0.01)  # a 20 mm tall rib turned 45° swings 14.1 mm back
    # The wall is modelled, so the rib is found inside it before any strength result is computed.
    with pytest.raises(ff.BuildError, match=r"\[fit\] 'rib through the wall' reach up to 14\.\d mm into 'wall'"):
        ff.analyze(spec, ff.values_of(spec), tmp_path)
    ok = ff.DesignSpec.model_validate(_example())
    assert next(c for c in ff.analyze(ok, ff.values_of(ok), tmp_path / "ok")["checks"] if c["name"] == "fit")["passed"]


@needs_solver
def test_a_piece_shifted_off_to_one_side_fails_the_alignment_check(tmp_path):
    # A live draft put a 25 mm-wide hook curl 12.5 mm off the 25 mm-wide arm, so half of it stuck out sideways.
    raw = _example()
    under = "shelf_height - plate - 6"  # hangs under the arm's end, clear of the shelf board resting on top
    raw["features"].append({"label": "tip lip", "shape": "box", "at": ["arm_length - 5", "width/2", under], "size": [5, "width", 6]})
    spec = ff.DesignSpec.model_validate(raw)
    problems = ff.alignment_problems(spec, ff.values_of(spec))
    assert len(problems) == 1 and "'tip lip'" in problems[0] and "shifted 12.5 mm" in problems[0]
    # Lined up, and pieces that join end to end, pass.
    raw["features"][-1]["at"] = ["arm_length - 5", 0, under]
    spec = ff.DesignSpec.model_validate(raw)
    assert ff.alignment_problems(spec, ff.values_of(spec)) == []
    result = ff.analyze(spec, ff.values_of(spec), tmp_path)
    assert next(c for c in result["checks"] if c["name"] == "aligned")["passed"]


def _fixture(name):
    from pathlib import Path
    data = json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))
    return ff.DesignSpec.model_validate(data["spec"]), data["values"]


def test_real_backpack_hook_draft_is_caught_by_the_mount_and_alignment_checks():
    # The design the user found unmountable: gusset rotated 28 mm through the wall, curl shifted sideways.
    spec, values = _fixture("live_backpack_hook")
    gusset = next(f for f in spec.features if f.label == "gusset")
    assert ff.feature_bounds(gusset, values)[0][0] == pytest.approx(-28.3, abs=0.1)
    problems = ff.alignment_problems(spec, values)
    assert any("'tip_curl_quarter'" in p and "shifted 12.5 mm" in p for p in problems)


@needs_solver
def test_real_backpack_hook_fails_and_its_draft_would_go_back_to_the_drafter(tmp_path):
    from coform.designer import _check_spec
    spec, values = _fixture("live_backpack_hook")
    result = ff.analyze(spec, values, tmp_path / "a")
    failed = {c["name"]: c.get("detail", "") for c in result["checks"] if not c["passed"]}
    assert "'gusset' (reaches x = -28.3 mm)" in failed["mount_clear"] and "'tip_curl_quarter'" in failed["aligned"]
    assert not result["passed"]
    _, _, errors = _check_spec(spec.model_dump(mode="json"), tmp_path / "b")
    assert any("held face 'wall_mount'" in e for e in errors) and any("sticks out to one side" in e for e in errors)


@needs_solver
def test_real_desk_hook_with_its_sealed_nut_trap_is_caught(tmp_path):
    spec, values = _fixture("live_desk_hook")
    result = ff.analyze(spec, values, tmp_path)
    failed = {c["name"]: c.get("detail", "") for c in result["checks"] if not c["passed"]}
    assert "sealed internal cavity" in failed["sealed_cavity"] and "x 35.0-42.0" in failed["sealed_cavity"]


class OutageTeam(Drafter):
    """Drafts and reviews the part, then loses the connection for every optimisation call."""

    def __init__(self):
        super().__init__()
        self.down, self.lost = True, 0

    def complete_json(self, system, payload, max_output_tokens):
        if self.down and "context" in payload and "proposals" not in payload:
            self.lost += 1
            raise ProviderError("could not connect to nvidia (ConnectTimeout): the network or the provider's server is unreachable")
        return super().complete_json(system, payload, max_output_tokens)


@needs_solver
def test_an_outage_stops_the_rounds_and_says_so_instead_of_spending_every_round(tmp_path, monkeypatch):
    from coform.designer import MAX_UNANSWERED_ROUNDS
    from coform.service import CoForm
    service = CoForm(tmp_path)
    team = OutageTeam()
    monkeypatch.setattr(Designer, "_providers", lambda self, live: {r: team for r in ("mechanics", "fabrication", "measurement")} if live else None)
    try:
        designer = Designer(service)
        pid = designer.create("A water bottle holder that clamps to a desk.", {}, live=True)["project"]["id"]
        _wait(pid)
        state = designer.state(pid)
        assert [r["status"] for r in state["rounds"]] == ["no_reply"] * MAX_UNANSWERED_ROUNDS  # not all 6 allowed rounds
        assert team.lost == 3 * MAX_UNANSWERED_ROUNDS
        last = state["progress"]["log"][-1]["message"]
        assert last.startswith("Stopped: no agent answered") and "ConnectTimeout" in last and "not the design" in last
        team.down = False  # connection back: the same button carries on
        designer.run_more(pid, live=True, rounds=1)
        _wait(pid)
        assert designer.state(pid)["rounds"][-1]["status"] != "no_reply"
    finally:
        service.store.close()
