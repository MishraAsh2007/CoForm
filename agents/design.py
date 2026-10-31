from agents import mock_policy
from agents.base import Agent


class DesignAgent(Agent):
    name = "design"
    goal = ("Create and edit the enclosure design so it meets the brief. You see rendered pictures of the design "
            "and the component data, and other agents' check summaries (pass/fail and counts).")
    owns_checks = ()
    owns_data = ("renders", "components")

    def one_shot(self, state):
        """One-shot mode: design once from the brief, no checks."""
        ctx = self.context(state)
        text, usage = self.llm.complete(
            self.system_prompt() + "\nThis is a ONE-SHOT design: you get no check results and no second try. "
            "Make every change the brief needs in a single patch.", self.prompt(ctx),
            [ctx["pictures"]["assembled"]] if ctx["pictures"].get("assembled") else [],
            mock_fn=lambda: mock_policy.one_shot(ctx))
        self.board.log_usage(self.run_id, self.name, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"])
        from core.llm import parse_json
        return parse_json(text)
