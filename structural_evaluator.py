# The Mechanic's structural evaluation: chooses the evaluation level per candidate.
#
# Levels:
#   L1  analytical.py, always run (milliseconds). Optionally corrected by factors
#       fitted to converged L2 FEA on reference designs (`calibrate`).
#   L2  fea.py, mesh-converged FEA (~2 min). Run when L1 cannot decide a limit
#       within its uncertainty, when selection-grade evidence is required, or on
#       explicit request, and only if the budget allows.
#   L3  measurement-calibrated model (milestone M7); not available yet.
#
# Calibration ratios L2/L1 do not depend on the load or Young's modulus (both
# models are linear in F/E); they depend on geometry and Poisson's ratio, so the
# calibration file records those and is ignored if they change.
import itertools
import json
import math
from pathlib import Path

import numpy as np

import analytical
import config
import fea
from schemas import Evaluation, GeometryParams, Quantity, StructuralResult


def limits():
    """Requirement limits: quantity name -> (attribute on StructuralResult, upper limit)."""
    return {"tip_deflection": config.MAX_TIP_DEFLECTION_MM,
            "max_stress": config.TENSILE_STRENGTH_MPA / config.SAFETY_FACTOR}


def probability_within(quantity: Quantity, limit):
    """P(true value <= limit), treating the uncertainty as a normal 1-sigma."""
    if quantity.status != "completed":
        return None
    if not quantity.uncertainty:
        return 1.0 if quantity.value <= limit else 0.0
    z = (limit - quantity.value) / quantity.uncertainty
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# --- L1 calibration against converged L2 --------------------------------------

def _fingerprint():
    return {"envelope": [config.BRACKET_WIDTH, config.ARM_LENGTH, config.WALL_HEIGHT],
            "inner_fillet_radius": config.INNER_FILLET_RADIUS,
            "rib_fillet_radius": config.RIB_FILLET_RADIUS,
            "poisson_ratio": config.POISSON_RATIO}


# Geometric features the L2/L1 correction may depend on (chosen per quantity in
# config.L1_CALIBRATION_FEATURES, by default automatically with select_features).
FEATURES = {
    "depth_ratio": analytical.depth_ratio,
    "rib_slenderness": lambda p: math.log(p.rib_height / p.rib_thickness),
    "rib_to_plate": lambda p: math.log(p.rib_thickness / p.plate_thickness),
    "plate_slenderness": lambda p: math.log((config.ARM_LENGTH - p.plate_thickness)
                                            / p.plate_thickness),
}
QUANTITIES = ("tip_deflection", "max_stress")
_cache = {}
_active_path = None   # set by use_run_calibration; None means the shared file


def _path(path=None):
    return Path(path or _active_path or config.L1_CALIBRATION_PATH)


def use_run_calibration(run_id, sources=None):
    """Give a run its own frozen copy of the shared calibration (runs/<run_id>/calibration.json).

    From then on every calibration read and every record_fea in this process uses
    the copy, so the run learns from its own FEA but neither sees nor changes other
    runs' work. sources=("reference",) starts from the reference designs only,
    hiding earlier runs' experiment rows (use this for method comparisons).
    Re-using a run id keeps its existing copy. Returns the copy's path.
    """
    global _active_path
    import store

    dest = store.RUNS_DIR / str(run_id) / "calibration.json"
    shared = Path(config.L1_CALIBRATION_PATH)
    if not dest.exists() and shared.exists():
        data = json.loads(shared.read_text(encoding="utf-8"))
        if sources is not None:
            data["rows"] = [r for r in data.get("rows", []) if r.get("source") in sources]
        _save(refit(data) if "rows" in data else data, dest)
    _active_path = dest
    return dest


def use_shared_calibration():
    global _active_path
    _active_path = None


def promote_run_calibration(run_id):
    """Merge a run's new FEA rows into the shared calibration. Returns how many rows were added."""
    import store

    run_data = load_calibration(store.RUNS_DIR / str(run_id) / "calibration.json")
    if not run_data:
        return 0
    shared_path = Path(config.L1_CALIBRATION_PATH)
    shared = load_calibration(shared_path) or {"fingerprint": _fingerprint(), "rows": []}
    have = {r["candidate_id"] for r in shared["rows"]}
    new = [r for r in run_data["rows"] if r["candidate_id"] not in have]
    if new:
        shared["rows"] = shared["rows"] + new
        _save(refit(shared), shared_path)
    return len(new)


_l1_cache = {}


