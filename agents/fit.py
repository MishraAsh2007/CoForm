from agents.base import Agent


class FitAgent(Agent):
    name = "fit"
    goal = ("Set clearances and fastener style from REAL measurements of the printed tolerance test "
            "(data/fit_test_results.yaml): which clearances fit on this printer and resin, which screw posts crack.")
    owns_checks = ("fit",)
    owns_data = ("fit_test_results",)
