import json
import re

import httpx
import pytest

from coform.agents import decide_independent, decide_single
from coform.budget import BudgetedProvider, SpendGuard
from coform.comparison import run_comparison
from coform.providers import JSONProvider, ProviderError, ProviderResponse
from tests.test_agents import context, proposal


def nvidia(content, finish="stop"):
    def handler(request):
        body = json.loads(request.content)
        assert request.url.host == "integrate.api.nvidia.com" and request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer nvapi-test"
        assert body["messages"][0]["role"] == "system" and body["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}],
                                         "usage": {"prompt_tokens": 120, "completion_tokens": 30}})
    return JSONProvider("nvidia", "nvidia/llama-3.3-nemotron-super-49b-v1.5", "nvapi-test",
                        client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("content", ['{"ok": true}', '{"ok": true}}, "reason": "stray brace seen live"}', '<think>reasoning</think>\n```json\n{"ok": true}\n```', 'Here: {"ok": true}'])
def test_nvidia_provider_parses_json_from_hosted_models(content):
    response = nvidia(content).complete_json("system", {"x": 1}, 256)
    assert response.data == {"ok": True} and (response.input_tokens, response.output_tokens) == (120, 30)


def test_nvidia_truncation_is_an_explicit_failure_with_usage():
    with pytest.raises(ProviderError) as exc:
        nvidia('{"ok": tr', finish="length").complete_json("system", {}, 256)
    assert exc.value.input_tokens == 120
    # Named as a cut-off (not "invalid JSON"), so callers can ask for a shorter reply instead of giving up.
    assert exc.value.truncated and "cut off at the 256-token output limit" in str(exc.value)
    assert exc.value.excerpt == '{"ok": tr'
    with pytest.raises(ProviderError) as other:
        nvidia('{"ok": tr', finish="content_filter").complete_json("system", {}, 256)
    assert not other.value.truncated


def test_nvidia_calls_have_their_own_cap_and_do_not_use_paid_limits(tmp_path):
    guard = SpendGuard(tmp_path / "ledger.sqlite3", max_usd=0.01, max_calls=1, max_per_provider=1, max_free_calls=3)
    provider = BudgetedProvider(nvidia('{"ok": true}'), guard)
    for _ in range(3):
        provider.complete_json("system", {}, 256)
    with pytest.raises(ProviderError, match="call cap of 3"):
        provider.complete_json("system", {}, 256)
    assert guard.summary()["estimated_or_reserved_usd"] == 0


class Scripted:
    name, model = "fake", "scripted"

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def complete_json(self, system, payload, max_output_tokens):
        self.calls.append((system, payload))
        return ProviderResponse(self.responses.pop(0), 300, 50)


def test_single_agent_owns_every_responsibility_and_retries_with_validator_feedback():
    bad = {**proposal(), "load_position_m": .09}  # reserved station: invalid
    agent = Scripted({"proposal": bad}, {"proposal": proposal()})
    result = decide_single(context(), provider=agent)
    assert result["status"] == "agreed" and result["selected"]["supporters"] == ["single"]
    assert result["rounds"] == 2 and result["usage"]["calls"] == 2
    system, payload = agent.calls[1]
    assert "only engineering agent" in system and all(role in system for role in ("mechanics", "fabrication", "measurement"))
    assert any("Reserved prediction condition" in e for e in payload["previous_attempts"][0]["validation_errors"])


def test_independent_specialists_never_see_peers_and_pick_the_plurality():
    other = {**proposal(), "load_position_m": .12}
    agents = {"mechanics": Scripted({"proposal": proposal()}), "fabrication": Scripted({"proposal": other}),
              "measurement": Scripted({"proposal": proposal()})}
    result = decide_independent(context(), provider=agents)
    assert result["status"] == "agreed" and result["selected"]["load_position_m"] == .06
    assert result["selected"]["supporters"] == ["measurement", "mechanics"]
    # Same charters as the collective: fabrication proposes designs, not tests.
    rejected = next(p for p in result["proposals"] if p["agent"] == "fabrication")
    assert not rejected["valid"] and "Outside the fabrication charter" in rejected["validation_errors"][0]
    assert all(a.calls[0][1]["peer_feedback"] == [] for a in agents.values())
    assert not result["reviews"] and result["usage"]["calls"] == 3


def test_offline_baselines_produce_decisions():
    assert decide_single(context())["status"] == "agreed"
    assert decide_independent(context())["selected"]["supporters"] == ["measurement", "mechanics"]


def test_four_method_comparison_shares_evidence_and_summarizes(tmp_path):
    from coform.validation import SCENARIOS
    report = run_comparison([7], tmp_path / "cmp", methods=["adaptive", "collective", "single_agent", "independent"],
                            scenarios=[SCENARIOS[0], SCENARIOS[2]])
    assert len(report["results"]) == 8 and len(report["pairs"]) == 2
    assert set(report["summary"]) == {"adaptive", "collective", "single_agent", "independent"}
    assert {r["policy"] for r in report["results"] if r["method"] == "single_agent"} == {"rule_based_single_agent"}
    assert all(r["scenario"] in ("nominal", "stiff_material_loose_mount") for r in report["results"])
    assert (tmp_path / "cmp" / "results.jsonl").read_text().count("\n") == 8


def test_single_agent_retries_after_declining_and_reasons_are_recorded():
    agent = Scripted({"proposal": None, "reason": "unsure"}, {"proposal": proposal()})
    result = decide_single(context(), provider=agent)
    assert result["status"] == "agreed" and result["rounds"] == 2
    assert any(t["type"] == "evidence_request" and t["reason"] == "unsure" for t in result["transcript"])
    assert "No proposal returned" in agent.calls[1][1]["previous_attempts"][0]["validation_errors"][0]
    quiet = {r: Scripted({"proposal": None, "reason": f"{r} sees no action"}) for r in ("mechanics", "fabrication", "measurement")}
    declined = decide_independent(context(), provider=quiet)
    assert declined["status"] == "no_valid_proposals"
    assert {t["reason"] for t in declined["transcript"] if t["type"] == "evidence_request"} == {f"{r} sees no action" for r in quiet}


def test_unparseable_reply_excerpt_is_kept_for_diagnosis():
    from coform.agents import decide
    agents = {r: nvidia("Sure! Here is my proposal: action=experiment") for r in ("mechanics", "fabrication", "measurement")}
    result = decide(context(), provider=agents)
    error = next(t for t in result["transcript"] if t["type"] == "provider_error")
    assert result["status"] == "provider_error" and error["reply_excerpt"].startswith("Sure! Here is my proposal")


@pytest.mark.parametrize("error, says", [(httpx.ConnectTimeout, "could not connect to nvidia (ConnectTimeout)"),
                                         (httpx.ConnectError, "could not connect to nvidia (ConnectError)"),
                                         (httpx.ReadTimeout, "nvidia did not answer within 90 s (ReadTimeout)")])
def test_connection_failures_are_named_as_such_not_as_rate_limits(error, says):
    def handler(request):
        raise error("boom", request=request)
    provider = JSONProvider("nvidia", "nvidia/nemotron", "nvapi-test", timeout_s=90,
                            client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError, match=re.escape(says)):
        provider.complete_json("system", {}, 256)


def test_a_long_reply_gets_time_to_be_written():
    # NVIDIA once slowed to 29 tokens/s, so 2,900-token drafts missed a flat 90 s timeout three times running.
    from coform.providers import reply_timeout
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    provider = JSONProvider("nvidia", "nvidia/nemotron", "nvapi-test", timeout_s=90,
                            client=httpx.Client(transport=httpx.MockTransport(handler)))
    provider.complete_json("system", {}, 4096)
    provider.complete_json("system", {}, 256)
    assert seen == [pytest.approx(reply_timeout(90, 4096)), 90] and reply_timeout(90, 4096) > 4096 / 29