def _l1_values(params):
    """Uncalibrated L1 values, memoized (feature selection refits the same designs many times)."""
    key = (params.candidate_id(), config.TIP_FORCE_N, config.YOUNGS_MODULUS_MPA, config.POISSON_RATIO)
    if key not in _l1_cache:
        l1 = analytical.evaluate_l1(params)
        _l1_cache[key] = {n: getattr(l1, n).value for n in QUANTITIES}
    return _l1_cache[key]


def fit_quantity(rows, name, features):
    """Least-squares fit of log(L2/L1) = beta . [1, features] over rows with converged L2.

    Returns None without enough rows (needs more rows than coefficients). Stores
    what prediction intervals need: coefficients, (X^T X)^-1, residual scale,
    the FEA's numerical uncertainty, and the leave-one-out RMSE (from the hat
    matrix) used to compare feature sets.
    """
    used = [r for r in rows if r["l2"][name]["converged"]]
    k = len(features) + 1
    if len(used) <= k:
        return None
    params = [GeometryParams(**r["params"]) for r in used]
    X = np.array([[1.0] + [FEATURES[f](p) for f in features] for p in params])
    y = np.array([math.log(r["l2"][name]["value"] / _l1_values(p)[name])
                  for r, p in zip(used, params)])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    xtx_inv = np.linalg.inv(X.T @ X)
    residuals = y - X @ beta
    scatter = math.sqrt(float(residuals @ residuals) / (len(used) - k))
    leverage = np.einsum("ij,jk,ik->i", X, xtx_inv, X)
    # A point with leverage 1 cannot be predicted from the others: infinite LOO error.
    with np.errstate(divide="ignore", invalid="ignore"):
        loo = np.where(leverage < 1 - 1e-9, residuals / (1 - leverage), np.inf)
    # RMS of the FEA's own relative uncertainties: a shared numerical bias the
    # residual scatter cannot reveal (RMS so one poorly converged design does not dominate).
    numerical = math.sqrt(sum((r["l2"][name]["uncertainty"] / r["l2"][name]["value"]) ** 2
                              for r in used) / len(used))
    return {"features": list(features), "coef": beta.tolist(), "xtx_inv": xtx_inv.tolist(),
            "scatter": scatter, "numerical": numerical,
            "loo_rmse": math.sqrt(float(np.mean(loo**2))), "n": len(used),
            "ranges": {f: [float(X[:, i + 1].min()), float(X[:, i + 1].max())]
                       for i, f in enumerate(features)},
            "designs": [r["candidate_id"] for r in used]}


def correction(fit, params: GeometryParams):
    """(factor, relative 1-sigma uncertainty, note) for one design.

    The uncertainty is a regression prediction interval, so it grows with the
    design's distance from the calibration data (leverage), combined with the
    FEA's numerical uncertainty.
    """
    x0 = np.array([1.0] + [FEATURES[f](params) for f in fit["features"]])
    factor = math.exp(float(x0 @ np.array(fit["coef"])))
    leverage = float(x0 @ np.array(fit["xtx_inv"]) @ x0)
    rel = math.sqrt(fit["scatter"] ** 2 * (1 + leverage) + fit["numerical"] ** 2)
    outside = [f for f, v in zip(fit["features"], x0[1:])
               if not fit["ranges"][f][0] <= v <= fit["ranges"][f][1]]
    note = f"extrapolating in {outside} beyond the calibration designs" if outside else None
    return factor, rel, note


def select_features(rows, name, max_features=2, tolerance=0.10):
    """The project rule: the simplest feature set whose LOO RMSE is within `tolerance` of the best.

    A set is only considered with at least config.L1_MIN_ROWS_PER_COEFFICIENT
    converged rows per coefficient, so small datasets cannot select models that
    merely fit noise. Returns (features, table) where table lists every
    considered set's LOO RMSE. Ties in size are broken by lower LOO RMSE.
    """
    fits = []
    for k in range(max_features + 1):
        for combo in itertools.combinations(FEATURES, k):
            fit = fit_quantity(rows, name, list(combo))
            if fit is not None and fit["n"] >= config.L1_MIN_ROWS_PER_COEFFICIENT * (k + 1):
                fits.append(fit)
    if not fits:
        return [], []
    best = min(f["loo_rmse"] for f in fits)
    eligible = [f for f in fits if f["loo_rmse"] <= best * (1 + tolerance)]
    chosen = min(eligible, key=lambda f: (len(f["features"]), f["loo_rmse"]))
    table = sorted(({"features": f["features"], "loo_rmse": f["loo_rmse"]} for f in fits),
                   key=lambda r: r["loo_rmse"])
    return chosen["features"], table


