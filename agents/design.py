from agents import mock_policy
from agents.base import Agent


class DesignAgent(Agent):
    name = "design"
    goal = ("Create and edit the enclosure design so it meets the brief. You see rendered pictures of the design "
            "and the component data, and other agents' check summaries (pass/fail and counts). You own no geometry "
            "keys: you get changes made through needs to the owners or joint edits they support. Your quality goal "
            "is a simple design (no features beyond what the brief needs).")
    owns_checks = ()
    owns_data = ("renders", "components")

    def one_shot(self, state):
        """One-shot mode: design once from the brief, no checks; the single agent may edit every key."""
        ctx = self.context(state)
        sysp = self.system_prompt("\nThis is a ONE-SHOT design: no check results, no second try, and you may edit "
                                  "every spec key. Make every change the brief needs in a single patch.")
        img = [ctx["pictures"]["assembled"]] if ctx["pictures"].get("assembled") else []
        return self._ask(sysp, self.prompt(ctx), img, lambda: mock_policy.one_shot(ctx))
