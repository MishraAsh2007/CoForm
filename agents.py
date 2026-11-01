# Defines each agent's instructions, its tools, and the shared-state digest it reads each turn.
#
# Agents never touch CAD, FEA or PreForm directly: every action is a Tool whose
# input is validated by a Pydantic model and whose function goes through the same
# pipeline (coordination.run_experiment / run_print_prep) and Budget as the
# baselines. Tool results are real data; prompts forbid inventing numbers.
import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

import config
import optimization
import selection
import store
import structural_evaluator as se
from coordination import Budget, run_experiment, run_print_prep
from llm_client import Tool
from schemas import GeometryParams, PrintSettings, Proposal

CANDIDATE_ID = re.compile(r"\bg[0-9a-f]{10}\b")


@dataclass
class Context:
    """Shared state for one run, handed to every tool."""
    run_id: str
    budget: Budget
    round: int = 0
    preform: object = None    # PreForm API handle, or None if not connected
    client: object = None     # Anthropic client (None = default)
    log: list = field(default_factory=list)


@dataclass
class AgentSpec:
    name: str
    system: str
    tools: list


# --- formatting helpers ------------------------------------------------------------

def _r(v, digits=4):
    return None if v is None else float(f"{v:.{digits}g}")


def _q(q):
    if q is None:
        return None
    out = {"value": _r(q.value), "uncertainty": _r(q.uncertainty, 2), "unit": q.unit,
           "source": q.source, "status": q.status}
    if q.converged is not None:
        out["converged"] = q.converged
    return out


def _params(p):
    return [p.plate_thickness, p.rib_height, p.rib_thickness]


def record_summary(record):
    assessment = selection.assess(record)
    return {
        "candidate_id": record.candidate_id, "params_mm": _params(record.params),
        "mass_g": _r(record.mass.value) if record.mass else None,
        "structural": [{"level": r.level, "tip_deflection": _q(r.tip_deflection),
                        "max_stress": _q(r.max_stress), "warnings": r.warnings[:3]}
                       for r in record.structural],
        "printability": [{"all_checks_passed": p.all_checks_passed, "error": p.error,
                          "failed_or_unevaluated": [n for n, c in p.checks.items()
                                                    if not (c.status == "completed" and c.passed)],
                          "print_time_min": _r(p.print_time.value / 60, 3)
                          if p.print_time and p.print_time.value else None,
                          "settings": p.print_settings.model_dump()}
                         for p in record.printability],
        "selection_status": assessment.status, "selection_reasons": assessment.reasons,
        "recent_decisions": record.decisions[-4:],
    }


# --- tool inputs ---------------------------------------------------------------------

def _bounds_note():
    return ", ".join(f"{k} {lo}-{hi}" for k, (lo, hi) in config.PARAMETER_BOUNDS.items())


class Design(BaseModel):
    plate_thickness: float = Field(description="mm")
    rib_height: float = Field(description="mm")
    rib_thickness: float = Field(description="mm")

    def params(self):
        p = optimization.snap([self.plate_thickness, self.rib_height, self.rib_thickness])
        if p is None:
            raise ValueError(f"invalid geometry; bounds (mm): {_bounds_note()}, and "
                             f"rib_height <= {min(config.ARM_LENGTH, config.WALL_HEIGHT)} - plate_thickness")
        return p


class EvaluateArgs(Design):
    accuracy: Literal["screening", "selection"] = Field(
        description="screening: calibrated L1, FEA only if L1 cannot decide; "
                    "selection: always converged FEA unless L1 proves infeasibility")
    force_fea: bool = Field(False, description="run FEA even if L1 is decisive")


class PredictArgs(BaseModel):
    designs: list[Design] = Field(max_length=20)


class CandidateArgs(BaseModel):
    candidate_id: str


class OptimizeArgs(BaseModel):
    seed: int = 0
    max_evals: int = Field(600, ge=60, le=1500, description="calibrated-L1 evaluations to spend")
    top: int = Field(5, ge=1, le=10)


class ParetoArgs(BaseModel):
    objectives: list[Literal["mass", "tip_deflection", "max_stress", "print_time"]] = Field(
        default=["mass", "tip_deflection"], min_length=2, max_length=3)


