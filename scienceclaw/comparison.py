"""Reproducible, explicitly simulated policy comparisons and evidence exports."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from .domain import Geometry, Protocol, utc_now
from .service import ScienceClaw


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _initial_signature(state: dict) -> str:
    fields = ("geometry", "force_n", "load_position_m", "displacement_m", "uncertainty_m",
              "residual_m", "fixture_motion_m", "dwell_s", "recovery_s", "source", "quality_flags")
    values = [{key: obs[key] for key in fields} for obs in state["observations"] if obs["purpose"] == "initial"]
    if len(values) != 4:
        raise ValueError("Comparison requires exactly four shared initial observations per method")
    return hashlib.sha256(_json_bytes(values)).hexdigest()


def summarize_run(state: dict, *, elapsed_s: float | None = None) -> dict:
    """Report evidence-based metrics; volume is geometric, never measured mass."""
    run = state["run"]
    nomination = state["nominations"][0] if state["nominations"] else None
    candidate = next((c for c in state["candidates"] if nomination and c["id"] == nomination["candidate_id"]), None)
    reference = next(c for c in state["candidates"] if c["reference"])
    errors = [event["payload"] for event in state["events"] if event["type"] == "reserved_prediction_scored"]
    acceptance = state["acceptance"]
    numerical_evaluations = sum(event["payload"].get("evaluations", 0) for event in state["events"] if event["type"] == "numerical_search")
    calls = sum(decision.get("usage", {}).get("calls", 0) for decision in state["decisions"])
    actual_tokens = sum(decision.get("usage", {}).get("input_tokens", 0) + decision.get("usage", {}).get("output_tokens", 0) for decision in state["decisions"])
    count = acceptance["independent_specimens_tested"]
    policies = {d.get("policy", "unknown") for d in state["decisions"]}
    if run["method"] == "adaptive":
        policy = "adaptive_numerical"
    elif run["method"] in ("single_agent", "independent"):
        policy = "+".join(sorted(policies)) or "no_decisions"
    elif {"three_provider_collective", "rule_based_specialist_demo"} <= policies:
        policy = "mixed_live_and_offline_specialists"
    elif "three_provider_collective" in policies:
        policy = "live_specialist_collective" if calls else "live_specialists_not_executed"
    else:
        policy = "offline_rule_based_specialists"
    # Experimental efficiency: search/reserved cycles spent until a new design first passed.
    assessed = {o["experiment_id"]: o for o in state["observations"]}
    reference_ids = {c["id"] for c in state["candidates"] if c["reference"]}
    spent, first_pass = 0, None
    for experiment in state["experiments"]:
        if experiment["purpose"] in ("search", "reserved"):
            spent += 1
        observation = assessed.get(experiment["id"])
        if (experiment["purpose"] == "search" and experiment["candidate_id"] not in reference_ids and observation
                and observation["assessment"]["status"] in ("simulated_pass", "physical_pass")):
            first_pass = spent
            break
    return {
        "run_id": run["id"],
        "scenario": (run.get("simulation_scenario") or {}).get("name"),
        "search_cycles_to_first_pass": first_pass,
        "seed": run["seed"],
        "mode": run["mode"],
        "method": run["method"],
        "policy": policy,
        "decision_policies": sorted(policies),
        "candidate_id": candidate["id"] if candidate else None,
        "material_metric": "nominal_geometry_volume_m3",
        "nominated_volume_m3": candidate["volume_m3"] if candidate else None,
        "reference_volume_m3": reference["volume_m3"],
        "volume_ratio_to_reference": candidate["volume_m3"] / reference["volume_m3"] if candidate else None,
        "measured_mass_kg": None,
        "acceptance_status": acceptance["status"],
        "passing_specimens": acceptance["passing_specimens"],
        "independent_specimens_tested": count,
        "passing_fraction": acceptance["passing_specimens"] / count if count else None,
        "acceptance_observations": [
            {"id": o["id"], "specimen_id": o["specimen_id"], "displacement_m": o["displacement_m"],
             "uncertainty_m": o["uncertainty_m"], "status": o["assessment"]["status"]}
            for o in state["observations"] if o["purpose"] == "acceptance"
        ],
        "reserved_predictions": errors,
        "reserved_mean_absolute_error_m": mean(e["absolute_error_m"] for e in errors) if errors else None,
        "budget": state["budget"],
        "numerical_evaluations": numerical_evaluations,
        "reported_tokens": actual_tokens,
        "charged_or_reserved_tokens": state["budget"]["tokens_used"],
        "model_calls": calls,
        "discussion_rounds": sum(d.get("rounds", 0) for d in state["decisions"]),
        "unresolved_decisions": sum(not d.get("selected") for d in state["decisions"]) + sum(e["type"] == "execution_rejected" for e in state["events"]),
        "human_fabrication_time_s": sum(f.get("duration_s", 0) for f in state["fabrications"] if f.get("source") == "physical"),
        "elapsed_s": elapsed_s,
        "event_chain_verified": state["log_verified"],
    }


def export_run(service: ScienceClaw, run_id: str, output_dir: str | Path) -> Path:
    """Export one run and its referenced evidence, refusing overwritten archives.

    No environment variables, credentials, unrelated run records or database
    files are included. Hashes describe the exported bytes, not authenticity.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("Invalid run identifier")
    with service.store.lock:
        state = service.state(run_id)
        rows = service.store.db.execute(
            "SELECT seq,id,kind,payload FROM records WHERE run_id=? ORDER BY seq", (run_id,)
        ).fetchall()
        records = [{"seq": row["seq"], "id": row["id"], "kind": row["kind"],
                    "payload": json.loads(row["payload"])} for row in rows]
        payloads = {
            "state.json": _json_bytes(state),
            "records.json": _json_bytes(records),
            "events.json": _json_bytes(state["events"]),
            "metrics.json": _json_bytes(summarize_run(state)),
        }
        artifacts = {}
        for candidate in state["candidates"]:
            for artifact in candidate["artifacts"].values():
                artifacts[artifact["filename"]] = artifact["sha256"]
        for obs in state["observations"]:
            if obs.get("raw_artifact"):
                name = obs["raw_artifact"]
                artifacts[name] = name.split(".")[0]
        for name, expected_hash in sorted(artifacts.items()):
            if not re.fullmatch(r"[0-9a-f]{64}\.[A-Za-z0-9]+", name):
                raise ValueError("Malformed evidence artifact name")
            path = (service.store.artifacts / name).resolve()
            if path.parent != service.store.artifacts.resolve():
                raise ValueError("Artifact path escapes evidence directory")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected_hash:
                raise ValueError(f"Artifact hash mismatch: {name}")
            payloads[f"artifacts/{name}"] = raw
        for record in state.get("print_assets", []) + state.get("print_projects", []):
            from .formlabs import Manufacturing
            path = Manufacturing(service.store)._file(record["filename"], record["sha256"])
            payloads[f"formlabs/{path.name}"] = path.read_bytes()
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "exported_at": utc_now(),
            "mode": state["run"]["mode"],
            "source_event_chain_verified": state["log_verified"],
            "event_chain_scope": "events.json includes only this run; original previous_hash links may refer to other runs in the source store",
            "notes": [
                "Simulated observations are not physical validation.",
                "File hashes verify exported bytes; they do not prove measurement authenticity.",
                "The manifest lists every ZIP member except manifest.json itself.",
            ],
            "files": [{"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                      for name, raw in sorted(payloads.items())],
        }
        payloads["manifest.json"] = _json_bytes(manifest)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{run_id}.zip"
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(payloads.items()):
            archive.writestr(name, raw)
    return destination


def _aggregate(results: list[dict]) -> dict:
    """Per-method summary; material is compared only among runs whose final copies all passed."""
    summary = {}
    for method in dict.fromkeys(r["method"] for r in results):
        rows = [r for r in results if r["method"] == method]
        passed = [r for r in rows if r["passing_fraction"] == 1]
        first = [r["search_cycles_to_first_pass"] for r in rows if r["search_cycles_to_first_pass"] is not None]
        average = lambda values: mean(values) if values else None  # noqa: E731
        summary[method] = {
            "runs": len(rows), "all_copies_passed_runs": len(passed),
            "mean_passing_fraction": average([r["passing_fraction"] or 0 for r in rows]),
            "mean_volume_ratio_when_passing": average([r["volume_ratio_to_reference"] for r in passed]),
            "runs_reaching_a_passing_new_design": len(first), "mean_cycles_to_first_pass": average(first),
            "mean_reserved_error_m": average([r["reserved_mean_absolute_error_m"] for r in rows if r["reserved_mean_absolute_error_m"] is not None]),
            "total_model_calls": sum(r["model_calls"] for r in rows), "total_reported_tokens": sum(r["reported_tokens"] for r in rows),
            "unresolved_decisions": sum(r["unresolved_decisions"] for r in rows),
        }
    return summary


def run_comparison(
    seeds: Iterable[int], output_dir: str | Path, *,
    protocol: Protocol | None = None, geometry: Geometry | None = None,
    methods: Iterable[str] = ("adaptive", "collective"), live: bool = False, scenarios=None,
) -> dict:
    """Run a paired simulation with identical initial evidence and frozen limits.

    Every method sees the same seeds, scenarios, shared initial cycles, protocol and
    budgets. live=True makes real model calls for the agent methods.
    """
    from .domain import SimulationScenario
    from .service import METHODS
    methods = list(methods)
    if not methods or any(m not in METHODS for m in methods) or len(set(methods)) != len(methods):
        raise ValueError(f"Methods must be distinct values from {', '.join(METHODS)}")
    scenarios = list(scenarios or [SimulationScenario()])
    seed_values = list(seeds)
    if not seed_values or any(type(seed) is not int or seed < 0 for seed in seed_values):
        raise ValueError("Provide at least one nonnegative integer seed")
    if len(set(seed_values)) != len(seed_values):
        raise ValueError("Repeated seeds are not independent evaluation tasks")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "comparison.json"
    csv_path = output / "comparison.csv"
    if report_path.exists() or csv_path.exists():
        raise FileExistsError("Comparison report already exists; choose a fresh output directory")
    protocol, geometry = protocol or Protocol(), geometry or Geometry()
    service = ScienceClaw(output / "data")
    results, pairs = [], []
    progress = output / "results.jsonl"  # Survives an interrupted live comparison.
    try:
        for scenario in scenarios:
            for seed in seed_values:
                signatures = []
                for method in methods:
                    started = time.monotonic()
                    scenario_protocol = Protocol(**{**protocol.model_dump(), **scenario.protocol_overrides})
                    state = service.create_run(name=f"Paired simulated {method}, {scenario.name}, seed {seed}", mode="simulated",
                                               method=method, seed=seed, protocol=scenario_protocol, geometry=geometry, scenario=scenario)
                    signatures.append(_initial_signature(state))
                    state = service.demo(state["run"]["id"], live=live and method != "adaptive")
                    metrics = summarize_run(state, elapsed_s=time.monotonic() - started)
                    metrics["evidence_archive"] = str(export_run(service, state["run"]["id"], output / "evidence").relative_to(output))
                    results.append(metrics)
                    with progress.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(metrics, sort_keys=True, allow_nan=False) + "\n")
                if len(set(signatures)) != 1:
                    raise RuntimeError("Methods did not receive identical shared initial evidence")
                pairs.append({"seed": seed, "scenario": scenario.name, "shared_initial_sha256": signatures[0], "shared_initial_cycles": 4})
    finally:
        service.store.close()
    report = {
        "schema_version": 1,
        "created_at": utc_now(),
        "mode": "simulated",
        "comparison": " vs ".join(methods) + (" (live model agents)" if live else " (offline rule-based agents)"),
        "methods": methods, "live_agents": live, "scenarios": [s.model_dump() for s in scenarios],
        "summary": _aggregate(results),
        "limits": [
            ("Agent methods made real model calls; models, prompts and budgets are recorded per decision." if live else
             "No API calls or real LLM decisions occur in this comparison."),
            ("A few seeds per scenario support a case study, not a statistically reliable average advantage." if live else
             "This checks software policies and accounting; it cannot establish an advantage from LLM coordination."),
            "Simulation is not fabrication or physical validation; volume is nominal geometry, not measured mass.",
            "Simulator material and fixture parameters are fixed; seeds vary measurement noise and final-copy variation.",
            "Final reference copies are not independently tested here; volume ratios alone are not performance improvements.",
        ],
        "protocol": protocol.model_dump(mode="json"),
        "reference_geometry": geometry.model_dump(mode="json"),
        "seeds": seed_values,
        "pairs": pairs,
        "results": results,
    }
    report_path.write_bytes(_json_bytes(report))
    fields = ("scenario", "seed", "method", "policy", "run_id", "acceptance_status", "passing_specimens", "search_cycles_to_first_pass",
              "independent_specimens_tested", "nominated_volume_m3", "volume_ratio_to_reference",
              "reserved_mean_absolute_error_m", "search_cycles_used", "designs_used",
              "numerical_evaluations", "reported_tokens", "model_calls", "elapsed_s")
    with csv_path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for result in results:
            row = {key: result.get(key) for key in fields}
            row["search_cycles_used"] = result["budget"]["cycles_used"]
            row["designs_used"] = result["budget"]["designs_used"]
            writer.writerow(row)
    return report
