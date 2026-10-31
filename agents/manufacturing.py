from agents.base import Agent


class ManufacturingAgent(Agent):
    name = "manufacturing"
    goal = ("Make the parts print well in the configured resin (Alpha Fast) on the Form Studio 1: PreForm results (print time, resin, "
            "warnings) and local printability (wall thickness, slivers, small features, nut pockets).")
    owns_checks = ("printability", "preform")
    owns_data = ("preform",)