class ProposeArgs(Design):
    hypothesis: str = Field(description="why this design should be better, and what could go wrong")
    cites: list[str] = Field(default=[], description="candidate ids this proposal builds on")


class PrintArgs(BaseModel):
    candidate_id: str
    orientation_deg: tuple[float, float, float] | None = Field(
        None, description="rotations about X, Y, Z in degrees; omit for PreForm auto-orient")
    support_density: float | None = Field(None, description="omit for PreForm default")
    touchpoint_size_mm: float | None = Field(None, description="omit for PreForm default")


class CompareArgs(BaseModel):
    quantity: Literal["tip_deflection", "max_stress"]


class SpecimenArgs(BaseModel):
    candidate_id: str
    reason: str
    loads_n: list[float] = Field(default=[], description="test loads to apply, N")


class NoArgs(BaseModel):
    pass


# --- tool functions ------------------------------------------------------------------

def _record_or_error(ctx, candidate_id):
    record = store.load_record(ctx.run_id, candidate_id)
    if record is None:
        raise ValueError(f"no candidate {candidate_id} in this run")
    return record


def _prediction(entry):
    return {"candidate_id": entry["params"].candidate_id(), "params_mm": _params(entry["params"]),
            "mass_g": _r(entry["mass"]), "tip_deflection": _q(entry["tip_deflection"]),
            "max_stress": _q(entry["max_stress"]),
            "margins_after_2sigma": {k: _r(v, 3) for k, v in entry["margins"].items()},
            "l1_feasible_with_margin": entry["feasible"]}


def calibration_report(ctx, _args=None):
    cal = se.load_calibration()
    if not cal:
        return {"calibration": None, "note": "no valid L1 calibration; L1 stress is a lower bound only"}
    fits = {n: (None if f is None else {"features": f["features"], "scatter": _r(f["scatter"], 3),
                                        "loo_rmse": _r(f["loo_rmse"], 3), "n": f["n"]})
            for n, f in cal["fits"].items()}
    rows = []
    for row in cal["rows"]:
        p = GeometryParams(**row["params"])
        # Leave-one-out: judge each design with a model fitted without it, so a
        # discrepancy cannot hide by pulling the fit toward itself.
        others = [r for r in cal["rows"] if r["candidate_id"] != row["candidate_id"]]
        loo_fits = {n: se.fit_quantity(others, n, cal["fits"][n]["features"]) if cal["fits"][n] else None
                    for n in se.QUANTITIES}
        predicted = se.evaluate_l1_calibrated(p, {"fits": loo_fits})
        cells = {"candidate_id": row["candidate_id"], "params_mm": _params(p), "source": row["source"]}
        for n in se.QUANTITIES:
            fea_q, pred = row["l2"][n], getattr(predicted, n)
            if fea_q["value"] is None:
                cells[n] = "FEA failed"
                continue
            if loo_fits[n] is None:
                cells[n] = "too few other designs to fit"
                continue
            z = (fea_q["value"] - pred.value) / pred.uncertainty if pred.uncertainty else None
            cells[n] = {"fea": _r(fea_q["value"]), "fea_converged": fea_q["converged"],
                        "calibrated_l1": _r(pred.value), "l1_uncertainty": _r(pred.uncertainty, 2),
                        "z": _r(z, 2), "flag": z is not None and abs(z) > 2}
        rows.append(cells)
    return {"fits": fits, "rows": rows,
            "note": "z = (FEA - calibrated L1) / L1 uncertainty, where L1 is calibrated WITHOUT that "
                    "design (leave-one-out); |z| > 2 flags a discrepancy. Rows with unconverged FEA are "
                    "excluded from that quantity's fit. Rows may come from earlier runs."}


