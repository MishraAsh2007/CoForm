"""Measurement reduction and fixed acceptance rules.

No result in this module is an ultimate-strength or safe-working-load claim.
Trace uncertainty is a conservative cycle estimate, not a standard error of
the video-frame mean: consecutive camera frames are correlated.
"""
from __future__ import annotations

import csv
import io
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median, stdev
from typing import Any

from .domain import Observation, Protocol


def _same_test(value: float, target: float) -> bool:
    # Only accommodate numerical serialization, not an undeclared test tolerance.
    return math.isclose(value, target, rel_tol=1e-6, abs_tol=1e-9)


def classify_observation(observation: Observation, protocol: Protocol) -> dict[str, Any]:
    """Classify one cycle against a frozen protocol using a two-sigma guard band.

    A valid off-proof search measurement is useful for fitting, but cannot earn
    a passing proof-test status. Permanent set is a failure; bad metrology and
    motion outside the model are invalid. A confidence interval crossing a
    fixed limit is uncertain and must never become a pass by rounding.
    """
    obs = observation
    reasons: list[str] = []
    invalid: list[str] = []
    uncertainty = 2 * obs.uncertainty_m
    upper = abs(obs.displacement_m) + uncertainty
    lower = max(0.0, abs(obs.displacement_m) - uncertainty)
    residual_upper = abs(obs.residual_m) + uncertainty
    residual_lower = max(0.0, abs(obs.residual_m) - uncertainty)
    if obs.quality_flags:
        invalid.extend(f"quality:{flag}" for flag in obs.quality_flags)
    if abs(obs.fixture_motion_m) > protocol.max_fixture_motion_m:
        invalid.append("fixture_motion_exceeds_limit")
    if obs.dwell_s + 1e-9 < protocol.dwell_s:
        invalid.append("dwell_too_short")
    if obs.recovery_s + 1e-9 < protocol.recovery_s:
        invalid.append("recovery_too_short")
    if abs(obs.displacement_m) / obs.load_position_m > protocol.max_deflection_ratio:
        invalid.append("outside_small_deflection_model")
    # A downward known load should not create a confidently upward displacement.
    if obs.displacement_m + uncertainty < 0:
        invalid.append("unexpected_displacement_direction")

    is_proof = _same_test(obs.force_n, protocol.proof_force_n) and _same_test(
        obs.load_position_m, protocol.proof_position_m
    )
    if invalid:
        status = "invalid"
        reasons.extend(invalid)
    elif residual_lower > protocol.max_residual_m:
        status = "fail"
        reasons.append("permanent_set_exceeds_limit")
    elif is_proof and lower > protocol.max_deflection_m:
        status = "fail"
        reasons.append("deflection_exceeds_limit")
    elif not is_proof:
        status = "uncertain"
        reasons.append("non_proof_condition")
    elif obs.uncertainty_m > protocol.max_uncertainty_m:
        status = "uncertain"
        reasons.append("measurement_uncertainty_exceeds_limit")
    elif upper > protocol.max_deflection_m or residual_upper > protocol.max_residual_m:
        status = "uncertain"
        if upper > protocol.max_deflection_m:
            reasons.append("deflection_interval_crosses_limit")
        if residual_upper > protocol.max_residual_m:
            reasons.append("permanent_set_interval_crosses_limit")
    else:
        status = "physical_pass" if obs.source == "physical" else "simulated_pass"
    return {
        "observation_id": obs.id,
        "specimen_id": obs.specimen_id,
        "status": status,
        "source": obs.source,
        "purpose": obs.purpose,
        "is_proof_condition": is_proof,
        "reasons": reasons,
        "deflection_interval_m": [lower, upper],
        "residual_interval_m": [residual_lower, residual_upper],
        "guard_band_sigma": 2,
    }


