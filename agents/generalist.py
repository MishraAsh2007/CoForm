from agents.base import Agent


class GeneralistAgent(Agent):
    name = "generalist"
    goal = ("Design the enclosure alone, with every tool and data source: renders, component data, assembly, "
            "printability, PreForm and fit checks, and the fit-test clearance rules.")
    owns_checks = ("assembly", "printability", "preform", "fit")
    owns_data = ("renders", "components", "preform", "fit_test_results")
