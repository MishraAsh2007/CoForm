# Conventional search baselines for comparison with the agent collective.
#
# Each baseline screens designs with the calibrated L1 model, then verifies its
# lightest promising designs through the same experiment pipeline and budget the
# agents use (coordination.run_experiment / run_print_prep), and finally applies
# the same selection rule. Compare methods only with identical Budget limits.
# The single-agent baseline arrives with the agents (milestone M8).
import numpy as np

import optimization
import selection
import store
from coordination import Budget, BudgetExceeded, run_experiment, run_print_prep


def finalize(run_id, budget: Budget, shortlist, preform=None):
    """Verify shortlisted designs (in the given order) until one is selectable or the budget ends.

    Each design gets selection-grade structural evaluation; structurally
    feasible ones are then prepared in PreForm (if a connection is given).
    """
    for params in shortlist:
        if not budget.can_spend("l1") or not budget.can_spend("l2_fea"):
            break
        record = run_experiment(params, run_id, budget, accuracy="selection")
        if selection.assess(record).status == "print_not_evaluated" and preform is not None:
            if not budget.can_spend("preform_prep"):
                break
            run_print_prep(record, run_id, budget, preform)
        if selection.select(store.list_records(run_id)).selected:
            break
    return selection.select(store.list_records(run_id))


def _shortlist(model: optimization.Model):
    """L1-feasible designs, lightest first."""
    return [e["params"] for e in sorted((e for e in model.history if e["feasible"]),
                                        key=lambda e: e["mass"])]


def random_search(run_id, budget: Budget, seed=0, screening_evals=None, preform=None):
    """Uniform random designs screened by L1, then the lightest L1-feasible ones verified."""
    rng = np.random.default_rng(seed)
    model = optimization.Model(budget)
    target = screening_evals if screening_evals is not None else budget.remaining()["l1"] // 2
    attempts = 0
    while len(model.history) < target and attempts < target * 50:
        attempts += 1
        params = optimization.snap([rng.uniform(lo, hi) for lo, hi in optimization.bounds()])
        if params is None:
            continue
        try:
            model(params)
        except BudgetExceeded:
            break
    return finalize(run_id, budget, _shortlist(model), preform), model


def optimizer_baseline(run_id, budget: Budget, seed=0, screening_evals=None, preform=None):
    """Differential evolution on the L1 chance constraints, then verification of its best designs."""
    model = optimization.Model(budget)
    evals = screening_evals if screening_evals is not None else budget.remaining()["l1"] // 2
    optimization.constrained_optimum(model, seed=seed,
                                     maxiter=max(1, evals // (10 * len(optimization.PARAMS)) - 1))
    return finalize(run_id, budget, _shortlist(model), preform), model
