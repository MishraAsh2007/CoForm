"""Offline robustness matrix and an explicit, tightly bounded live smoke test."""
from pathlib import Path
from collections import Counter
import json
import os
import time

import httpx

from .comparison import summarize_run, export_run
from .domain import Geometry, Protocol, SimulationScenario, utc_now
from .service import ScienceClaw


SCENARIOS = (
    SimulationScenario(name="nominal"),
    SimulationScenario(name="soft_material_rigid_mount", youngs_modulus_pa=0.5e9, rotational_compliance_rad_per_nm=0.03, noise_std_m=0.00001),
    SimulationScenario(name="stiff_material_loose_mount", youngs_modulus_pa=3e9, rotational_compliance_rad_per_nm=0.45, noise_std_m=0.00003),
    SimulationScenario(name="fixture_translation", fixture_motion_m=0.0005),
    SimulationScenario(name="permanent_set", residual_m=0.0005),
    SimulationScenario(name="nonlinear_response", nonlinear_force_coefficient=4, noise_std_m=0.000002),
)
# Virtual test rig: no hardcoded stiffness or mount compliance. Resin stiffness is drawn from
# its datasheet range per study; clamp looseness comes from the jaw material. Limits are
# chosen so the lightest passing design depends on the (hidden) cure state.
# The project prints in Formlabs Alpha Fast Resin only. Noise is a dial indicator (~5 um);
# at 0.3 N the 60 mm station bends ~20 um, so the model can be calibrated. The 0.25 mm limit
# puts the lightest passing design at 73-82% of the starting resin, depending on cure state.
_ALPHA_FAST = dict(physics="fea", material="alpha_fast", noise_std_m=0.000005,
                   protocol_overrides={"max_deflection_m": 0.00025, "max_uncertainty_m": 0.00002})
RIG_SCENARIOS = (
    SimulationScenario(name="rig_alpha_fast_printed_jaws", fixture="printed_jaws", **_ALPHA_FAST),
    SimulationScenario(name="rig_alpha_fast_rubber_clamp", fixture="rubber_lined", **_ALPHA_FAST),
    SimulationScenario(name="rig_alpha_fast_steel_vise", fixture="rigid_vise", **_ALPHA_FAST),
)
FAMILIES = ("u_channel", "solid_bar", "rectangular_tube")


