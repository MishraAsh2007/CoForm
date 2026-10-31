"""Command-line entry point. Offline commands never make model API calls."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .comparison import export_run, run_comparison, summarize_run
from .service import ScienceClaw


def _seed(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Seed must be a nonnegative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("Seed must be a nonnegative integer")
    return parsed


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="scienceclaw", description="Evidence-led design of printable supports")
    commands = root.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Open the local review interface and API")
    serve.add_argument("--data-dir", default="data")
    serve.add_argument("--port", type=int, default=8000)
    demo = commands.add_parser("demo", help="Run an explicitly simulated, offline experiment")
    demo.add_argument("--method", choices=("adaptive", "collective"), default="collective")
    demo.add_argument("--seed", type=_seed, default=7)
    demo.add_argument("--data-dir", default="data")
    compare = commands.add_parser("compare", help="Compare decision methods on identical simulated tasks and budgets")
    compare.add_argument("--seeds", nargs="+", type=_seed, default=[7])
    compare.add_argument("--output", default="comparison-output")
    compare.add_argument("--methods", nargs="+", default=["adaptive", "collective"],
                         choices=["adaptive", "collective", "single_agent", "independent"])
    compare.add_argument("--scenarios", nargs="+", default=None, help="Named synthetic scenarios (see validation.SCENARIOS)")
    compare.add_argument("--live", action="store_true", help="Agent methods call the configured models (uses API credits)")
    compare.add_argument("--token-budget", type=int, default=None, help="Aggregate model tokens per study, equal for every method")
    validate = commands.add_parser("validate", help="Offline multi-prototype robustness matrix; no API calls")
    validate.add_argument("--output", default="data/robustness")
    smoke = commands.add_parser("live-smoke", help="One paid three-provider round, bounded by persistent .env limits")
    smoke.add_argument("--output", default="data/live-smoke")
    smoke.add_argument("--preflight-only", action="store_true", help="Only read model metadata; no generation")
    export = commands.add_parser("export", help="Export a run's records, artifacts and hash manifest as ZIP")
    export.add_argument("run_id")
    export.add_argument("--data-dir", default="data")
    export.add_argument("--output", default="exports")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "serve":
            if not 1 <= args.port <= 65535:
                raise ValueError("Port must be between 1 and 65535")
            os.environ["SCIENCECLAW_DATA_DIR"] = args.data_dir
            import uvicorn
            uvicorn.run("scienceclaw.app:create_app", factory=True, host="127.0.0.1", port=args.port)
        elif args.command == "compare":
            from dotenv import load_dotenv
            load_dotenv(override=False)
            scenarios = None
            if args.scenarios:
                from .validation import RIG_SCENARIOS, SCENARIOS
                named = {s.name: s for s in (*SCENARIOS, *RIG_SCENARIOS)}
                unknown = [n for n in args.scenarios if n not in named]
                if unknown:
                    raise ValueError(f"Unknown scenarios {unknown}; choose from {sorted(named)}")
                scenarios = [named[n] for n in args.scenarios]
            from .domain import Protocol
            protocol = Protocol(token_budget=args.token_budget) if args.token_budget else None
            report = run_comparison(args.seeds, args.output, methods=args.methods, live=args.live,
                                    scenarios=scenarios, protocol=protocol)
            print(json.dumps({"output": args.output, "comparison": report["comparison"], "runs": len(report["results"]),
                              "summary": report["summary"]}, indent=2))
        elif args.command == "validate":
            from .validation import robustness_matrix
            report = robustness_matrix(args.output)
            print(json.dumps({k:report[k] for k in ("runs","api_calls","acceptance_status_counts","fit_status_counts","all_protocol_invariants_passed")},indent=2))
        elif args.command == "live-smoke":
            from .validation import live_smoke
            report = live_smoke(args.output, preflight_only=args.preflight_only)
            print(json.dumps({"status":report["status"],"preflight":report["preflight"],"calls":report["budget"]["calls"],"estimated_or_reserved_usd":report["budget"]["estimated_or_reserved_usd"]},indent=2))
        else:
            service = ScienceClaw(args.data_dir)
            try:
                if args.command == "demo":
                    started = time.monotonic()
                    state = service.create_run(mode="simulated", method=args.method, seed=args.seed)
                    state = service.demo(state["run"]["id"])
                    print(json.dumps(summarize_run(state, elapsed_s=time.monotonic() - started), indent=2))
                elif args.command == "export":
                    print(export_run(service, args.run_id, args.output))
            finally:
                service.store.close()
    except (ValueError, KeyError, FileNotFoundError, FileExistsError) as exc:
        print(f"scienceclaw: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
