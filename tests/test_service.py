import sqlite3

import pytest

from coform.domain import Geometry, Protocol
from coform.service import CoForm


@pytest.fixture
def service(tmp_path):
    value = CoForm(tmp_path)
    yield value
    value.store.close()


@pytest.mark.parametrize("method", ["adaptive", "collective"])
def test_full_demo_budgets_holdout_and_independent_acceptance(service, method):
    initial = service.create_run(method=method)
    result = service.demo(initial["run"]["id"])
    assert result["budget"]["cycles_used"] == 8
    assert result["budget"]["designs_used"] <= 3
    assert result["acceptance"]["status"] == "simulated_pass"
    assert result["acceptance"]["independent_specimens_tested"] == 3
    reserved = {o["id"] for o in result["observations"] if o["purpose"] == "reserved"}
    assert len(reserved) == 1
    for model in result["models"]:
        assert reserved.isdisjoint(model["fit"].get("source_observation_ids", []))
    assert all(o["assessment"]["status"] != "physical_pass" for o in result["observations"])
    assert result["log_verified"]
    with pytest.raises(ValueError, match="frozen"):
        service.step(result["run"]["id"])


def test_records_are_immutable(service):
    state = service.create_run()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with service.store.atomic():
            service.store.db.execute("UPDATE records SET payload='{}' WHERE id=?", (state["run"]["id"],))
    assert service.store.verify()


def test_execution_rejection_keeps_decision_and_usage(service, monkeypatch):
    run_id = service.create_run()["run"]["id"]
    monkeypatch.setattr(service, "_collective_decision", lambda *_: {"status": "agreed", "usage": {"total_tokens": 50},
        "selected": {"action": "experiment", "candidate_id": "not-in-run", "force_n": .3, "load_position_m": .06}})
    result = service.step(run_id, live=True)
    assert result["status"] == "execution_rejected"
    state = service.state(run_id)
    assert len(state["decisions"]) == 1
    assert state["budget"]["tokens_used"] == 50
    assert state["budget"]["cycles_used"] == 0
    assert state["pending_experiment"] is None
    assert state["log_verified"]


def test_physical_ingestion_requires_fabrication_and_actual_load(service):
    state = service.create_run(mode="physical")
    run_id = state["run"]["id"]
    exp = service.step(run_id)["experiment"]
    kwargs = dict(displacement_m=.0005, uncertainty_m=.00002, residual_m=0, fixture_motion_m=0,
                  dwell_s=3, recovery_s=3, human_notes="Manual ruler measurement")
    with pytest.raises(ValueError, match="fabrication"):
        service.record(run_id, exp["id"], **kwargs)
    service.fabricate(run_id, exp["candidate_id"], exp["specimen_id"], Geometry(), "Resin pilot print")
    with pytest.raises(ValueError, match="measured force"):
        service.record(run_id, exp["id"], **kwargs)
    result = service.record(run_id, exp["id"], actual_force_n=.14, actual_load_position_m=.119, **kwargs)
    assert result["observation"]["force_n"] == .14
    assert result["observation"]["load_position_m"] == .119
    assert result["observation"]["source"] == "physical"
    with pytest.raises(ValueError, match="immutable"):
        service.record(run_id, exp["id"], **kwargs)


def test_search_budget_is_reserved_at_queue_time(service):
    run_id = service.create_run(protocol=Protocol(search_cycles=1))["run"]["id"]
    assert service.step(run_id)["status"] == "executed"
    assert service.step(run_id)["status"] == "budget_exhausted"
    assert service.state(run_id)["budget"]["cycles_used"] == 1


def test_protocol_cannot_reserve_the_proof_condition():
    with pytest.raises(ValueError, match="proof"):
        Protocol(diagnostic_positions_m=(.05, .10), reserved_position_m=.12)


def test_live_round_routes_three_providers_with_realistic_context(service, monkeypatch):
    from coform import providers
    from coform.providers import ProviderResponse, encode
    # Independent of the developer's .env role assignment.
    for role, name in (("MECHANICS", "openai"), ("FABRICATION", "anthropic"), ("MEASUREMENT", "gemini")):
        monkeypatch.setenv(f"{role}_PROVIDER", name)
        monkeypatch.delenv(f"{role}_MODEL", raising=False)

    class TestProvider:
        model = "mock-model"

        def __init__(self, name):
            self.name = name
            self.calls = 0

        def complete_json(self, system, payload, max_output_tokens):
            self.calls += 1
            context = payload["context"]
            evidence = context["evidence_ids"][0]
            if "proposals" in payload:
                data = {"reviews": [{"proposal_id": p["id"], "support": True, "reason": "Diagnostic station is informative", "evidence_ids": [evidence]} for p in payload["proposals"]], "ranking": [p["id"] for p in payload["proposals"]]}
            else:
                data = {"proposal": {"action": "experiment", "candidate_id": context["candidate_rows"][0][0], "force_n": .3, "load_position_m": .06, "reason": "Separate mounting compliance from material bending", "evidence_ids": [evidence], "cost": {"cycles": 1, "designs": 0}}}
            return ProviderResponse(data, (len(system) + len(encode(payload))) // 4, len(encode(data)) // 4)

    instances = {name: TestProvider(name) for name in ("openai", "anthropic", "gemini")}
    monkeypatch.setattr(providers, "from_environment", lambda name, model=None: instances[name])
    run_id = service.create_run()["run"]["id"]
    result = service.step(run_id, live=True)
    assert result["status"] == "executed", result
    assert all(p.calls == 2 for p in instances.values())
    assert result["decision"]["usage"]["calls"] == 6
    assert 0 < service.state(run_id)["budget"]["tokens_used"] <= Protocol().token_budget
    service.step(run_id, live=False)
    from coform.comparison import summarize_run
    assert summarize_run(service.state(run_id))["policy"] == "mixed_live_and_offline_specialists"
