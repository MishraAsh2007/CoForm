# Fabrication / design-optimization tools: constrained optimization, Pareto
# analysis, sensitivity analysis and Bayesian optimization.
#
# Optimizers search the cheap calibrated L1 model (or a Gaussian-process
# surrogate of an expensive evaluator) and only *propose* candidates. Nothing
# found here is selectable until it has selection-grade evidence (selection.py).
import math
import warnings

import numpy as np
from pydantic import ValidationError
from scipy.optimize import NonlinearConstraint, differential_evolution
from scipy.stats import norm, qmc

import analytical
import config
import structural_evaluator as se
from coordination import BudgetExceeded
from schemas import GeometryParams, Quantity

PARAMS = ("plate_thickness", "rib_height", "rib_thickness")


def bounds():
    return [config.PARAMETER_BOUNDS[p] for p in PARAMS]


def snap(x):
    """Round a parameter vector to the manufacturing grid; None if the geometry is invalid."""
    step = config.PARAMETER_RESOLUTION_MM
    values = {}
    for name, v, (low, high) in zip(PARAMS, x, bounds()):
        values[name] = round(min(max(round(float(v) / step) * step, low), high), 6)
    try:
        return GeometryParams(**values)
    except ValidationError:
        return None


def _from_unit(u):
    return [low + ui * (high - low) for ui, (low, high) in zip(u, bounds())]


class Model:
    """Calibrated L1 predictions for optimizers, cached and charged to the budget.

    A design is "feasible" here when value + OPT_CONSTRAINT_SIGMAS * sigma is
    within every limit. That is a search criterion, not selection evidence.
    """

    def __init__(self, budget=None, calibration="auto", sigmas=None):
        self.budget = budget
        self.calibration = se.load_calibration() if calibration == "auto" else calibration
        self.sigmas = config.OPT_CONSTRAINT_SIGMAS if sigmas is None else sigmas
        self.cache = {}

    def __call__(self, params: GeometryParams):
        key = params.candidate_id()
        if key not in self.cache:
            if self.budget is not None:
                self.budget.spend("l1")
            l1 = se.evaluate_l1_calibrated(params, self.calibration)
            entry = {"params": params, "mass": analytical.mass(params).value,
                     "tip_deflection": l1.tip_deflection, "max_stress": l1.max_stress}
            entry["margins"] = {n: (lim - (entry[n].value + self.sigmas * (entry[n].uncertainty or 0)))
                                / lim for n, lim in se.limits().items()}
            entry["feasible"] = min(entry["margins"].values()) >= 0
            self.cache[key] = entry
        return self.cache[key]

    @property
    def history(self):
        return list(self.cache.values())

    def best_feasible(self):
        feasible = [e for e in self.history if e["feasible"]]
        return min(feasible, key=lambda e: e["mass"]) if feasible else None


# --- constrained optimization -----------------------------------------------------

