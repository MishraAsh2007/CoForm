"""Agent base: a goal, the checks/data/spec keys it owns, and its actions.

Actions (all through the shared board; ownership is enforced in core/protocol.py and run.py):
  propose_edit(patch, reason[, need_id])  own keys -> normal edit; other agents' keys -> joint edit
  need(to, failure, suggest_patch, reason)  ask the owner of the keys a fix needs
  decline(need_id, reason)
  object(version, reason) / support(version, reason)  reasons carry numbers
  run_my_check(version)
  run_preform()  (Manufacturing / generalist only, limited per run)
The "thinking" is a real model (core/llm.py) or the rule-based mock (agents/mock_policy.py); same JSON.
"""
import json

from agents import mock_policy
from core import protocol
from core.llm import parse_json

ORDER = """Each turn, in this order:
1. If needs are addressed to you (open_needs_to_you, biggest first), handle the FIRST one: propose a patch that
   fixes it (set "need_id"), or decline it with a reason that has numbers.
2. Otherwise fix failures in checks you own (each failure may carry a "hint": a suggested patch and its key owner).
   If the fix needs keys another agent owns, post a need to that owner, or propose a joint edit (the owner must
   support it before it is evaluated).
3. If your own checks pass, work on needs or other agents' failures that YOUR keys can fix. Don't just vote.
4. When every check passes (phase "improve"), keep improving your own goal terms of the quality score until the
   budget runs out; do NOT pass while evaluations are left. A rejected improvement costs only one evaluation and
   the design stays as it was, so small careful steps are fine (see quality_formula for what raises the score).
   An edit is accepted only if every check still passes and the score goes up.
5. Real PreForm warnings (cups, supports, thin walls) count in the FINAL pass/fail but the in-loop "preform" check
   is only a local estimate. Manufacturing / generalist: once local checks pass, use run_preform to see real
   warnings and fix them (e.g. drain holes, other fastener style or wall changes change the orientation PreForm picks).
Look at recent_outcomes (what each past proposal fixed and broke) and don't repeat rejected patches."""

REPLY_FORMAT = """Reply with ONE JSON object and nothing else, one of:
{"action": "propose", "patch": [JSON patch ops], "reason": "why, with numbers", "need_id": optional int}
{"action": "need", "to": "assembly|manufacturing|fit|design", "failure": "the failure text", "suggest_patch": [ops], "reason": "..."}
{"action": "decline", "need_id": int, "reason": "with numbers"}
{"action": "run_preform", "reason": "..."}   (manufacturing / generalist only; uses one of your PreForm requests)
{"action": "pass", "reason": "..."}
Any reply may ALSO carry "needs": [{"to": ..., "failure": ..., "suggest_patch": [ops], "reason": ...}] to post needs
for failures whose fix is in other agents' keys, in the same turn.
Patch paths start at the spec object itself (NOT "/spec/..."), e.g.
  {"op": "replace", "path": "/cable_slots/1/enabled", "value": true}
  {"op": "add", "path": "/mount_tabs/-", "value": {"side": "+y", "offset": 20, "width": 10, "length": 8, "thickness": 3, "hole": 3.4}}
Every value must follow spec_schema exactly. Keep patches small (one fix). Units are mm."""

VOTE_FORMAT = """Reply with ONE JSON object: {"vote": "support" or "object", "reason": "with numbers"}"""