def features_for(rows, name):
    """Feature set for one quantity: config override, or automatic selection ("auto")."""
    choice = config.L1_CALIBRATION_FEATURES
    choice = choice.get(name, "auto") if isinstance(choice, dict) else choice
    return select_features(rows, name)[0] if choice == "auto" else list(choice)


def refit(data):
    """Recompute every fit from the stored FEA rows, the current L1, and the feature choice."""
    fits = {}
    for n in QUANTITIES:
        fit = fit_quantity(data["rows"], n, features_for(data["rows"], n))
        fits[n] = fit
    data["fits"] = fits
    return data


def load_calibration(path=None):
    """Return the calibration refitted with the current L1 and features.

    Returns None if the file is absent, in an old format, or made for different
    geometry. Cached until the file or the feature choice changes.
    """
    path = _path(path)
    if not path.exists():
        return None
    key = (str(path), path.stat().st_mtime_ns,
           json.dumps([config.L1_CALIBRATION_FEATURES, _fingerprint()], sort_keys=True))
    if key not in _cache:
        data = json.loads(path.read_text(encoding="utf-8"))
        ok = data.get("fingerprint") == _fingerprint() and "rows" in data
        _cache.clear()
        _cache[key] = refit(data) if ok else None
    return _cache[key]


def _save(data, path=None):
    path = _path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _row(params: GeometryParams, l2: StructuralResult, source):
    return {"params": params.model_dump(), "candidate_id": params.candidate_id(), "source": source,
            "l2": {n: getattr(l2, n).model_dump() for n in QUANTITIES},
            "levels": l2.details.get("levels")}


def record_fea(params: GeometryParams, l2: StructuralResult, source="experiment", path=None):
    """Add a converged FEA result to the calibration data and refit (the Mechanic's feedback loop).

    Returns True if the result was used. Results with no converged quantity are ignored.
    """
    if not any(getattr(l2, n).status == "completed" and getattr(l2, n).converged
               for n in QUANTITIES):
        return False
    data = load_calibration(path) or {"fingerprint": _fingerprint(), "rows": []}
    rows = [r for r in data["rows"] if r["candidate_id"] != params.candidate_id()]
    data["rows"] = rows + [_row(params, l2, source)]
    _save(refit(data), path)
    return True


def rescore(path=None):
    """Re-judge every stored FEA row's convergence from its mesh levels under the current rules.

    Use after changing FEA convergence settings; no FEA is re-run. Returns the
    number of quantities whose converged flag changed.
    """
    data = load_calibration(path)
    if not data:
        return 0
    keys = {"tip_deflection": ("tip_deflection_mm", "mm", config.FEA_GCI_MAX_DEFLECTION),
            "max_stress": ("peak_von_mises_mpa", "MPa", config.FEA_GCI_MAX_STRESS)}
    changed = 0
    for row in data["rows"]:
        if not row.get("levels"):
            continue
        for n, (key, unit, threshold) in keys.items():
            q, _ = fea._converged_quantity(row["levels"], key, unit, threshold)
            changed += q.converged != row["l2"][n]["converged"]
            row["l2"][n] = q.model_dump()
    _save(refit(data), path)
    return changed


def calibrate(designs=None, run_id="calibration", log=print):
    """FEA-solve reference designs missing from the calibration data, then refit and save.

    Rows already present (reference or experiment) are kept, so only new designs cost FEA.
    """
    import bracket
    import store

    data = load_calibration() or {"fingerprint": _fingerprint(), "rows": []}
    have = {r["candidate_id"] for r in data["rows"]}
    for t, h, rt in designs or config.L1_REFERENCE_DESIGNS:
        params = GeometryParams(plate_thickness=t, rib_height=h, rib_thickness=rt)
        if params.candidate_id() in have:
            log(f"{(t, h, rt)}: already in the calibration data")
            continue
        stl, step = store.geometry_paths(run_id, params.candidate_id())
        bracket.build_bracket(t, h, rt, stl, step)
        l2 = fea.evaluate_l2(params, step)
        data["rows"].append(_row(params, l2, "reference"))
        _save(refit(data))   # save after each design so an interrupted run keeps its work
        log(f"{(t, h, rt)}: FEA deflection {l2.tip_deflection.value} "
            f"(converged={l2.tip_deflection.converged}), stress {l2.max_stress.value} "
            f"(converged={l2.max_stress.converged})")
    _save(refit(data))
    return data


