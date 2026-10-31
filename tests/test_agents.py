import json

import httpx
import pytest

from scienceclaw.agents import Proposal, decide, validate_proposal
from scienceclaw.domain import Geometry, ModelParameters, Protocol
from scienceclaw.providers import JSONProvider, ProviderError, ProviderResponse, from_environment


def context():
    return {
        "observations": [{"id": "obs_initial", "candidate_id": "candidate_1", "purpose": "initial",
                          "force_n": .15, "load_position_m": .12, "displacement_m": .001,
                          "uncertainty_m": .00001, "source": "simulated"}],
        "candidates": [{"id": "candidate_1", "geometry": Geometry().model_dump()}],
        "model": {"id": "model_1", "parameters": ModelParameters().model_dump(), "fit": {"status": "insufficient"}},
        "protocol": Protocol().model_dump(),
        "budget": {"cycles_remaining": 8, "designs_remaining": 3, "tokens_remaining": 12000},
        "tools": {"recommended_geometry": None, "diagnostic_position_m": .06, "predictions": []},
        "evidence_ids": ["obs_initial", "candidate_1", "model_1"], "agent_states": {},
    }


def proposal():
    return {"action": "experiment", "candidate_id": "candidate_1", "geometry": None,
            "force_n": .3, "load_position_m": .06, "reason": "Separate compliance terms",
            "evidence_ids": ["obs_initial"], "cost": {"cycles": 1, "designs": 0}}


class FakeProvider:
    name = "fake"
    model = "offline-test"

    def __init__(self, supports=True, proposal_value=None):
        self.supports = supports
        self.calls = []
        self.proposal_value = proposal_value or proposal()

    def complete_json(self, system, payload, max_output_tokens):
        self.calls.append((system, payload, max_output_tokens))
        if "proposals" not in payload:
            return ProviderResponse({"proposal": self.proposal_value}, 100, 80)
        reviews = [{"proposal_id": p["id"], "support": self.supports, "reason": "Check measured compliance",
                    "evidence_ids": ["obs_initial"]} for p in payload["proposals"]]
        return ProviderResponse({"reviews": reviews, "ranking": [p["id"] for p in payload["proposals"]]}, 100, 80)


def test_offline_specialists_act_within_their_charters():
    result = decide(context())
    assert result["mode"] == "rule_based_demo"
    assert result["status"] == "agreed"
    # Unresolved model: mechanics and measurement propose tests; fabrication owns designs and abstains.
    assert {p["agent"] for p in result["proposals"]} == {"mechanics", "measurement"}
    assert any(t["type"] == "evidence_request" and t["agent"] == "fabrication" for t in result["transcript"])
    assert len(result["selected"]["supporters"]) == 3 and "measurement" in result["selected"]["supporters"]
    assert result["selected"]["load_position_m"] == .06
    assert result["usage"]["calls"] == 0
    assert result["agent_states"]["measurement"]["last_selected"]


@pytest.mark.parametrize("field,value,reason", [
    ("candidate_id", "invented", "candidate"),
    ("evidence_ids", ["invented"], "evidence"),
    ("load_position_m", .09, "Reserved"),
    ("force_n", 3, "Force"),
    ("cost", {"cycles": 0, "designs": 0}, "Costs"),
])
def test_invalid_actions_cannot_bypass_deterministic_checks(field, value, reason):
    value_dict = proposal()
    value_dict[field] = value
    errors = validate_proposal(Proposal.model_validate(value_dict), context())
    assert any(reason in e for e in errors)


def test_search_budget_is_a_hard_gate():
    c = context()
    c["budget"]["cycles_remaining"] = 0
    assert decide(c)["selected"] is None
    errors = validate_proposal(Proposal.model_validate(proposal()), c)
    assert "Insufficient physical budget" in errors


def test_all_three_providers_are_used_and_accounted_together():
    providers = {role: FakeProvider() for role in ("mechanics", "fabrication", "measurement")}
    result = decide(context(), provider=providers)
    assert result["status"] == "agreed"
    assert all(len(p.calls) == 2 for p in providers.values())
    assert result["usage"]["calls"] == 6
    assert result["usage"]["total_tokens"] == 1080
    assert len([e for e in result["transcript"] if e["type"] == "model_call"]) == 6


def test_two_supporters_required_no_human_tie_override():
    providers = {"mechanics": FakeProvider(True), "fabrication": FakeProvider(False), "measurement": FakeProvider(False)}
    c = context()
    c["protocol"]["max_rounds"] = 1
    result = decide(c, provider=providers)
    assert result["status"] == "no_consensus"
    assert result["selected"] is None


def test_bad_evidence_stays_rejected_even_with_unanimous_support():
    raw = proposal()
    raw["evidence_ids"] = ["invented"]
    c = context()
    c["protocol"]["max_rounds"] = 1
    result = decide(c, provider=FakeProvider(proposal_value=raw))
    assert result["selected"] is None
    assert all(not p["valid"] for p in result["proposals"])


def test_tiny_token_budget_makes_no_network_call():
    c = context()
    c["budget"]["tokens_remaining"] = 50
    provider = FakeProvider()
    result = decide(c, provider=provider)
    assert result["status"] == "token_budget_exhausted"
    assert provider.calls == []


def test_reserved_measurement_and_human_notes_are_not_sent_to_models():
    c = context()
    c["observations"].append({"id": "reserved", "purpose": "reserved", "displacement_m": 123.456})
    c["observations"][0]["human_notes"] = "private note"
    provider = FakeProvider()
    decide(c, provider=provider)
    sent = json.dumps([payload for _, payload, _ in provider.calls])
    assert "123.456" not in sent and "private note" not in sent


@pytest.mark.parametrize("name,payload,expected", [
    ("openai", {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}], "usage": {"input_tokens": 20, "output_tokens": 10}}, (20, 10)),
    ("anthropic", {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"ok":true}'}], "usage": {"input_tokens": 20, "output_tokens": 10, "cache_read_input_tokens": 5}}, (25, 10)),
    ("gemini", {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"ok":true}'}]}}], "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 10, "thoughtsTokenCount": 7, "totalTokenCount": 37}}, (20, 17)),
])
def test_rest_provider_parsing_headers_and_reasoning_usage(name, payload, expected):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = JSONProvider(name, "test-model", "secret-test-key", client=client)
        result = provider.complete_json("JSON only", {"test": 1})
        assert result.data == {"ok": True}
        assert (result.input_tokens, result.output_tokens) == expected
        assert "secret-test-key" not in repr(provider)
        assert "secret-test-key" not in str(seen[0].url)
        body = json.loads(seen[0].content)
        assert "tools" not in body


def test_provider_http_error_does_not_leak_body_or_keys():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="secret-test-key private prompt"))) as client:
        provider = JSONProvider("openai", "test-model", "secret-test-key", client=client)
        with pytest.raises(ProviderError) as error:
            provider.complete_json("JSON", {})
        assert "secret-test-key" not in str(error.value)
        assert "401" in str(error.value)


def test_invalid_json_retains_billable_usage():
    payload = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "bad JSON"}]}], "usage": {"input_tokens": 20, "output_tokens": 10}}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        provider = JSONProvider("openai", "test-model", "secret-test-key", client=client)
        with pytest.raises(ProviderError) as error:
            provider.complete_json("JSON", {})
        assert error.value.input_tokens == 20
        assert error.value.output_tokens == 10


def test_config_requires_explicit_model_and_constructor_does_not_connect(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        from_environment("openai")
    assert from_environment("openai", model="configured-model").model == "configured-model"
