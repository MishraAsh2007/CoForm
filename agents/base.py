"""Agent base: a goal, the checks and data it owns, and four actions.

Actions (all go through the shared board; ownership is enforced here and in core/protocol.py):
  propose_edit(patch, reason)   JSON patch on the spec; refused if it touches keys the agent doesn't own
  object(version, reason)       reason must carry numbers
  support(version)
  run_my_check(version)         results of the checks this agent owns, for a version already evaluated
The "thinking" is either a real model (core/llm.py) or the rule-based mock (agents/mock_policy.py);
both return the same JSON.
"""
import json

from agents import mock_policy
from core import protocol
from core.llm import parse_json

REPLY_FORMAT = """Reply with ONE JSON object and nothing else:
{"action": "propose", "patch": [RFC 6902 JSON patch ops on the spec], "reason": "why, with numbers"}
or {"action": "note", "text": "request for another agent", "suggest_patch": [ops]}
or {"action": "pass", "reason": "..."}
Only patch top-level spec keys you are allowed to edit. Keep patches small (one fix). Units are mm."""

VOTE_FORMAT = """Reply with ONE JSON object: {"vote": "support" or "object", "reason": "with numbers"}"""


class Agent:
    name = "agent"
    goal = ""
    owns_checks = ()
    owns_data = ()

    def __init__(self, llm, cfg, mode, board, run_id, rng):
        self.llm, self.cfg, self.mode, self.board, self.run_id, self.rng = llm, cfg, mode, board, run_id, rng

    # ---------- the four actions ----------
    def propose_edit(self, version_id, patch, reason):
        if not protocol.can_edit(self.name, patch):
            bad = protocol.forbidden_keys(self.name, patch)
            self.board.post(self.run_id, self.name, "note", f"REFUSED (ownership): {self.name} may not edit {bad}",
                            version_id)
            return None
        self.board.post(self.run_id, self.name, "propose", f"{reason} | patch {json.dumps(patch)}", version_id)
        return patch

    def object(self, version_id, reason):
        self.board.post(self.run_id, self.name, "object", reason, version_id)

    def support(self, version_id, reason="support"):
        self.board.post(self.run_id, self.name, "support", reason, version_id)

    def run_my_check(self, version_id):
        owned = set(self.owns_checks) if self.mode == "collective" else set(protocol.CHECK_OWNERS)
        return {c["name"]: c["details"] for c in self.board.checks(self.run_id, version_id) if c["name"] in owned}

    # ---------- thinking ----------
    def system_prompt(self):
        rights = protocol.EDIT_RIGHTS[self.name]
        rights = "all keys" if rights == "*" else ", ".join(sorted(rights))
        pr = self.cfg["print"]
        return (f"You are the {self.name} agent in a team designing a 3D-printed (SLA, {material} resin, {layer} mm layers) enclosure.\n".format(material=pr["material"], layer=pr["layer_thickness_mm"]) +
                f"Goal: {self.goal}\nYou own these checks: {', '.join(self.owns_checks) or 'none'}. "
                f"Your data: {', '.join(self.owns_data) or 'none'}.\nYou may edit spec keys: {rights}.\n"
                "There is no boss. An edit is accepted automatically if no check gets worse; otherwise agents vote "
                "and objections must carry numbers. Claims must stay within what the checks show.\n" + REPLY_FORMAT)

    def context(self, state):
        results = protocol.visible_checks(self.name, state["results"], self.mode) if state["results"] else {}
        if self.mode != "collective":
            results = state["results"] or {}
        requests = [n for n in state["requests"] if n["author"] != self.name]
        return {"spec": state["spec"], "brief": state["brief"], "comp": state["comp"], "rules": state["rules"],
                "cfg": self.cfg, "results": results, "rejected": state["rejected"], "requests": requests,
                "my_requests": {json.dumps(n["patch"], sort_keys=True) for n in state["requests"]
                                if n["author"] == self.name},
                "pictures": state.get("pictures", {})}

    def prompt(self, ctx):
        comp = ctx["comp"]
        ports = [{"name": p["name"], "side": p["side"]} for p in comp["ports"]]
        cables = [{"name": a["name"], "side": a["side"]} for a in comp["cable_access"]]
        parts = {
            "brief": ctx["brief"],
            "component": {"name": comp["name"], "board": comp["board"], "ports": ports, "cable_access": cables},
            "spec": ctx["spec"],
            "check_results": ctx["results"],
            "requests_from_other_agents": [{"author": n["author"], "text": n["text"], "patch": n["patch"]}
                                           for n in ctx["requests"]],
            "already_rejected_patches": sorted(ctx["rejected"])[:20],
        }
        if "fit_test_results" in self.owns_data or self.mode != "collective":
            parts["clearance_rules"] = ctx["rules"]
        return json.dumps(parts, indent=1, default=str)

    def act(self, state):
        ctx = self.context(state)
        images = []
        if "renders" in self.owns_data or self.mode != "collective":
            pic = ctx["pictures"].get("assembled")
            images = [pic] if pic else []
        text, usage = self.llm.complete(self.system_prompt(), self.prompt(ctx), images,
                                        mock_fn=lambda: self.mock_act(ctx))
        self.board.log_usage(self.run_id, self.name, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"])
        reply = parse_json(text)
        if reply.get("action") == "propose" and not isinstance(reply.get("patch"), list):
            reply = {"action": "pass", "reason": "propose without a patch list"}
        return reply

    def mock_act(self, ctx):
        return mock_policy.propose(self.name, ctx, self.rng)

    def vote(self, state, proposal, before, after, before_total, after_total):
        ctx = self.context(state)
        ctx.update({"before_total": before_total, "after_total": after_total})
        prompt = json.dumps({"proposal": proposal, "results_before": protocol.visible_checks(self.name, before, self.mode),
                             "results_after": protocol.visible_checks(self.name, after, self.mode),
                             "total_failures": [before_total, after_total]}, indent=1, default=str)
        text, usage = self.llm.complete(self.system_prompt().replace(REPLY_FORMAT, VOTE_FORMAT), prompt,
                                        mock_fn=lambda: mock_policy.vote(self.name, ctx, proposal))
        self.board.log_usage(self.run_id, self.name, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"])
        v = parse_json(text)
        return v if v.get("vote") in ("support", "object") else {"vote": "object", "reason": "no valid vote"}