def robustness_matrix(output_dir="data/robustness", seeds=(7, 11)):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.json"
    if report_path.exists():
        raise FileExistsError("Report exists; choose a new output directory")
    service = ScienceClaw(output / "runs")
    results = []
    try:
        for family in FAMILIES:
            for scenario in SCENARIOS:
                for seed in seeds:
                    for method in ("adaptive", "collective"):
                        started = time.monotonic()
                        initial = service.create_run(name=f"{family} / {scenario.name} / {method} / {seed}",
                            method=method, seed=seed, geometry=Geometry(section_family=family), scenario=scenario)
                        final = service.demo(initial["run"]["id"])
                        metrics = summarize_run(final, elapsed_s=time.monotonic()-started)
                        metrics.update(section_family=family, scenario=scenario.name, final_fit_status=final["model"]["fit"]["status"],
                            fit_warnings=final["model"]["fit"].get("warnings", []),
                            protocol_invariants_passed=final["log_verified"] and final["budget"]["cycles_used"] <= 8
                                and final["budget"]["designs_used"] <= 3
                                and all(o["assessment"]["status"] != "physical_pass" for o in final["observations"]))
                        results.append(metrics)
        report = {"created_at": utc_now(), "mode": "offline_simulation", "api_calls": 0,
            "scope": "3 prismatic support families; synthetic material, fixture and measurement conditions; rule-based collective versus adaptive numerical baseline",
            "limitations": ["No printed specimens or real measurements", "No LLM reasoning tested by this offline matrix", "Two noise seeds per condition are not evidence of population-level superiority", "Simple beam model, not arbitrary objects or FEA"],
            "scenarios": [s.model_dump() for s in SCENARIOS], "seeds": list(seeds), "runs": len(results),
            "acceptance_status_counts": dict(Counter(r["acceptance_status"] for r in results)),
            "fit_status_counts": dict(Counter(r["final_fit_status"] for r in results)),
            "all_protocol_invariants_passed": all(r["protocol_invariants_passed"] for r in results), "results": results}
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        lines = ["# Offline robustness results", "", f"{len(results)} simulated studies; no API calls.", "",
            "These are software stress tests, not evidence that a printed prototype works or that LLMs cooperate well.", "",
            "| Family | Scenario | Method | Seed | Fit | Final simulated acceptance |", "|---|---|---|---:|---|---|"]
        lines += [f'| {r["section_family"]} | {r["scenario"]} | {r["method"]} | {r["seed"]} | {r["final_fit_status"]} | {r["acceptance_status"]} |' for r in results]
        (output / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
        return report
    finally:
        service.store.close()


def preflight_providers(client=None):
    """Read-only model lookup, no generation. Never return headers or response bodies."""
    config = (
        ("openai", "https://api.openai.com/v1/models/", "OPENAI_API_KEY", "OPENAI_MODEL"),
        ("anthropic", "https://api.anthropic.com/v1/models/", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"),
        ("gemini", "https://generativelanguage.googleapis.com/v1beta/models/", "GEMINI_API_KEY", "GEMINI_MODEL"),
    )
    results = []
    owned = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        for name, endpoint, key_env, model_env in config:
            key, model = os.getenv(key_env, ""), os.getenv(model_env, "")
            from .providers import JSONProvider
            try:
                JSONProvider(name, model, key)  # Validate identifier before constructing URL.
            except ValueError:
                results.append({"provider":name,"model":model,"status":"not_configured","ready":False})
                continue
            headers = {"x-goog-api-key":key} if name == "gemini" else {"Authorization":"Bearer "+key}
            if name == "anthropic":
                headers["anthropic-version"] = "2026-10-21"
                if os.getenv("ANTHROPIC_WORKSPACE_ID"):
                    headers["anthropic-workspace-id"] = os.environ["ANTHROPIC_WORKSPACE_ID"]
            try:
                response = client.get(endpoint+model, headers=headers)
                results.append({"provider":name,"model":model,"status":response.status_code,"ready":response.status_code==200})
            except httpx.HTTPError:
                results.append({"provider":name,"model":model,"status":"connection_failed","ready":False})
    finally:
        if owned:
            client.close()
    return results


def live_smoke(output_dir="data/live-smoke", *, preflight_only=False):
    """One real proposal/review round. Never loop or fall back to another model."""
    from dotenv import load_dotenv
    from .budget import guard_from_environment
    load_dotenv(override=False)
    guard = guard_from_environment()
    if guard is None or guard.max_usd > 0.05 or guard.max_calls > 6 or guard.max_per_provider > 2:
        raise ValueError("Smoke test requires persistent limits <= $0.05, 6 calls, 2 per provider")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / ("preflight.json" if preflight_only else "report.json")
    if report_path.exists():
        raise FileExistsError("A smoke report already exists; do not spend again automatically")
    preflight = preflight_providers()
    report = {"created_at":utc_now(), "preflight":preflight, "status":"preflight_only" if preflight_only else "preflight_failed",
              "budget":guard.summary(), "scope":"One live mixed-provider round on simulated evidence; not a capability benchmark"}
    if not preflight_only and all(p["ready"] for p in preflight):
        service = ScienceClaw(output / "run")
        try:
            state = service.create_run(name="Low-cost live collective smoke test", protocol=Protocol(max_rounds=1,token_budget=18000))
            result = service.step(state["run"]["id"], live=True)
            report.update(status=result["status"], run_id=state["run"]["id"], decision=result.get("decision", result))
            report["evidence_archive"] = str(export_run(service,state["run"]["id"],output/"evidence").relative_to(output))
        finally:
            service.store.close()
    report["budget"] = guard.summary()
    report_path.write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    return report
