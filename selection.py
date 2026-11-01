# Checks feasibility and selects the lowest-mass candidate satisfying the required constraints.
#
# Only selection-grade evidence counts (Quantity.counts_for_selection: converged
# FEA, validated calibrated models, measurements). L1 and surrogate predictions
# never make a candidate selectable, however attractive. A selectable candidate
# needs P(all structural limits met) >= config.SELECTION_MIN_PROBABILITY and at
# least one print preparation whose checks all completed and passed.
import config
from schemas import CandidateAssessment, CandidateRecord, Quantity, SelectionReport
from structural_evaluator import limits, probability_within

# Preference when several selection-grade values exist for one quantity.
_EVIDENCE_RANK = {"measured": 0, "calibrated": 1, "fea": 2}


def best_evidence(record: CandidateRecord, name) -> Quantity | None:
    """The strongest selection-grade value of `name` (e.g. "max_stress"), newest first within a rank."""
    found = [getattr(r, name) for r in reversed(record.structural)
             if getattr(r, name).counts_for_selection]
    return min(found, key=lambda q: _EVIDENCE_RANK[q.source]) if found else None


def best_print(record: CandidateRecord):
    """The fastest print preparation whose checks all passed, else None."""
    passing = [p for p in record.printability if p.all_checks_passed
               and p.print_time is not None and p.print_time.status == "completed"]
    return min(passing, key=lambda p: p.print_time.value) if passing else None


def assess(record: CandidateRecord, min_probability=None) -> CandidateAssessment:
    p_min = config.SELECTION_MIN_PROBABILITY if min_probability is None else min_probability
    mass = record.mass.value if record.mass and record.mass.status == "completed" else None
    base = {"candidate_id": record.candidate_id, "params": record.params, "mass_g": mass}

    reasons, p_joint = [], 1.0
    for name, limit in limits().items():
        q = best_evidence(record, name)
        if q is None:
            reasons.append(f"{name}: no selection-grade evidence (needs converged FEA or measurements)")
            p_joint = None
            continue
        p = probability_within(q, limit)
        reasons.append(f"{name}: {q.value:.4g} +/- {q.uncertainty or 0:.2g} {q.unit} ({q.source}) "
                       f"vs limit {limit:.4g}; P(within) = {p:.3f}")
        if p_joint is not None:
            p_joint *= p

    if p_joint is None:
        return CandidateAssessment(**base, status="unverified", reasons=reasons)
    if p_joint < 1 - p_min:
        return CandidateAssessment(**base, status="infeasible", p_structural=p_joint, reasons=reasons)
    if p_joint < p_min:
        return CandidateAssessment(**base, status="uncertain", p_structural=p_joint,
                                   reasons=reasons + [f"P(all limits met) {p_joint:.3f} < {p_min}"])

    printed = best_print(record)
    if printed is None:
        if record.printability:
            failing = sorted({n for p in record.printability for n, c in p.checks.items()
                              if not (c.status == "completed" and c.passed)})
            return CandidateAssessment(**base, status="not_printable", p_structural=p_joint,
                                       reasons=reasons + [f"no preparation passed every check "
                                                          f"(failed or unevaluated: {failing})"])
        return CandidateAssessment(**base, status="print_not_evaluated", p_structural=p_joint,
                                   reasons=reasons + ["no PreForm preparation yet"])
    return CandidateAssessment(**base, status="feasible", p_structural=p_joint,
                               print_time_s=printed.print_time.value, reasons=reasons)


def select(records, min_probability=None) -> SelectionReport:
    """Assess every candidate and pick the lowest-mass feasible one, if any."""
    p_min = config.SELECTION_MIN_PROBABILITY if min_probability is None else min_probability
    rows = sorted((assess(r, p_min) for r in records),
                  key=lambda a: (a.mass_g is None, a.mass_g or 0.0))
    feasible = [a for a in rows if a.status == "feasible" and a.mass_g is not None]
    if feasible:
        best = feasible[0]
        best.status = "selected"
        summary = (f"Selected {best.candidate_id}: {best.mass_g:.2f} g, P(structural limits met) "
                   f"= {best.p_structural:.3f}, print time {best.print_time_s / 60:.0f} min.")
    else:
        counts = {}
        for a in rows:
            counts[a.status] = counts.get(a.status, 0) + 1
        summary = f"No feasible design found among {len(rows)} candidates ({counts})."
    return SelectionReport(selected=feasible[0].candidate_id if feasible else None, summary=summary,
                           candidates=rows, min_probability=p_min, limits=limits())
