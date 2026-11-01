import math

import pytest

import config
import optimization as opt
import selection
from coordination import Budget
from schemas import (CandidateRecord, CheckResult, GeometryParams, PrintabilityResult,
                     PrintSettings, Quantity, StructuralResult)

DEFL_LIMIT = config.MAX_TIP_DEFLECTION_MM
STRESS_LIMIT = config.TENSILE_STRENGTH_MPA / config.SAFETY_FACTOR


# --- helpers ------------------------------------------------------------------------

def structural(level, deflection, stress, source, rel=0.02, converged=True):
    extra = {"converged": converged} if source == "fea" else {}
    def q(v, unit):
        return Quantity(value=v, unit=unit, source=source, status="completed",
                        uncertainty=v * rel, **extra)
    return StructuralResult(level=level, tip_deflection=q(deflection, "mm"),
                            max_stress=q(stress, "MPa"), load_case={}, material={})


def printed(passed=True, seconds=3600):
    check = CheckResult(name="cups", status="completed", passed=passed)
    return PrintabilityResult(print_settings=PrintSettings(), checks={"cups": check},
                              print_time=Quantity(value=seconds, unit="s", source="preform",
                                                  status="completed"))


def record(t, mass, results, prints=()):
    p = GeometryParams(plate_thickness=t, rib_height=30, rib_thickness=5)
    return CandidateRecord(candidate_id=p.candidate_id(), params=p, proposed_by="baseline",
                           mass=Quantity(value=mass, unit="g", source="analytical", status="completed"),
                           structural=list(results), printability=list(prints))


# --- selection ----------------------------------------------------------------------

def test_attractive_l1_only_candidate_is_never_selected():
    light = record(2.0, 10.0, [structural(1, DEFL_LIMIT * 0.2, STRESS_LIMIT * 0.2, "analytical")],
                   [printed()])
    heavy = record(5.0, 30.0, [structural(2, DEFL_LIMIT * 0.5, STRESS_LIMIT * 0.5, "fea")],
                   [printed()])
    report = selection.select([light, heavy])
    assert report.selected == heavy.candidate_id
    assert {a.candidate_id: a.status for a in report.candidates}[light.candidate_id] == "unverified"


def test_unconverged_fea_does_not_count():
    rec = record(3.0, 15.0, [structural(2, DEFL_LIMIT * 0.5, STRESS_LIMIT * 0.5, "fea",
                                        converged=False)], [printed()])
    assert selection.assess(rec).status == "unverified"


def test_probability_classes():
    near = record(3.0, 15.0, [structural(2, DEFL_LIMIT * 0.99, STRESS_LIMIT * 0.5, "fea", rel=0.02)],
                  [printed()])
    assert selection.assess(near).status == "uncertain"
    over = record(3.0, 15.0, [structural(2, DEFL_LIMIT * 1.5, STRESS_LIMIT * 0.5, "fea")], [printed()])
    assert selection.assess(over).status == "infeasible"


def test_structurally_feasible_needs_a_passing_print():
    ok = [structural(2, DEFL_LIMIT * 0.5, STRESS_LIMIT * 0.5, "fea")]
    assert selection.assess(record(3.0, 15.0, ok)).status == "print_not_evaluated"
    assert selection.assess(record(3.0, 15.0, ok, [printed(passed=False)])).status == "not_printable"
    feasible = selection.assess(record(3.0, 15.0, ok, [printed(False), printed(True, 1800)]))
    assert feasible.status == "feasible" and feasible.print_time_s == 1800


def test_measurements_outrank_fea():
    fea = structural(2, DEFL_LIMIT * 1.5, STRESS_LIMIT * 0.5, "fea")
    measured = structural(3, DEFL_LIMIT * 0.5, STRESS_LIMIT * 0.5, "measured")
    rec = record(3.0, 15.0, [measured, fea], [printed()])
    assert selection.best_evidence(rec, "tip_deflection").source == "measured"
    assert selection.assess(rec).status == "feasible"


def test_no_feasible_candidate_is_reported_explicitly():
    report = selection.select([record(3.0, 15.0, [structural(1, 0.1, 1.0, "analytical")])])
    assert report.selected is None and "No feasible design" in report.summary


# --- optimization tools ---------------------------------------------------------------

def test_snap_rounds_and_rejects_invalid_geometry():
    p = opt.snap([3.04, 20.06, 2.96])
    assert (p.plate_thickness, p.rib_height, p.rib_thickness) == (3.0, 20.1, 3.0)
    assert opt.snap([8.0, 50.0, 10.0]) is not None
    assert opt.snap([8.0, 55.0, 10.0]).rib_height == 50.0   # clipped to bounds


def test_pareto_front():
    pts = [(1, 5), (2, 3), (3, 4), (4, 1), (2, 3)]
    assert sorted(opt.pareto_front(pts)) == [0, 1, 3, 4]


def test_model_caches_and_charges_budget():
    budget = Budget({"l1": 3})
    model = opt.Model(budget, calibration=None)
    p = GeometryParams(plate_thickness=4, rib_height=30, rib_thickness=5)
    model(p)
    model(p)
    assert budget.used["l1"] == 1


def test_constrained_optimum_respects_budget_and_constraints():
    budget = Budget({"l1": 300})
    best, history = opt.constrained_optimum(opt.Model(budget), seed=1)
    assert budget.used["l1"] <= 300 and len(history) == budget.used["l1"]
    assert best is not None and best["feasible"]
    assert all(m >= 0 for m in best["margins"].values())
    assert best["mass"] == min(e["mass"] for e in history if e["feasible"])


def test_sensitivities_have_physical_signs():
    model = opt.Model(calibration=None)
    s = opt.local_sensitivity(model, GeometryParams(plate_thickness=4, rib_height=30, rib_thickness=5))
    e = s["elasticities"]
    for name in opt.PARAMS:
        assert e[name]["mass"] > 0            # more material, more mass
        assert e[name]["tip_deflection"] < 0  # and a stiffer bracket
    morris = opt.morris_screening(model, trajectories=5, seed=0)
    assert morris["trajectories"] == 5
    assert all(morris[n]["mass"]["mu_star"] > 0 for n in opt.PARAMS)


def test_bayes_opt_finds_feasible_design_and_labels_surrogate():
    model = opt.Model(calibration=None)

    def evaluate(params):
        e = model(params)
        return {"tip_deflection": e["tip_deflection"].value, "max_stress": e["max_stress"].value}

    obs, surrogate = opt.bayes_opt(evaluate, n_init=6, n_iter=8, seed=0)
    assert len(obs) == 14 and any(o["feasible"] for o in obs)
    q = surrogate.predict(obs[0]["params"])["tip_deflection"]
    assert q.source == "surrogate" and not q.counts_for_selection
    assert math.isclose(q.value, obs[0]["tip_deflection"], rel_tol=0.1)