def acceptance_summary(observations: list[Observation], protocol: Protocol) -> dict[str, Any]:
    """Summarize acceptance cycles with independent copies and provenance checks.

    Supply all observations in the run to detect reuse of search specimens.
    Repeated cycles do not increase the independent-copy count, and a failing
    repeat is retained. Search cycles are explicitly excluded from acceptance.
    """
    selected = [obs for obs in observations if obs.purpose == "acceptance"]
    excluded = [obs.id for obs in observations if obs.purpose != "acceptance"]
    reused_ids = {obs.specimen_id for obs in observations if obs.purpose != "acceptance"}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    results = [classify_observation(obs, protocol) for obs in selected]
    errors: list[str] = []
    if len({obs.source for obs in selected}) > 1:
        errors.append("mixed_physical_and_simulated_sources")
    if len({obs.candidate_id for obs in selected}) > 1:
        errors.append("mixed_candidates")
    if len({obs.id for obs in selected}) != len(selected):
        errors.append("duplicate_observation_ids")
    if len({obs.experiment_id for obs in selected}) != len(selected):
        errors.append("duplicate_experiment_ids")
    for result in results:
        groups[result["specimen_id"]].append(result)
    specimens: list[dict[str, Any]] = []
    priority = {"invalid": 4, "fail": 3, "uncertain": 2, "physical_pass": 1, "simulated_pass": 1}
    for specimen_id, cycles in sorted(groups.items()):
        status = max((cycle["status"] for cycle in cycles), key=priority.__getitem__)
        if specimen_id in reused_ids:
            status = "invalid"
            errors.append(f"acceptance_specimen_previously_used:{specimen_id}")
        specimens.append({
            "specimen_id": specimen_id,
            "status": status,
            "cycle_count": len(cycles),
            "observation_ids": [cycle["observation_id"] for cycle in cycles],
        })
    source = selected[0].source if selected and len({o.source for o in selected}) == 1 else None
    pass_label = "physical_pass" if source == "physical" else "simulated_pass"
    passing = sum(specimen["status"] == pass_label for specimen in specimens)
    if errors:
        status = "invalid"
    elif any(specimen["status"] == "invalid" for specimen in specimens):
        status = "invalid"
    elif any(specimen["status"] == "fail" for specimen in specimens):
        status = "fail"
    elif len(specimens) < protocol.final_copies:
        status = "incomplete"
    elif any(specimen["status"] == "uncertain" for specimen in specimens):
        status = "uncertain"
    else:
        status = pass_label
    return {
        "status": status,
        "source": source,
        "required_independent_copies": protocol.final_copies,
        "independent_specimens_tested": len(specimens),
        "passing_specimens": passing,
        "cycle_count": len(selected),
        "cycle_status_counts": dict(Counter(result["status"] for result in results)),
        "specimens": specimens,
        "observations": results,
        "excluded_observation_ids": excluded,
        "errors": sorted(set(errors)),
    }


TRACE_COLUMNS = (
    "frame", "time_s", "phase", "fixture_x_px", "fixture_y_px",
    "root_x_px", "root_y_px", "load_x_px", "load_y_px",
)
TRACE_PHASES = ("unloaded", "loaded", "recovered")


