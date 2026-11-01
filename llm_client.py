# Calls Claude (Anthropic SDK) and runs one agent turn as a tool-use loop.
#
# A manual loop rather than the SDK tool runner, so that every API request is
# charged to the run's Budget ("llm_calls") like any other evaluation, every
# tool input is validated with Pydantic before its function runs, and one
# agent's turn is capped at config.AGENT_MAX_API_CALLS_PER_TURN requests.
# The conversation is only ever appended to (full response.content, thinking
# included), never edited.
import json
from dataclasses import dataclass, field
from typing import Callable

import anthropic
from pydantic import BaseModel, ValidationError

import config
from coordination import Budget, BudgetExceeded


@dataclass
class Tool:
    """A function an agent may call. `args` validates the input; `fn(args)` returns JSON-able data."""
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[[BaseModel], object]

    def definition(self):
        return {"name": self.name, "description": self.description,
                "input_schema": self.args.model_json_schema()}


@dataclass
class TurnResult:
    text: str = ""
    stop_reason: str | None = None
    api_calls: int = 0
    tool_calls: list = field(default_factory=list)   # {"name", "input", "ok", "result"}
    input_tokens: int = 0
    output_tokens: int = 0
    notes: list = field(default_factory=list)


def _execute(tool: Tool | None, block):
    """Run one tool_use block; returns (tool_result block, log entry). Failures become is_error results."""
    if tool is None:
        content, ok = f"unknown tool {block.name!r}", False
    else:
        try:
            args = tool.args.model_validate(block.input)
            content, ok = json.dumps(tool.fn(args), default=str), True
        except ValidationError as e:
            content, ok = f"invalid input: {e}", False
        except BudgetExceeded as e:
            content, ok = f"budget exhausted: {e}", False
        except Exception as e:   # report tool failures to the agent instead of crashing the run
            content, ok = f"{type(e).__name__}: {e}", False
    result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
    if not ok:
        result["is_error"] = True
    return result, {"name": block.name, "input": block.input, "ok": ok, "result": content[:2000]}


def run_turn(system: str, prompt: str, tools: list[Tool], budget: Budget, client=None,
             max_api_calls=None) -> TurnResult:
    """One agent turn: call Claude, run requested tools, repeat until it stops or a cap is hit."""
    client = client or anthropic.Anthropic()
    max_api_calls = config.AGENT_MAX_API_CALLS_PER_TURN if max_api_calls is None else max_api_calls
    by_name = {t.name: t for t in tools}
    definitions = [t.definition() for t in tools]
    # Tools and system are identical every request, so they form a cacheable prefix.
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": prompt}]
    turn = TurnResult()
    response = None

    while True:
        if turn.api_calls >= max_api_calls:
            turn.notes.append(f"turn stopped at the {max_api_calls}-request cap")
            break
        try:
            budget.spend("llm_calls")
        except BudgetExceeded:
            turn.notes.append("turn stopped: llm_calls budget exhausted")
            break
        turn.api_calls += 1
        response = client.beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=config.CLAUDE_MAX_TOKENS,
            system=system_blocks,
            tools=definitions,
            messages=messages,
            output_config={"effort": config.CLAUDE_EFFORT},
            betas=[config.CLAUDE_FALLBACK_BETA],
            fallbacks="default",
        )
        turn.stop_reason = response.stop_reason
        turn.input_tokens += response.usage.input_tokens or 0
        turn.output_tokens += response.usage.output_tokens or 0

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            turn.notes.append(f"request declined (category {category})")
            break
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason != "tool_use":
            break

        results = []
        for block in response.content:
            if block.type == "tool_use":
                result, log = _execute(by_name.get(block.name), block)
                results.append(result)
                turn.tool_calls.append(log)
        messages.append({"role": "user", "content": results})   # all results in one message

    if response is not None and response.stop_reason != "refusal":
        turn.text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    return turn
