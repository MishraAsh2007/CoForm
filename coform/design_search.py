"""Checkpoint selection, evaluated-geometry deduplication and bounded unsuccessful search."""
import re

from . import freeform as ff
from .store import digest

FUNCTION = {"fit", "contact", "insertion", "retention"}


def geometry_key(spec):
    """Expressions and literal numbers yielding the same physical candidate share a key."""
    values = ff.values_of(spec)

    def evaluated(value):
        if isinstance(value, list):
            return [evaluated(v) for v in value]
        if isinstance(value, str):
            value = ff.evaluate(value, values)
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            rounded = round(float(value), 8)
            return rounded if rounded else 0.0
        return value

    raw = spec.model_dump(mode="json")
    for section in ("features", "environment", "supports", "loads"):
        for item in raw[section]:
            for cosmetic in ("label", "note"):
                item.pop(cosmetic, None)
            for field in ("at", "size", "radius", "length", "thickness", "min", "max", "force_n"):
                if item.get(field) is not None:
                    item[field] = evaluated(item[field])
            if item.get("rotate"):
                item["rotate"]["degrees"] = evaluated(item["rotate"]["degrees"])
            for leg in item.get("legs") or []:
                leg["length"] = evaluated(leg["length"])
    return digest({k: raw[k] for k in ("features", "environment", "supports", "loads", "requirements")})


def must_fix_count(record) -> int:
    """Verified must-fix review issues on a candidate: fewer is a better checkpoint."""
    return sum(1 for r in record.get("reviews") or [] for i in r.get("issues") or []
               if i.get("severity") == "must_fix" and i.get("verification") == "verified")


def progress_rank(record) -> tuple:
    """How far a tested candidate got: a later stage beats an earlier one, fewer findings beat more.

    0 not a valid spec, 1 did not build (separate pieces, kernel), 2 built but does not work (fit, contact,
    insertion, retention), 3 works but fails another gate (cavity, knife edge...), 4 passes every gate.
    """
    if not record.get("spec"):
        return (0, 0)
    if record.get("geometry_checkpoint"):
        return (4, -must_fix_count(record))
    checks = record.get("checks") or []
    if checks:
        return (3, -sum(c.get("passed") is False for c in checks))
    errors = " ".join(record.get("errors") or [])
    if "does not work with what it is for" in errors:
        return (2, -len(re.findall(r"\[(?:fit|contact|insertion|retention)\]", errors)))
    return (1, 0)


def checkpoint_from(attempts, gates):
    """Recover the best tested functional checkpoint (fewest verified must-fix issues, then latest),
    including historical review-rejected drafts."""
    best, best_key = None, None
    for index, record in enumerate(attempts):
        if record.get("geometry_checkpoint") is False or record.get("status") in ("rejected", "regression_rejected", "provider_error", "error"):
            continue
        try:
            spec = ff.DesignSpec.model_validate(record.get("spec"))
        except (ValueError, TypeError):
            continue
        checks = {c["name"]: c for c in record.get("checks", [])}
        required = {"sealed_cavity", "watertight"} | (FUNCTION if spec.environment else {"mount_clear"})
        if required <= checks.keys() and all(c.get("passed") is True for n, c in checks.items() if n in gates):
            key = (-must_fix_count(record), index)
            if best_key is None or key > best_key:
                best, best_key = record, key
    return {k: best.get(k) for k in ("candidate_id", "spec", "checks", "design_intent", "reviews", "status")} if best else None


def regressions(checkpoint, checks, gates):
    if checkpoint is None:
        return []
    current = {c["name"]: c.get("passed") for c in checks}
    return [c["name"] for c in checkpoint["checks"]
            if c["name"] in gates and c.get("passed") is True and current.get(c["name"]) is not True]


class FailureTracker:
    """Only tested progress clears a failure streak; cosmetic/no-op edits never do."""
    def __init__(self):
        self.count = 0
        self.best_failures = None
        self.signature = None

    def record(self, record):
        status = record.get("status")
        if status not in ("rejected", "regression_rejected"):
            if record.get("geometry_checkpoint"):
                self.count, self.best_failures, self.signature = 0, None, None
            return
        failures = {c["name"]: float(c.get("value", 1)) for c in record.get("checks", []) if c.get("passed") is False}
        improved = (failures and self.best_failures and failures.keys() <= self.best_failures.keys()
                    and (failures.keys() < self.best_failures.keys() or
                         all(v <= self.best_failures[n] for n, v in failures.items()) and
                         any(v < self.best_failures[n] for n, v in failures.items())))
        # Before a valid CAD spec exists, different schema failures can still be progress.
        signature = re.sub(r"\d+(?:\.\d+)?", "#", " ".join(record.get("errors", [])))[:400]
        if not record.get("spec") and not record.get("baseline_candidate_id") and signature != self.signature:
            self.count = 0
        self.count = 1 if improved else self.count + 1
        if failures and (self.best_failures is None or improved):
            self.best_failures = failures
        self.signature = signature