def evaluate_l1_calibrated(params: GeometryParams, calibration=None):
    """L1, with each quantity corrected by its fitted L2/L1 factor when one exists."""
    result = analytical.evaluate_l1(params)
    fits = (calibration or {}).get("fits") or {}
    updates, notes, warnings = {}, [], []
    for name in QUANTITIES:
        fit = fits.get(name)
        if not fit:
            continue
        factor, rel, warning = correction(fit, params)
        raw = getattr(result, name)
        value = raw.value * factor
        updates[name] = Quantity(value=value, unit=raw.unit, source="analytical",
                                 status="completed", uncertainty=value * rel)
        notes.append(f"{name} multiplied by {factor:.3f} (+/- {rel:.1%}), a correction on "
                     f"{fit['features']} fitted to converged L2 FEA on {fit['n']} designs")
        if warning:
            warnings.append(f"{name}: {warning}")
    if not updates:
        return result
    return result.model_copy(update={
        **updates, "assumptions": result.assumptions + notes,
        "warnings": result.warnings + warnings,
        "details": {"raw_l1": {n: getattr(result, n).value for n in updates}}})


# --- Level selection ------------------------------------------------------------

def l1_verdict(result: StructuralResult, calibrated_stress: bool):
    """'infeasible', 'feasible' or 'ambiguous' from L1 alone.

    A limit is decided only if the value is more than L1_DECISION_SIGMAS from it.
    Uncalibrated L1 stress is nominal (no stress concentration), so it is a lower
    bound: it can prove a stress violation but never stress feasibility.
    """
    k = config.L1_DECISION_SIGMAS
    verdicts = []
    for name, limit in limits().items():
        q = getattr(result, name)
        if name == "max_stress" and not calibrated_stress:
            verdicts.append("infeasible" if q.value > limit else "ambiguous")
            continue
        sigma = q.uncertainty or 0.0
        if q.value - k * sigma > limit:
            verdicts.append("infeasible")
        elif q.value + k * sigma < limit:
            verdicts.append("feasible")
        else:
            verdicts.append("ambiguous")
    if "infeasible" in verdicts:
        return "infeasible"
    return "feasible" if all(v == "feasible" for v in verdicts) else "ambiguous"


def evaluate(params: GeometryParams, step_path=None, accuracy="screening", budget=None,
             request_level=None, calibration="auto"):
    """Evaluate one candidate at the cheapest level that can answer the question.

    accuracy: "screening" (is it worth pursuing?) or "selection" (could it be the
    final design? needs converged FEA). request_level=2 forces FEA if the budget
    allows. step_path is the candidate's STEP file (required for L2).
    """
    if calibration == "auto":
        calibration = load_calibration()
    evaluation = Evaluation(candidate_id=params.candidate_id(), accuracy=accuracy)
    decisions = evaluation.decisions

    if budget is not None:
        budget.spend("l1")
    l1 = evaluate_l1_calibrated(params, calibration)
    evaluation.results.append(l1)
    calibrated_stress = bool(((calibration or {}).get("fits") or {}).get("max_stress"))
    verdict = l1_verdict(l1, calibrated_stress)
    evaluation.l1_verdict = verdict
    decisions.append(f"L1 run ({'calibrated' if calibration else 'uncalibrated'}): verdict {verdict}")

    if request_level == 2:
        reason = "L2 explicitly requested"
    elif verdict == "infeasible":
        reason = None
        decisions.append("L2 skipped: L1 shows a limit is violated beyond its uncertainty")
    elif accuracy == "selection":
        reason = "selection requires converged FEA evidence"
    elif verdict == "ambiguous":
        reason = "L1 cannot decide every limit within its uncertainty"
    else:
        reason = None
        decisions.append("L2 skipped: L1 clears every limit with margin (screening only)")

    if reason:
        if step_path is None:
            decisions.append(f"L2 needed ({reason}) but no STEP geometry was given")
        elif budget is not None and not budget.can_spend("l2_fea"):
            decisions.append(f"L2 needed ({reason}) but the l2_fea budget is exhausted; "
                             "the candidate has no selection-grade evidence")
        else:
            if budget is not None:
                budget.spend("l2_fea")
            l2 = fea.evaluate_l2(params, step_path)
            evaluation.results.append(l2)
            decisions.append(f"L2 run: {reason}")

    if accuracy == "selection":
        decisions.append("L3 not available: no measurement-calibrated model yet")
    return evaluation


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["calibrate"]:
        calibrate(log=lambda m: print(m, flush=True))
        print(json.dumps(load_calibration()["fits"], indent=2))
    elif sys.argv[1:] == ["rescore"]:
        print(f"{rescore()} convergence flag(s) changed")
    else:
        print("usage: python structural_evaluator.py calibrate | rescore")