def analyze_trace(
    csv_text: str,
    *,
    pixels_per_m: float,
    calibration_uncertainty_fraction: float = 0.01,
    max_jump_m: float = 0.002,
    max_fixture_motion_m: float = 0.0001,
    max_uncertainty_m: float = 0.00015,
) -> dict[str, Any]:
    """Reduce a marker CSV from one load/unload cycle, with y positive downwards.

    Coordinates must come from the same fixed camera and calibration plane.
    Rows are phase *settled windows*, not moving/loading frames. Input is text
    for safe API uploads; use ``analyze_trace_file`` for an existing local file.
    """
    numeric_config = (pixels_per_m, calibration_uncertainty_fraction, max_jump_m,
                      max_fixture_motion_m, max_uncertainty_m)
    if not all(math.isfinite(value) for value in numeric_config):
        raise ValueError("Trace calibration and thresholds must be finite")
    if pixels_per_m <= 0 or calibration_uncertainty_fraction < 0 or min(
        max_jump_m, max_fixture_motion_m, max_uncertainty_m
    ) <= 0:
        raise ValueError("Calibration and thresholds must be positive; calibration uncertainty may be zero")
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    if not reader.fieldnames or not set(TRACE_COLUMNS).issubset(reader.fieldnames):
        raise ValueError("Trace CSV requires columns: " + ",".join(TRACE_COLUMNS))
    groups: dict[str, list[dict[str, float]]] = {phase: [] for phase in TRACE_PHASES}
    flags: set[str] = set()
    previous: dict[str, float] | None = None
    previous_phase: str | None = None
    phase_number = -1
    dropped_rows = 0
    for row in reader:
        phase = (row.get("phase") or "").strip().lower()
        if phase not in groups:
            raise ValueError("Each trace phase must be unloaded, loaded, or recovered")
        try:
            values = {key: float(row[key]) for key in TRACE_COLUMNS if key != "phase"}
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError("Nonfinite marker value")
            if values["frame"] < 0 or not values["frame"].is_integer() or values["time_s"] < 0:
                raise ValueError("Invalid frame or time")
        except (TypeError, ValueError):
            flags.add("missing_or_invalid_marker_sample")
            dropped_rows += 1
            continue
        if previous is not None and (
            values["frame"] <= previous["frame"] or values["time_s"] <= previous["time_s"]
        ):
            raise ValueError("Trace frame and time must increase strictly")
        new_phase_number = TRACE_PHASES.index(phase)
        if new_phase_number < phase_number:
            raise ValueError("Trace phases must follow unloaded, loaded, recovered order")
        phase_number = new_phase_number
        if previous is not None and phase == previous_phase:
            if values["frame"] != previous["frame"] + 1:
                flags.add("missing_frames_within_phase")
            for marker in ("fixture", "root", "load"):
                jump_m = math.hypot(
                    values[f"{marker}_x_px"] - previous[f"{marker}_x_px"],
                    values[f"{marker}_y_px"] - previous[f"{marker}_y_px"],
                ) / pixels_per_m
                if jump_m > max_jump_m:
                    flags.add("abrupt_marker_jump")
        groups[phase].append(values)
        previous, previous_phase = values, phase
    if any(len(rows) < 3 for rows in groups.values()):
        raise ValueError("At least three valid samples per unloaded, loaded, and recovered phase are required")

    relative_y = {
        phase: [(row["load_y_px"] - row["fixture_y_px"]) / pixels_per_m for row in rows]
        for phase, rows in groups.items()
    }
    level = {phase: median(values) for phase, values in relative_y.items()}
    displacement = level["loaded"] - level["unloaded"]
    residual = level["recovered"] - level["unloaded"]
    stationary_sigma = {phase: stdev(values) for phase, values in relative_y.items()}
    # Do not divide by sqrt(frame count); independent load cycles establish repeatability.
    displacement_noise = math.hypot(stationary_sigma["loaded"], stationary_sigma["unloaded"])
    residual_noise = math.hypot(stationary_sigma["recovered"], stationary_sigma["unloaded"])
    uncertainty = math.sqrt(
        max(displacement_noise, residual_noise) ** 2
        + (calibration_uncertainty_fraction * max(abs(displacement), abs(residual))) ** 2
        + (0.5 / pixels_per_m) ** 2
    )
    fixture_reference = (
        median(row["fixture_x_px"] for row in groups["unloaded"]),
        median(row["fixture_y_px"] for row in groups["unloaded"]),
    )
    fixture_motion = max(
        math.hypot(row["fixture_x_px"] - fixture_reference[0], row["fixture_y_px"] - fixture_reference[1])
        / pixels_per_m
        for rows in groups.values() for row in rows
    )
    root_level = {
        phase: median((row["root_y_px"] - row["fixture_y_px"]) / pixels_per_m for row in rows)
        for phase, rows in groups.items()
    }
    if fixture_motion > max_fixture_motion_m:
        flags.add("fixture_motion_exceeds_limit")
    if uncertainty > max_uncertainty_m:
        flags.add("noisy_trace")
    return {
        "displacement_m": displacement,
        "residual_m": residual,
        "fixture_motion_m": fixture_motion,
        "root_motion_m": root_level["loaded"] - root_level["unloaded"],
        "uncertainty_m": uncertainty,
        "quality_flags": sorted(flags),
        "phase_sample_counts": {phase: len(rows) for phase, rows in groups.items()},
        "stationary_noise_m": stationary_sigma,
        "dropped_rows": dropped_rows,
        "pixels_per_m": pixels_per_m,
        "calibration_uncertainty_fraction": calibration_uncertainty_fraction,
        "independent_load_cycles": 1,
        "independent_specimens": 1,
        "notes": [
            "Positive displacement is downwards in image coordinates.",
            "Fixture subtraction does not make a moving fixture valid.",
            "Uncertainty is a cycle estimate; use independent cycles to establish repeatability.",
            "Dwell/recovery times and load metadata must be recorded separately.",
        ],
    }


def analyze_trace_file(path: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Read an existing local marker trace. Does not modify the source file."""
    return analyze_trace(Path(path).read_text(encoding="utf-8-sig"), **kwargs)