class Agent:
    name = "agent"
    goal = ""
    owns_checks = ()
    owns_data = ()

    def __init__(self, llm, cfg, mode, board, run_id, rng):
        self.llm, self.cfg, self.mode, self.board, self.run_id, self.rng = llm, cfg, mode, board, run_id, rng

    # ---------- actions that only write to the board ----------
    def object(self, version_id, reason):
        self.board.post(self.run_id, self.name, "object", reason, version_id)

    def support(self, version_id, reason="support"):
        self.board.post(self.run_id, self.name, "support", reason, version_id)

    def run_my_check(self, version_id):
        owned = set(self.owns_checks) if self.mode == "collective" else set(protocol.CHECK_OWNERS)
        return {c["name"]: c["details"] for c in self.board.checks(self.run_id, version_id) if c["name"] in owned}

    # ---------- thinking ----------
    def system_prompt(self, extra=""):
        rights = protocol.EDIT_RIGHTS[self.name]
        rights = "all keys" if rights == "*" else ", ".join(sorted(rights)) or "none (use needs / joint edits)"
        goals = ", ".join(protocol.GOALS.get(self.name, []))
        pr = self.cfg["print"]
        return (f"You are the {self.name} agent in a team designing a 3D-printed part (SLA, {pr['printer']}, "
                f"{pr['material']} resin, {pr['layer_thickness_mm']} mm layers).\n"
                f"Goal: {self.goal}\nOnce every check passes, your quality goal terms: {goals}.\n"
                f"You own these checks: {', '.join(self.owns_checks) or 'none'}. "
                f"Your data: {', '.join(self.owns_data) or 'none'}.\nYou own these spec keys: {rights}.\n"
                "There is no boss. An edit is accepted automatically if no check gets worse; otherwise agents vote "
                "and objections must carry numbers. Claims must stay within what the checks show.\n"
                + ORDER + "\n" + REPLY_FORMAT + extra)

    def context(self, state):
        results = state["results"] or {}
        if self.mode == "collective":
            results = protocol.visible_checks(self.name, results, self.mode)
        results = mock_policy.with_hints(results, state, self.cfg)
        needs = protocol.open_needs(state["needs"], self.name)
        return {"spec": state["spec"], "brief": state["brief"], "comp": state["comp"], "rules": state["rules"],
                "cfg": self.cfg, "results": results, "rejected": state["rejected"],
                "phase": "improve" if protocol.all_pass(state["results"] or {}) else "fix",
                "quality": state.get("quality"), "needs_to_me": needs,
                "my_open_needs": [n for n in state["needs"] if n["frm"] == self.name and n["status"] == "open"],
                "recent": state["outcomes"][-6:], "pictures": state.get("pictures", {}),
                "my_errors": state["errors"].get(self.name, []), "real_preform": state.get("real_preform_last"),
                "preform_left": state.get("preform_left", 0), "mode": self.mode, "agent": self.name,
                "budget_left": state.get("budget_left"), "family": state.get("family")}

    def prompt(self, ctx):
        fam = ctx.get("family")
        if fam is not None and fam.name == "freeform":
            from families.freeform import SCHEMA
        else:
            from cad.spec import SCHEMA
        parts = {
            "phase": ctx["phase"],
            "open_needs_to_you": [{k: n[k] for k in ("id", "frm", "failure", "amount_mm", "suggest_patch", "reason")}
                                  for n in ctx["needs_to_me"]],
            "your_open_needs_to_others": [{k: n[k] for k in ("id", "to", "failure")} for n in ctx["my_open_needs"]],
            "brief": ctx["brief"],
            "part": fam.context(ctx["spec"], ctx["brief"]) if fam is not None else {},
            "spec": ctx["spec"],
            "spec_schema": SCHEMA["properties"],
            "key_owner": protocol.KEY_OWNER,
            "check_results": ctx["results"],
            "quality": ctx["quality"],
            "quality_formula": {k: v for k, v in self.cfg["quality"].items()},
            "evaluations_left": ctx.get("budget_left"),
            "recent_outcomes": ctx["recent"],
            "your_rejected_attempts": ctx["my_errors"][-5:],
        }
        if "fit_test_results" in self.owns_data or self.mode != "collective":
            parts["clearance_rules"] = ctx["rules"]
        if self.name in ("manufacturing", "generalist"):
            parts["real_preform_last"] = ctx["real_preform"]
            parts["preform_requests_left"] = ctx["preform_left"]
        return json.dumps(parts, indent=1, default=str)

    def _ask(self, system, prompt, images, mock_fn):
        text, usage = self.llm.complete(system, prompt, images, mock_fn=mock_fn)
        self.board.log_usage(self.run_id, self.name, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"])
        return parse_json(text)

    def act(self, state):
        ctx = self.context(state)
        images = []
        if "renders" in self.owns_data or self.mode != "collective":
            pic = ctx["pictures"].get("assembled")
            images = [pic] if pic else []
        reply = self._ask(self.system_prompt(), self.prompt(ctx), images, lambda: mock_policy.propose(self.name, ctx, self.rng))
        if reply.get("action") == "propose" and not isinstance(reply.get("patch"), list):
            reply = {"action": "pass", "reason": "propose without a patch list"}
        for key in ("patch", "suggest_patch"):          # tolerate "/spec/..." paths
            for op in reply.get(key) or []:
                if isinstance(op, dict) and str(op.get("path", "")).startswith("/spec/"):
                    op["path"] = op["path"][5:]
        if reply.get("action") == "pass":
            self.board.post(self.run_id, self.name, "note", f"pass: {reply.get('reason', '')}"[:500])
        return reply

    def vote(self, state, proposal, breakdown, before_total, after_total, joint=False):
        """Vote on an evaluated proposal (with its per-failure breakdown), or on a joint edit before evaluation."""
        ctx = self.context(state)
        ctx.update({"before_total": before_total, "after_total": after_total, "breakdown": breakdown})
        what = ("JOINT EDIT touching keys you own: support only if you agree this change to your keys is right; "
                "it is evaluated only if you support it." if joint else
                "An evaluated proposal made a check worse. Vote using the per-failure breakdown.")
        prompt = json.dumps({"situation": what, "proposal": proposal, "per_failure_breakdown": breakdown,
                             "total_failures_before_after": [before_total, after_total],
                             "your_checks_now": {k: v for k, v in ctx["results"].items()
                                                 if protocol.CHECK_OWNERS.get(k) == self.name},
                             "spec": ctx["spec"] if joint else None}, indent=1, default=str)
        sysp = self.system_prompt().split(ORDER)[0] + VOTE_FORMAT
        v = self._ask(sysp, prompt, [], lambda: mock_policy.vote(self.name, ctx, proposal, joint))
        return v if v.get("vote") in ("support", "object") else {"vote": "object", "reason": "no valid vote"}
