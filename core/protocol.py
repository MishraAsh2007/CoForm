"""ALL coordination rules live in this one file.

- ownership: which agent owns which check and which top-level spec keys (one owner per key)
- who sees what
- needs: a failure whose fix needs another agent's keys is posted as a need to that owner (agents can post
  needs themselves; run.py also routes every such failure automatically after each accepted evaluation);
  every agent handles open needs addressed to it first (biggest failure first)
- joint edits: a patch touching other agents' keys is applied only if every owner of those keys supports it
- acceptance: accept if no check gets worse, otherwise discuss (numbers + per-failure breakdown)
- improve phase: once every check passes, an edit is accepted only if every check still passes and the
  quality score (core/quality.py, weights in config.yaml) goes up
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

# One owner per top-level spec key. Anyone else may only touch it through a joint edit or a need.
KEY_OWNER = {
    "box": "assembly", "ports": "assembly", "cable_slots": "assembly", "mount_tabs": "assembly",
    "lid_screws": "assembly",
    "walls": "manufacturing", "ribs": "manufacturing", "vents": "manufacturing", "drain_holes": "manufacturing",
    "clearances": "fit", "fasteners": "fit", "standoffs": "fit", "lid": "fit",
    "name": "design", "bundle": "design", "clearance_source": "fit",
}

COLLECTIVE_AGENTS = ["design", "assembly", "manufacturing", "fit"]


def _rights(agent):
    if agent == "generalist":
        return "*"
    return {k for k, o in KEY_OWNER.items() if o == agent}


EDIT_RIGHTS = {a: _rights(a) for a in COLLECTIVE_AGENTS + ["generalist"]}

# Data sources each agent owns (used by agents/base.py to decide what goes in its prompt).
DATA_OWNERS = {
    "design": {"renders", "components"},
    "assembly": {"components"},
    "manufacturing": {"preform"},
    "fit": {"fit_test_results"},
}

# Each agent's own goal once everything passes (quality score components it works on).
GOALS = {
    "manufacturing": ["volume_ml", "print_height_mm", "preform_warnings"],
    "fit": ["clearance_margin_mm"],
    "assembly": ["assembly_margin_mm"],
    "design": ["extra_features"],
    "generalist": ["volume_ml", "print_height_mm", "preform_warnings", "clearance_margin_mm",
                   "assembly_margin_mm", "extra_features"],
}


def configure(family):
    """Use a part family's ownership maps (families/). The rules below stay the same for every family."""
    global KEY_OWNER, CHECK_OWNERS, EDIT_RIGHTS
    KEY_OWNER = dict(family.key_owner)
    CHECK_OWNERS = dict(family.check_owners)
    EDIT_RIGHTS = {a: _rights(a) for a in COLLECTIVE_AGENTS + ["generalist"]}


def keys_of(patch):
    return {op["path"].strip("/").split("/")[0] for op in patch}


def can_edit(agent, patch):
    """True if every op in the JSON patch touches a key this agent owns."""
    rights = EDIT_RIGHTS.get(agent)
    if rights is None:
        return False
    if rights == "*":
        return True
    return keys_of(patch) <= rights


def forbidden_keys(agent, patch):
    rights = EDIT_RIGHTS.get(agent, set())
    if rights == "*":
        return []
    return sorted(keys_of(patch) - set(rights))


def owners_needed(agent, patch):
    """Owners who must support a joint edit (owners of keys the proposer does not own)."""
    return sorted({KEY_OWNER.get(k, "design") for k in forbidden_keys(agent, patch)} - {agent})


def visible_checks(agent, results, mode):
    """Everyone sees every check's pass/fail and failure count. Full details (every failure with numbers)
    only for checks the agent owns. In generalist and one-shot modes the single agent owns everything."""
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


def all_pass(results):
    return bool(results) and all(r.get("passed") for r in results.values())


def _fkey(check, f):
    t = f.get("target")
    if isinstance(t, (list, dict)):
        t = str(t)
    if t is None:
        t = f.get("kind") or f.get("axis") or f["msg"][:40]    # count/size failures keep their identity
    return (check, f["code"], t, f.get("part"), f.get("kind"), f.get("index"))


def failure_diff(old_results, new_results):
    """Per-failure breakdown of a proposal: fixed, new (broken), better, worse (same failure, other amount)."""
    old = {_fkey(c, f): f for c, r in old_results.items() for f in r.get("failures", [])}
    new = {_fkey(c, f): f for c, r in new_results.items() for f in r.get("failures", [])}
    out = {"fixed": [], "new": [], "better": [], "worse": []}
    for k, f in old.items():
        if k not in new:
            out["fixed"].append(f"{k[0]}: {f['msg']}")
        else:
            a, b = f.get("amount_mm", 0), new[k].get("amount_mm", 0)
            if b < a - 0.01:
                out["better"].append(f"{k[0]}: {new[k]['msg']} ({a} -> {b} mm)")
            elif b > a + 0.01:
                out["worse"].append(f"{k[0]}: {new[k]['msg']} ({a} -> {b} mm)")
    for k, f in new.items():
        if k not in old:
            out["new"].append(f"{k[0]}: {f['msg']}")
    return out


def decide(old_results, new_results, supports, objections, q_old=None, q_new=None):
    """Returns (decision, reason). decision: accepted | rejected.

    Improve phase (every check passed before): accepted only if every check still passes and the
    quality score goes up.
    Otherwise: no check got worse -> accepted. Else discussion: accepted only if total failures went down
    AND supporters outnumber objectors (the proposer counts as one supporter).
    """
    if all_pass(old_results):
        if not all_pass(new_results):
            return "rejected", f"improve phase: a check now fails ({total_failures(new_results)} failures)"
        if q_old is not None and q_new is not None and q_new > q_old + 1e-6:
            return "accepted", f"improve phase: all checks pass, quality {q_old:.2f} -> {q_new:.2f}"
        return "rejected", f"improve phase: quality did not improve ({q_old} -> {q_new})"
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


# ---------- needs ----------
def open_needs(needs, agent):
    """Open needs addressed to an agent, biggest failure first."""
    mine = [n for n in needs if n["to"] == agent and n["status"] == "open"]
    return sorted(mine, key=lambda n: -(n.get("amount_mm") or 0))


# ---------- turns, budget, stop ----------
def turn_order(mode, seed):
    if mode == "collective":
        rest = list(COLLECTIVE_AGENTS[1:])
        random.Random(seed).shuffle(rest)
        return [COLLECTIVE_AGENTS[0]] + rest  # design always starts; the rest in seeded order
    return ["generalist"] if mode == "generalist" else ["design"]


class Budget:
    """Counts full evaluations (one evaluation = build + every local check on one version).
    Passing everything does not stop a run: agents keep improving quality until the budget runs out."""

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
        if self.left <= 0:
            return "evaluation budget exhausted"
        if cost_usd >= self.cost_cap:
            return f"cost cap reached (${cost_usd:.2f})"
        if idle_rounds >= 2:
            return "no agent proposed an edit for 2 rounds"
        return None
