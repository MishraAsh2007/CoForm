"""ALL coordination rules live in this one file.

- who owns which check, and who may edit which part of the spec
- who sees what
- edit acceptance: accept if no check gets worse, otherwise discuss
- discussion outcome
- turn order, budget, stop conditions
"""
import random

# ---------- ownership ----------
CHECK_OWNERS = {
    "assembly": "assembly",
    "printability": "manufacturing",
    "preform": "manufacturing",
    "fit": "fit",
}

# Top-level spec keys each agent may change through a JSON patch.
EDIT_RIGHTS = {
    "design": "*",
    "generalist": "*",
    "assembly": {"box", "standoffs", "ports", "lid", "lid_screws", "cable_slots", "mount_tabs", "vents"},
    "manufacturing": {"walls", "ribs", "vents", "lid", "standoffs"},
    "fit": {"clearances", "fasteners", "standoffs", "lid"},
}

# Data sources each agent owns (used by agents/base.py to decide what goes in its prompt).
DATA_OWNERS = {
    "design": {"renders", "components"},
    "assembly": {"components"},
    "manufacturing": {"preform"},
    "fit": {"fit_test_results"},
}

COLLECTIVE_AGENTS = ["design", "assembly", "manufacturing", "fit"]


def can_edit(agent, patch):
    """True if every op in the JSON patch touches a key this agent owns."""
    rights = EDIT_RIGHTS.get(agent)
    if rights is None:
        return False
    if rights == "*":
        return True
    for op in patch:
        top = op["path"].strip("/").split("/")[0]
        if top not in rights:
            return False
    return True


def forbidden_keys(agent, patch):
    rights = EDIT_RIGHTS.get(agent, set())
    if rights == "*":
        return []
    return sorted({op["path"].strip("/").split("/")[0] for op in patch} - set(rights))


def visible_checks(agent, results, mode):
    """Everyone sees every check's pass/fail and failure count. Full details
    (every failure with numbers) only for checks the agent owns. In generalist
    and one-shot modes the single agent owns everything."""
    out = {}
    for name, r in results.items():
        owns = mode != "collective" or CHECK_OWNERS.get(name) == agent
        if owns:
            out[name] = r
        else:
            out[name] = {"passed": r.get("passed"), "failure_count": len(r.get("failures", [])),
                         "mocked": r.get("mocked", False)}
    return out


# ---------- scoring and acceptance ----------
def badness(result):
    """(number of failures, total amount in mm). Lower is better."""
    fails = result.get("failures", [])
    return (len(fails), round(sum(abs(f.get("amount_mm", 0.0)) for f in fails), 3))


def worsened(old_results, new_results, tol_mm=0.01):
    """Names of checks that got worse: more failures, or same count but larger total amount."""
    out = []
    for name, new in new_results.items():
        old = old_results.get(name)
        if old is None:
            continue
        (on, oa), (nn, na) = badness(old), badness(new)
        if nn > on or (nn == on and na > oa + tol_mm):
            out.append(name)
    return out


def total_failures(results):
    return sum(badness(r)[0] for r in results.values())


def decide(old_results, new_results, supports, objections):
    """Returns (decision, reason). decision: accepted | rejected.

    1. No check got worse -> accepted.
    2. Otherwise discussion: accepted only if total failures went down AND
       supporters outnumber objectors (the proposer counts as one supporter).
    """
    worse = worsened(old_results, new_results)
    if not worse:
        return "accepted", "no check got worse"
    before, after = total_failures(old_results), total_failures(new_results)
    if after < before and len(supports) > len(objections):
        return "accepted", (f"discussed: {', '.join(worse)} got worse but total failures {before}->{after}, "
                            f"support {len(supports)} vs object {len(objections)}")
    return "rejected", (f"discussed: {', '.join(worse)} got worse; total failures {before}->{after}, "
                        f"support {len(supports)} vs object {len(objections)}")


def auto_objectors(old_results, new_results, mode):
    """Owners of checks that got worse object automatically, with the numbers."""
    if mode != "collective":
        return []
    out = []
    for name in worsened(old_results, new_results):
        (on, oa), (nn, na) = badness(old_results[name]), badness(new_results[name])
        out.append((CHECK_OWNERS[name], name, f"{name}: failures {on}->{nn}, total {oa}->{na} mm"))
    return out


# ---------- turns, budget, stop ----------
def turn_order(mode, seed):
    if mode == "collective":
        rest = COLLECTIVE_AGENTS[1:]
        random.Random(seed).shuffle(rest := list(rest))
        return [COLLECTIVE_AGENTS[0]] + rest  # design always starts; the rest in seeded order
    return ["generalist"] if mode == "generalist" else ["design"]


class Budget:
    """Counts full evaluations (one evaluation = build + every check on one version)."""

    def __init__(self, evaluations, cost_cap_usd):
        self.left = evaluations
        self.used = 0
        self.cost_cap = cost_cap_usd

    def spend(self):
        if self.left <= 0:
            raise RuntimeError("evaluation budget exhausted")
        self.left -= 1
        self.used += 1

    def stop_reason(self, results, cost_usd, idle_rounds):
        if results and all(r.get("passed") for r in results.values()):
            return "all checks pass"
        if self.left <= 0:
            return "evaluation budget exhausted"
        if cost_usd >= self.cost_cap:
            return f"cost cap reached (${cost_usd:.2f})"
        if idle_rounds >= 2:
            return "no agent proposed an edit for 2 rounds"
        return None
