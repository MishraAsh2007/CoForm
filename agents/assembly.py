from agents.base import Agent


class AssemblyAgent(Agent):
    name = "assembly"
    goal = ("Make the board, cooler and cables fit: no collisions, board drops in, lid closes, ports line up with "
            "room for cable plugs, screwdriver access, cable slots, robot mounting tabs, size limits.")
    owns_checks = ("assembly",)
    owns_data = ("components",)