def constrained_optimum(model: Model, seed=0, popsize=10, maxiter=None):
    """Minimize mass subject to the L1 chance constraints, by differential evolution.

    Stops early, keeping the best design so far, if the model's budget runs out.
    Returns (best feasible entry or None, all evaluated entries).
    """
    def entry(x):
        params = snap(x)
        return model(params) if params is not None else None

    def objective(x):
        e = entry(x)
        return e["mass"] if e else 1e6

    def constraint(x):
        e = entry(x)
        return min(e["margins"].values()) if e else -1.0

    if maxiter is None:
        remaining = model.budget.remaining()["l1"] if model.budget else 3000
        maxiter = max(1, remaining // (popsize * len(PARAMS)) - 1)
    try:
        differential_evolution(objective, bounds(), seed=seed, popsize=popsize, maxiter=maxiter,
                               constraints=NonlinearConstraint(constraint, 0, np.inf),
                               init="sobol", polish=False, tol=1e-8)
    except BudgetExceeded:
        pass
    return model.best_feasible(), model.history


# --- Pareto analysis ----------------------------------------------------------------

def pareto_front(points):
    """Indices of non-dominated points; every objective is minimized."""
    pts = np.asarray(points, dtype=float)
    keep = []
    for i, p in enumerate(pts):
        dominated = np.any(np.all(pts <= p, axis=1) & np.any(pts < p, axis=1))
        if not dominated:
            keep.append(i)
    return keep


def pareto_table(entries, objectives=("mass", "tip_deflection")):
    """Non-dominated entries for the given objectives (Quantity values are unwrapped)."""
    def value(e, name):
        v = e[name]
        return v.value if isinstance(v, Quantity) else v
    rows = [e for e in entries if all(value(e, o) is not None for o in objectives)]
    front = pareto_front([[value(e, o) for o in objectives] for e in rows])
    return sorted((rows[i] for i in front), key=lambda e: value(e, objectives[0]))


# --- sensitivity analysis ----------------------------------------------------------

OUTPUTS = ("mass", "tip_deflection", "max_stress")


def _outputs(e):
    return {o: (e[o].value if isinstance(e[o], Quantity) else e[o]) for o in OUTPUTS}


def local_sensitivity(model: Model, params: GeometryParams, rel_step=0.05):
    """Elasticities d ln(output) / d ln(parameter) by central differences (one-sided at bounds)."""
    base = _outputs(model(params))
    result = {}
    for name in PARAMS:
        x0 = getattr(params, name)
        lo = snap([x0 * (1 - rel_step) if n == name else getattr(params, n) for n in PARAMS])
        hi = snap([x0 * (1 + rel_step) if n == name else getattr(params, n) for n in PARAMS])
        pts = [(p, getattr(p, name)) for p in (lo, hi) if p is not None and getattr(p, name) != x0]
        pts.append((params, x0))
        pts.sort(key=lambda t: t[1])
        (p_lo, x_lo), (p_hi, x_hi) = pts[0], pts[-1]
        if x_hi == x_lo:
            result[name] = {o: None for o in OUTPUTS}
            continue
        y_lo, y_hi = _outputs(model(p_lo)), _outputs(model(p_hi))
        result[name] = {o: (math.log(y_hi[o]) - math.log(y_lo[o])) / (math.log(x_hi) - math.log(x_lo))
                        for o in OUTPUTS}
    return {"at": params.model_dump(), "base": base, "elasticities": result}


def morris_screening(model: Model, trajectories=10, levels=4, seed=0):
    """Morris elementary effects over the whole parameter box.

    Effects are on mass, ln(deflection) and ln(stress) per unit of normalized
    parameter range. mu_star = mean |effect| (overall influence); sigma = spread
    (non-linearity / interactions). Invalid geometries are resampled.
    """
    rng = np.random.default_rng(seed)
    k, delta = len(PARAMS), levels / (2 * (levels - 1))
    grid = np.arange(levels // 2) / (levels - 1)   # starts that keep x + delta inside [0, 1]
    effects = {p: {o: [] for o in OUTPUTS} for p in PARAMS}
    attempts = 0
    done = 0
    while done < trajectories and attempts < trajectories * 20:
        attempts += 1
        x = rng.choice(grid, size=k)
        points = [x.copy()]
        for i in rng.permutation(k):
            x = x.copy()
            x[i] += delta
            points.append(x)
        params = [snap(_from_unit(u)) for u in points]
        if any(p is None for p in params):
            continue
        order = [int(np.flatnonzero(points[j + 1] - points[j])[0]) for j in range(k)]
        ys = [_outputs(model(p)) for p in params]
        for j, i in enumerate(order):
            for o in OUTPUTS:
                a, b = ys[j][o], ys[j + 1][o]
                diff = (b - a) if o == "mass" else (math.log(b) - math.log(a))
                effects[PARAMS[i]][o].append(diff / delta)
        done += 1
    return {p: {o: {"mu_star": float(np.mean(np.abs(v))) if v else None,
                    "sigma": float(np.std(v, ddof=1)) if len(v) > 1 else None}
                for o, v in outs.items()} for p, outs in effects.items()} | {"trajectories": done}


# --- Bayesian optimization -----------------------------------------------------------

class Surrogate:
    """Gaussian-process models of ln(deflection) and ln(stress) over normalized parameters."""

    def __init__(self, observations, seed=0):
        from sklearn.exceptions import ConvergenceWarning
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

        x = np.array([self.unit(o["params"]) for o in observations])
        self.models = {}
        for name in ("tip_deflection", "max_stress"):
            y = np.log([o[name] for o in observations])
            kernel = (ConstantKernel(1.0) * Matern(length_scale=[0.3] * len(PARAMS), nu=2.5)
                      + WhiteKernel(1e-4, noise_level_bounds=(1e-8, 1e-1)))
            gp = GaussianProcessRegressor(kernel, normalize_y=True, random_state=seed,
                                          n_restarts_optimizer=3)
            with warnings.catch_warnings():
                # Hyperparameters at their bounds (e.g. an irrelevant parameter's
                # length scale) are expected with few, noise-free observations.
                warnings.simplefilter("ignore", ConvergenceWarning)
                self.models[name] = gp.fit(x, y)

    @staticmethod
    def unit(params):
        return [(getattr(params, n) - lo) / (hi - lo) for n, (lo, hi) in zip(PARAMS, bounds())]

    def predict_log(self, params_list):
        x = np.array([self.unit(p) for p in params_list])
        return {n: gp.predict(x, return_std=True) for n, gp in self.models.items()}

    def predict(self, params):
        """Surrogate Quantities (median and approximate 1-sigma) for one design."""
        out = {}
        for name, (mu, sd) in self.predict_log([params]).items():
            value = math.exp(mu[0])
            out[name] = Quantity(value=value, unit="mm" if name == "tip_deflection" else "MPa",
                                 source="surrogate", status="completed", uncertainty=value * sd[0])
        return out

    def probability_feasible(self, params_list):
        preds = self.predict_log(params_list)
        p = np.ones(len(params_list))
        for name, limit in se.limits().items():
            mu, sd = preds[name]
            p *= norm.cdf((math.log(limit) - mu) / np.maximum(sd, 1e-9))
        return p


def bayes_opt(evaluate, n_init=6, n_iter=10, seed=0, pool=4096):
    """Constrained Bayesian optimization of mass with an expensive structural evaluator.

    evaluate(params) -> {"tip_deflection": float, "max_stress": float} from the
    expensive model (e.g. converged FEA), or None if it failed; it may raise
    BudgetExceeded to stop. Mass is computed exactly, so the acquisition is
    (best feasible mass - mass) * P(feasible); before any feasible design is
    found it maximizes P(feasible). Returns (observations, surrogate).
    """
    sampler = qmc.Sobol(len(PARAMS), seed=seed)
    candidates = [p for p in (snap(_from_unit(u)) for u in sampler.random(pool)) if p is not None]
    observations, seen = [], set()

    def observe(params):
        seen.add(params.candidate_id())
        out = evaluate(params)
        if out is None:
            return
        feasible = all(out[n] <= lim for n, lim in se.limits().items())
        observations.append({"params": params, "mass": analytical.mass(params).value,
                             **out, "feasible": feasible})

    surrogate = None
    try:
        for params in candidates[:n_init]:
            observe(params)
        for _ in range(n_iter):
            if len(observations) < 3:
                break
            surrogate = Surrogate(observations, seed)
            pool_left = [p for p in candidates if p.candidate_id() not in seen]
            if not pool_left:
                break
            pf = surrogate.probability_feasible(pool_left)
            feasible_masses = [o["mass"] for o in observations if o["feasible"]]
            if feasible_masses:
                masses = np.array([analytical.mass(p).value for p in pool_left])
                score = np.maximum(min(feasible_masses) - masses, 0) * pf
                if score.max() <= 0:
                    score = pf
            else:
                score = pf
            observe(pool_left[int(np.argmax(score))])
    except BudgetExceeded:
        pass
    if len(observations) >= 3:
        surrogate = Surrogate(observations, seed)
    return observations, surrogate
