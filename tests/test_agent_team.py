import threading
import time

from scienceclaw.agents import decide, decide_independent
from scienceclaw.domain import Geometry, ModelParameters
from scienceclaw.providers import ProviderResponse
from tests.test_agents import context, proposal


class Slow:
    """Answers after a delay and records the overlap between concurrent calls."""
    name, model = "fake", "slow"

    def __init__(self, replies, delay=0.3):
        self.replies, self.delay, self.active, self.peak, self.calls = replies, delay, 0, 0, []
        self.lock = threading.Lock()

    def complete_json(self, system, payload, max_output_tokens):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append((system, payload))
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        stage = "review" if "proposals" in payload else "propose"
        return ProviderResponse(self.replies(stage, system, payload), 200, 40)


def agree(stage, system, payload):
    if stage == "propose":
        role = system.split("You are the ")[1].split(" specialist")[0]
        return {"proposal": proposal(), "notebook": f"{role} private plan"}
    return {"reviews": [{"proposal_id": p["id"], "support": True, "reason": "ok", "evidence_ids": ["obs_initial"]}
                        for p in payload["proposals"]], "ranking": [p["id"] for p in payload["proposals"]]}


def test_specialists_answer_concurrently_within_each_stage():
    provider = Slow(agree)
    ctx = context()
    ctx["protocol"]["token_budget"] = ctx["budget"]["tokens_remaining"] = 200000
    started = time.monotonic()
    result = decide(ctx, provider=provider)
    elapsed = time.monotonic() - started
    assert result["status"] == "agreed" and provider.peak == 3
    assert elapsed < 6 * 0.3 * 0.8  # two concurrent stages, not six sequential calls


def test_private_notebooks_reach_only_their_author():
    first = decide(context(), provider=Slow(agree, delay=0))
    assert first["agent_states"]["mechanics"]["notebook"] == "mechanics private plan"
    later = context()
    later["agent_states"] = first["agent_states"]
    provider = Slow(agree, delay=0)
    decide(later, provider=provider)
    for system, payload in provider.calls:
        role = system.split("You are the ")[1].split(" specialist")[0]
        seen = payload["context"]["local_state"].get("notebook")
        assert seen == f"{role} private plan"  # never a peer's notebook
        assert all(f"{other} private plan" not in str(payload) for other in ("mechanics", "fabrication", "measurement") if other != role)


def test_design_with_wrong_station_and_cost_is_normalized_not_rejected():
    ctx = context()
    ctx["model"]["fit"] = {"status": "identifiable"}
    ctx["model"]["parameters"] = ModelParameters().model_dump()
    design = {**proposal(), "action": "design", "load_position_m": .06,  # agents' common slip in live runs
              "geometry": Geometry(base_width_m=.020, wall_height_m=.012).model_dump(), "cost": {"cycles": 1, "designs": 0}}
    agents = {r: Slow(lambda stage, s, p: {"proposal": design} if stage == "propose" else agree(stage, s, p), delay=0)
              for r in ("mechanics", "fabrication", "measurement")}
    result = decide_independent(ctx, provider=agents)
    assert result["status"] == "agreed" and result["selected"]["action"] == "design"
    assert result["selected"]["load_position_m"] == .12 and result["selected"]["cost"] == {"cycles": 1, "designs": 1}
    assert any(t["type"] == "normalized" and set(t["fields"]) == {"load_position_m", "cost"} for t in result["transcript"])
    progress = agents["mechanics"].calls[0][1]["context"]["progress"]
    assert progress["fit_status"] == "identifiable" and "objective" in agents["mechanics"].calls[0][1]["context"]


def test_study_team_chooses_provider_and_model_per_role(tmp_path, monkeypatch):
    import pytest
    from scienceclaw import providers
    from scienceclaw.domain import Protocol
    from scienceclaw.service import ScienceClaw
    built = []

    def fake_from_environment(name, model=None):
        built.append((name, model))
        return Slow(agree, delay=0)
    monkeypatch.setattr(providers, "from_environment", fake_from_environment)
    service = ScienceClaw(tmp_path)
    try:
        nemotron = {"provider": "nvidia", "model": "nvidia/nemotron-3.5-lightning-30b-a3b"}
        run = service.create_run(team={r: nemotron for r in ("mechanics", "fabrication", "measurement")}, protocol=Protocol())
        assert run["run"]["team"]["fabrication"]["model"] == nemotron["model"]
        service.step(run["run"]["id"], live=True)
        assert built == [("nvidia", nemotron["model"])] * 3
        with pytest.raises(ValueError):
            service.create_run(team={"chef": nemotron})
    finally:
        service.store.close()


def identifiable_context():
    ctx = context()
    ctx["model"]["fit"] = {"status": "identifiable", "condition_number": 3.0}
    ctx["model"]["parameters"] = ModelParameters().model_dump()
    return ctx


