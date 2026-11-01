import json
from types import SimpleNamespace

import pytest

import agents
import config
import llm_client
import store
from coordination import Budget, run_agents
from test_baselines import fake_pipeline  # noqa: F401  (fixture: fake geometry/FEA, tmp runs dir)


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(name, inp, i="t1"):
    return SimpleNamespace(type="tool_use", id=i, name=name, input=inp)


def response(blocks, stop="tool_use"):
    return SimpleNamespace(content=blocks, stop_reason=stop, stop_details=None,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class FakeClient:
    """Plays back scripted responses; `script(system, messages, n)` returns the next one."""

    def __init__(self, script):
        self.script = script
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script(kwargs["system"][0]["text"], kwargs["messages"], len(self.calls))


ECHO = llm_client.Tool("echo", "echo a value", agents.CandidateArgs,
                       lambda a: {"echo": a.candidate_id})


def test_turn_runs_tools_then_returns_final_text():
    replies = iter([response([tool_use("echo", {"candidate_id": "gabc"})]),
                    response([text("done: gabc")], stop="end_turn")])
    client = FakeClient(lambda s, m, n: next(replies))
    budget = Budget({"llm_calls": 5})
    turn = llm_client.run_turn("sys", "go", [ECHO], budget, client=client)
    assert turn.text == "done: gabc" and turn.api_calls == 2 and budget.used["llm_calls"] == 2
    assert turn.tool_calls[0]["ok"] and json.loads(turn.tool_calls[0]["result"]) == {"echo": "gabc"}
    request = client.calls[0]
    assert request["model"] == config.CLAUDE_MODEL and request["fallbacks"] == "default"
    assert request["betas"] == [config.CLAUDE_FALLBACK_BETA]
    assert request["output_config"] == {"effort": config.CLAUDE_EFFORT}
    assert "tool_choice" not in request
    # The second request carries the full assistant turn, then the tool result.
    second = client.calls[1]["messages"]
    assert second[1]["role"] == "assistant" and second[2]["content"][0]["tool_use_id"] == "t1"


def test_invalid_tool_input_is_reported_not_run():
    replies = iter([response([tool_use("echo", {"wrong": 1}), tool_use("nope", {}, i="t2")]),
                    response([text("ok")], stop="end_turn")])
    client = FakeClient(lambda s, m, n: next(replies))
    turn = llm_client.run_turn("sys", "go", [ECHO], Budget({"llm_calls": 5}), client=client)
    results = client.calls[1]["messages"][2]["content"]
    assert len(results) == 2 and all(r["is_error"] for r in results)   # both in one message
    assert "invalid input" in results[0]["content"] and "unknown tool" in results[1]["content"]
    assert [c["ok"] for c in turn.tool_calls] == [False, False]


def test_turn_respects_call_cap_and_budget():
    client = FakeClient(lambda s, m, n: response([tool_use("echo", {"candidate_id": "x"}, i=f"t{n}")]))
    turn = llm_client.run_turn("sys", "go", [ECHO], Budget({"llm_calls": 10}), client=client,
                               max_api_calls=3)
    assert turn.api_calls == 3 and "cap" in turn.notes[0]
    turn = llm_client.run_turn("sys", "go", [ECHO], Budget({"llm_calls": 2}), client=client)
    assert turn.api_calls == 2 and "budget exhausted" in turn.notes[0]


def test_refusal_ends_turn_without_text():
    declined = response([], stop="refusal")
    declined.stop_details = SimpleNamespace(category="cyber")
    turn = llm_client.run_turn("sys", "go", [ECHO], Budget({"llm_calls": 5}),
                               client=FakeClient(lambda s, m, n: declined))
    assert turn.text == "" and "declined" in turn.notes[0]


def test_collective_round_end_to_end(fake_pipeline, monkeypatch):  # noqa: F811
    design = {"plate_thickness": 6.0, "rib_height": 45.0, "rib_thickness": 8.0}  # L1-feasible
    cid = agents.Design(**design).params().candidate_id()

    def script(system, messages, n):
        done = any(m["role"] == "user" and isinstance(m["content"], list) for m in messages)
        if done:
            return response([text(f"Finished; see {cid}.")], stop="end_turn")
        if "Fabrication" in system:
            return response([tool_use("propose_design", {**design, "hypothesis": "baseline check"})])
        if "Mechanic" in system:
            assert cid in messages[0]["content"]   # the pending proposal is in the digest
            return response([tool_use("evaluate_design", {**design, "accuracy": "selection"})])
        return response([tool_use("calibration_report", {}, i="a"),
                         tool_use("measurement_status", {}, i="b")])

    budget = Budget({"l1": 100, "l2_fea": 2, "preform_prep": 0, "llm_calls": 6})
    report = run_agents("agents", budget, rounds=1, client=FakeClient(script), log=lambda m: None)

    messages = store.read_messages("agents")
    assert [m.agent for m in messages] == ["fabrication", "mechanic", "measurement"]
    assert all(cid in m.cites for m in messages)
    assert len(fake_pipeline) == 1 and budget.used["l2_fea"] == 1 and budget.used["llm_calls"] == 6
    turns = store.read_turns("agents")
    assert all(c["ok"] for t in turns for c in t["tool_calls"])
    # No PreForm in this run, so the verified design waits for a print preparation.
    assert report.selected is None
    assert report.candidates[0].status == "print_not_evaluated"
    assert (store.RUNS_DIR / "agents" / "selection.json").exists()


def test_single_agent_gets_every_tool():
    ctx = agents.Context("x", Budget({}))
    (spec,) = agents.build_agents(ctx, mode="single")
    names = {t.name for t in spec.tools}
    assert {"evaluate_design", "optimize_mass", "prepare_print", "compare_models"} <= names
    assert len(names) == len(spec.tools)


def test_tool_schemas_are_plain_json_schema():
    ctx = agents.Context("x", Budget({}))
    for spec in agents.build_agents(ctx):
        for t in spec.tools:
            schema = t.definition()["input_schema"]
            assert schema["type"] == "object"
            json.dumps(schema)