def build_tools(ctx: Context, agent: str):
    def evaluate_design(a: EvaluateArgs):
        record = run_experiment(a.params(), ctx.run_id, ctx.budget, accuracy=a.accuracy,
                                proposed_by=agent, request_level=2 if a.force_fea else None)
        return record_summary(record)

    def predict_designs(a: PredictArgs):
        model = optimization.Model(ctx.budget)
        return [_prediction(model(d.params())) for d in a.designs]

    def get_candidate(a: CandidateArgs):
        return record_summary(_record_or_error(ctx, a.candidate_id))

    def optimize_mass(a: OptimizeArgs):
        model = optimization.Model(ctx.budget)
        optimization.constrained_optimum(model, seed=a.seed, maxiter=max(1, a.max_evals // 30 - 1))
        feasible = sorted((e for e in model.history if e["feasible"]), key=lambda e: e["mass"])
        return {"l1_evaluations": len(model.history), "lightest_l1_feasible": [_prediction(e) for e in feasible[:a.top]],
                "note": "calibrated-L1 search with a 2-sigma margin; not selection evidence"}

    def pareto(a: ParetoArgs):
        rows = []
        for record in store.list_records(ctx.run_id):
            row = {"candidate_id": record.candidate_id, "params_mm": _params(record.params),
                   "mass": record.mass.value if record.mass else None, "evidence": {}}
            for n in ("tip_deflection", "max_stress"):
                q = selection.best_evidence(record, n)
                if q is None and record.structural:
                    q = getattr(record.structural[-1], n)
                row[n] = q.value if q is not None and q.status == "completed" else None
                row["evidence"][n] = q.source if q is not None else None
            printed = selection.best_print(record)
            row["print_time"] = printed.print_time.value / 60 if printed else None
            rows.append(row)
        front = optimization.pareto_table(rows, tuple(a.objectives))
        return {"objectives": a.objectives, "front": [{k: (_r(v) if isinstance(v, float) else v)
                                                       for k, v in r.items()} for r in front],
                "candidates_considered": len(rows),
                "note": "print_time in minutes; evidence shows the source of each structural value"}

    def sensitivity(a: Design):
        result = optimization.local_sensitivity(optimization.Model(ctx.budget), a.params())
        return {"at_mm": result["at"],
                "elasticities": {p: {o: _r(v, 3) for o, v in d.items()}
                                 for p, d in result["elasticities"].items()},
                "note": "d ln(output) / d ln(parameter) on calibrated L1"}

    def propose_design(a: ProposeArgs):
        params = a.params()
        store.append_proposal(ctx.run_id, Proposal(agent=agent, params=params, hypothesis=a.hypothesis,
                                                   cites=a.cites))
        return {"candidate_id": params.candidate_id(), "params_mm": _params(params),
                "status": "queued for the Mechanic to evaluate"}

    def prepare_print(a: PrintArgs):
        if ctx.preform is None:
            raise RuntimeError("PreForm is not connected in this run")
        record = _record_or_error(ctx, a.candidate_id)
        result = run_print_prep(record, ctx.run_id, ctx.budget, ctx.preform,
                                PrintSettings(orientation_deg=a.orientation_deg,
                                              support_density=a.support_density,
                                              touchpoint_size_mm=a.touchpoint_size_mm))
        return {"candidate_id": a.candidate_id, "all_checks_passed": result.all_checks_passed,
                "error": result.error,
                "checks": {n: {"status": c.status, "passed": c.passed} for n, c in result.checks.items()},
                "print_time_min": _r(result.print_time.value / 60, 3)
                if result.print_time and result.print_time.value else None,
                "resin_ml": _r(result.resin_volume.value, 3)
                if result.resin_volume and result.resin_volume.value else None}

    def compare_models(a: CompareArgs):
        cal = se.load_calibration()
        if not cal:
            raise RuntimeError("no calibration data")
        results = []
        for k in range(3):
            for combo in itertools.combinations(se.FEATURES, k):
                fit = se.fit_quantity(cal["rows"], a.quantity, list(combo))
                if fit:
                    results.append({"features": list(combo), "loo_rmse": _r(fit["loo_rmse"], 3),
                                    "scatter": _r(fit["scatter"], 3), "n": fit["n"]})
        results.sort(key=lambda r: r["loo_rmse"])
        in_use = (cal["fits"].get(a.quantity) or {}).get("features")
        return {"quantity": a.quantity, "in_use": in_use,
                "candidates": results[:8],
                "note": "leave-one-out RMSE of log(FEA/L1). The calibration applies the project rule "
                        "automatically (simplest set within 10% of the best) every time it refits."}

    def measurement_status(_a: NoArgs):
        real = Path(config.L1_CALIBRATION_PATH).parent / "measurements" / "real"
        files = sorted(p.name for p in real.glob("*.csv")) if real.is_dir() else []
        return {"real_measurement_files": files,
                "note": "measurement import and L3 calibration are not built yet (milestones M3/M7); "
                        "no measured data is available to analyse. Never treat predictions as measurements."}

    def request_specimen(a: SpecimenArgs):
        _record_or_error(ctx, a.candidate_id)
        store._append_jsonl(ctx.run_id, "specimen_requests.jsonl",
                            a.model_dump_json())
        return {"status": "logged for a human to print and test", "candidate_id": a.candidate_id}

    def tool(name, desc, args, fn):
        return Tool(name, desc, args, fn)

    common = [tool("get_candidate", "Full record of one candidate in this run: parameters, mass, every "
                   "structural result with provenance, print preparations, selection status.",
                   CandidateArgs, get_candidate)]
    calib = tool("calibration_report", "How well calibrated L1 agrees with FEA on every design with FEA "
                 "data: fits, per-design residuals and z-scores, flagged discrepancies.", NoArgs,
                 lambda a: calibration_report(ctx, a))
    return {
        "mechanic": common + [
            tool("evaluate_design", "Build a design and run the Mechanic's level-selected structural "
                 "evaluation (calibrated L1, escalating to converged FEA when needed). FEA takes ~30-60 s "
                 "and costs one l2_fea budget unit. Converged FEA automatically recalibrates L1.",
                 EvaluateArgs, evaluate_design),
            tool("predict_designs", "Fast calibrated-L1 predictions (mass, deflection, stress with "
                 "uncertainty) for up to 20 designs. Costs l1 budget; not selection evidence.",
                 PredictArgs, predict_designs),
            calib],
        "fabrication": common + [
            tool("optimize_mass", "Constrained mass minimization on calibrated L1 (differential "
                 "evolution, 2-sigma margins). Returns the lightest L1-feasible designs.",
                 OptimizeArgs, optimize_mass),
            tool("pareto", "Pareto front over this run's candidates (all objectives minimized), "
                 "using the strongest evidence available for each.", ParetoArgs, pareto),
            tool("sensitivity", "Local elasticities of mass, deflection and stress to each parameter "
                 "at a design (calibrated L1).", Design, sensitivity),
            tool("propose_design", "Queue a design for the Mechanic to evaluate, with your hypothesis.",
                 ProposeArgs, propose_design),
            tool("prepare_print", "Prepare a candidate in PreForm: orient, support, run all 7 "
                 "printability checks, compute print time, save a .form. ~75 s; costs one preform_prep "
                 "budget unit. Never submits a print.", PrintArgs, prepare_print)],
        "measurement": common + [
            calib,
            tool("compare_models", "Compare L1 correction models (feature sets) by leave-one-out "
                 "error: hypothesis comparison for the calibration.", CompareArgs, compare_models),
            tool("measurement_status", "Which real physical measurements exist.", NoArgs,
                 measurement_status),
            tool("request_specimen", "Ask the human team to print and physically test a candidate, "
                 "with the reason it would change a decision.", SpecimenArgs, request_specimen),
            tool("propose_design", "Queue a design for the Mechanic to evaluate (e.g. to test the model "
                 "where it is least reliable), with your hypothesis.", ProposeArgs, propose_design)],
    }


# --- prompts -------------------------------------------------------------------------

def shared_brief():
    stress = config.TENSILE_STRENGTH_MPA / config.SAFETY_FACTOR
    return (
        "You are one of three agents in an engineering team designing a lightweight L-shaped "
        "cantilever bracket for SLA resin printing. The wall plate's back face is fixed; a "
        f"{config.TIP_FORCE_N:g} N downward load acts on the arm's end. Objective: minimize mass "
        f"subject to tip deflection <= {config.MAX_TIP_DEFLECTION_MM:g} mm and peak von Mises stress "
        f"<= {stress:g} MPa, with a print preparation that passes every PreForm check. Adjustable "
        f"(mm): {_bounds_note()}; envelope {config.BRACKET_WIDTH:g} wide, arm {config.ARM_LENGTH:g}, "
        f"wall {config.WALL_HEIGHT:g}. These limits and material values are placeholders.\n\n"
        "Evidence rules: calibrated L1 analytical predictions are fast and carry uncertainty, but are "
        "never selection evidence. Only converged FEA (or real measurements) qualifies a design. The "
        f"final design must have P(all limits met) >= {config.SELECTION_MIN_PROBABILITY} from that "
        "evidence plus a passing print preparation. Never invent numbers: use values returned by "
        "tools and cite candidate ids (like g1a2b3c4d5e). The budget is shared by the whole team; "
        "spend FEA and PreForm runs only where they can change a decision.\n\n"
        "End your turn with a message to the team of at most 150 words: what you did, what you "
        "found, and what you recommend next, citing candidate ids. Disagree with a teammate when "
        "the evidence supports it.")


ROLES = {
    "fabrication": (
        "You are the Fabrication / Design Optimization agent. You own the search for lighter designs "
        "and print preparation. Use optimize_mass, pareto and sensitivity to find promising designs; "
        "propose_design the best one to three with hypotheses that respond to the team's findings "
        "(for example, where FEA disagreed with L1). When a candidate's selection status is "
        "print_not_evaluated (structurally verified), prepare_print it; if checks fail, try other "
        "orientations or request a geometry change."),
    "mechanic": (
        "You are the Mechanic agent. You own structural evaluation. Evaluate pending proposals: use "
        "screening for exploration and selection accuracy (converged FEA) for candidates that could "
        "become the final design. Every converged FEA recalibrates L1, so FEA in regions where L1 is "
        "unreliable also improves later predictions. Explain your choice of evaluation level, and "
        "report where FEA and L1 disagree."),
    "measurement": (
        "You are the Measurement / Scientific Model Calibration agent. You own the comparison of "
        "evidence with models. Physical measurements are not available yet (a print is in progress) "
        "and you must not fabricate any. Use calibration_report and compare_models to check whether "
        "calibrated L1 agrees with FEA within its stated uncertainty, flag discrepancies (|z| > 2), "
        "say which designs need FEA because the model is unreliable there, propose diagnostic "
        "designs where that would settle a question, and request physical specimens when a test "
        "would change a decision."),
}

SINGLE = (
    "You are the only agent: you own optimization, structural evaluation, print preparation and "
    "model calibration. Use any of your tools.")


def build_agents(ctx: Context, mode="collective"):
    if mode == "single":
        merged = {}
        for group in build_tools(ctx, "single").values():
            for t in group:
                merged.setdefault(t.name, t)
        return [AgentSpec("single", shared_brief() + "\n\n" + SINGLE, list(merged.values()))]
    return [AgentSpec(name, shared_brief() + "\n\n" + ROLES[name], build_tools(ctx, name)[name])
            for name in ("fabrication", "mechanic", "measurement")]


# --- digest --------------------------------------------------------------------------

def digest(ctx: Context, agent: str, max_messages=9):
    """The shared state an agent sees at the start of its turn."""
    records = store.list_records(ctx.run_id)
    report = selection.select(records)
    seen = {r.candidate_id for r in records}
    pending = [p for p in store.read_proposals(ctx.run_id) if p.params.candidate_id() not in seen]
    lines = [f"Round {ctx.round}. Your role: {agent}. Budget remaining: {ctx.budget.remaining()}.",
             f"Selection: {report.summary}", "", "Candidates (lightest first):"]
    for a in report.candidates:
        lines.append(f"- {a.candidate_id} {_params(a.params)} mass {_r(a.mass_g)} g, status "
                     f"{a.status}" + (f", P={_r(a.p_structural, 3)}" if a.p_structural is not None else "")
                     + f": {'; '.join(a.reasons[:2])}")
    if not report.candidates:
        lines.append("- none yet")
    lines += ["", "Pending proposals:"]
    lines += [f"- {p.params.candidate_id()} {_params(p.params)} from {p.agent}: {p.hypothesis}"
              for p in pending] or ["- none"]
    lines += ["", "Recent team messages:"]
    lines += [f"- [round {m.round}] {m.agent}: {m.text}" for m in store.read_messages(ctx.run_id)[-max_messages:]] \
        or ["- none yet"]
    return "\n".join(lines)