def test_each_specialist_sees_only_its_own_evidence_and_tools():
    from scienceclaw.agents import _context_view
    ctx = identifiable_context()
    mech, fab, meas = (_context_view(ctx, r) for r in ("mechanics", "fabrication", "measurement"))
    assert set(mech["tools"]) == {"mechanics"} and set(fab["tools"]) == {"fabrication"} and set(meas["tools"]) == {"measurement"}
    assert "parameters" in mech["model"] and "parameters" not in meas["model"] and "parameters" not in fab["model"]
    assert "observation_rows" not in fab  # fabrication judges designs, not load data
    assert "uncertainty_m" in meas["observation_columns"] and "uncertainty_m" not in mech["observation_columns"]
    assert fab["tools"]["fabrication"]["design_options"][0]["geometry"]  # screened lighter templates
    assert meas["tools"]["measurement"]["starting_design_stations"][0]["signal_to_noise"] > 0
    assert "recommended_design" not in fab["progress"] and fab["charter"]["may_propose"] == ("design",)
    single = _context_view(ctx, "single")
    assert set(single["tools"]) == {"mechanics", "fabrication", "measurement"}


def test_reviewers_stress_test_from_different_angles():
    from scienceclaw.agents import _stress_tests
    ctx = identifiable_context()
    design = {**proposal(), "id": "p1", "action": "design", "load_position_m": .12,
              "geometry": Geometry(base_width_m=.020, wall_height_m=.012).model_dump()}
    found = {r: " ".join(_stress_tests(r, [design], ctx)["p1"]) for r in ("mechanics", "fabrication", "measurement")}
    assert "Root stress" in found["mechanics"] and "Material" in found["fabrication"] and "σ of measurement" in found["measurement"]
    assert len(set(found.values())) == 3


def test_owner_objection_blocks_a_design_even_with_two_supporters():
    ctx = identifiable_context()
    design = {**proposal(), "action": "design", "load_position_m": .12, "cost": {"cycles": 1, "designs": 1},
              "geometry": Geometry(base_width_m=.020, wall_height_m=.012).model_dump()}

    def replies(stage, system, payload):
        role = system.split("You are the ")[1].split(" specialist")[0]
        if stage == "propose":
            return {"proposal": design if role != "measurement" else None, "reason": "no measurement gap"}
        assert "your_stress_tests" in payload and set(payload["your_stress_tests"]) == {p["id"] for p in payload["proposals"]}
        return {"reviews": [{"proposal_id": p["id"], "support": role != "fabrication", "reason": "r", "findings": ["f"],
                             "evidence_ids": ["obs_initial"]} for p in payload["proposals"]], "ranking": [p["id"] for p in payload["proposals"]]}
    ctx["protocol"]["max_rounds"] = 1
    result = decide(ctx, provider=Slow(replies, delay=0))
    assert result["status"] == "no_consensus"
    assert any(t["type"] == "owner_objection" and t["owners"] == ["fabrication"] for t in result["transcript"])


def test_offline_design_stage_is_owned_by_mechanics_and_fabrication():
    ctx = identifiable_context()
    ctx["tools"] = {"recommended_geometry": Geometry(base_width_m=.020, wall_height_m=.012).model_dump(), "diagnostic_position_m": .06}
    result = decide(ctx)
    assert result["status"] == "agreed" and result["selected"]["action"] == "design"
    assert {p["agent"] for p in result["proposals"]} == {"mechanics", "fabrication"}
    assert all(v["findings"] for v in result["reviews"])


def test_one_malformed_reply_is_an_abstention_not_the_end_of_the_decision():
    from scienceclaw.providers import ProviderError

    class Flaky(Slow):
        def complete_json(self, system, payload, max_output_tokens):
            if "You are the fabrication" in system and "proposals" not in payload:
                raise ProviderError("invalid JSON", input_tokens=100, output_tokens=20)
            return super().complete_json(system, payload, max_output_tokens)
    ctx = context()
    ctx["protocol"]["token_budget"] = ctx["budget"]["tokens_remaining"] = 200000
    result = decide(ctx, provider=Flaky(agree, delay=0))
    assert result["status"] == "agreed" and result["selected"]["action"] == "experiment"
    assert any(t["type"] == "provider_error" and t["agent"] == "fabrication" for t in result["transcript"])


def test_fabrication_can_pick_a_screened_design_by_number():
    # Live runs: fabrication copied the starting design's geometry, so every design it proposed was a duplicate.
    ctx = identifiable_context()
    from scienceclaw.agents import _design_options
    lightest = _design_options(ctx)[0]
    copied = {**proposal(), "action": "design", "geometry": ctx["candidates"][0]["geometry"], "candidate_id": "made_up", "design_option": 0}

    def replies(stage, system, payload):
        role = system.split("You are the ")[1].split(" specialist")[0]
        if stage == "propose":
            return {"proposal": copied if role == "fabrication" else None, "reason": "defer"}
        return agree(stage, system, payload)
    result = decide(ctx, provider=Slow(replies, delay=0))
    chosen = result["selected"]
    assert result["status"] == "agreed" and chosen["agent"] == "fabrication" and chosen["action"] == "design"
    assert chosen["geometry"] == lightest["geometry"] and chosen["candidate_id"] == ctx["candidates"][0]["id"]
    assert any(t["type"] == "normalized" and "geometry" in t["fields"] for t in result["transcript"])
