# Runs one complete design-and-evaluation cycle.
#
#   python main.py --run-id demo --mode collective --rounds 3
#   python main.py --run-id base1 --mode optimizer          (no LLM)
#
# Every mode shares the same Budget limits, experiment pipeline and selection
# rule, and starts from a frozen copy of the shared L1 calibration, so runs with
# identical budgets can be compared. PreForm is used if its
# server is reachable (or skipped with --no-preform); without it nothing can be
# selected, because selection requires a passing print preparation.
import argparse
import contextlib
import json
import socket
import time

import config
import store
from coordination import Budget


def preform_reachable():
    try:
        with socket.create_connection(("localhost", config.PREFORM_PORT), timeout=1):
            return True
    except OSError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bracket Collective: one design cycle")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=["collective", "single", "optimizer", "random"],
                        default="collective")
    parser.add_argument("--rounds", type=int, default=3, help="agent rounds (collective/single)")
    parser.add_argument("--seed", type=int, default=0, help="seed for baselines")
    for kind, default in config.BUDGETS.items():
        parser.add_argument(f"--budget-{kind.replace('_', '-')}", type=int, default=default,
                            dest=f"budget_{kind}")
    parser.add_argument("--no-preform", action="store_true")
    parser.add_argument("--calibration-start", choices=["all", "reference"], default="all",
                        help="starting L1 calibration: every stored FEA row, or reference designs "
                             "only (use 'reference' for method comparisons)")
    parser.add_argument("--promote-calibration", action="store_true",
                        help="after the run, merge its new FEA results into the shared L1 calibration "
                             "(leave off for method comparisons)")
    args = parser.parse_args(argv)

    if store.list_records(args.run_id):
        parser.error(f"run {args.run_id!r} already has candidates; choose a new --run-id")
    budget = Budget({kind: getattr(args, f"budget_{kind}") for kind in config.BUDGETS})
    # Each run starts from a frozen copy of the shared calibration and learns only from its own FEA.
    import structural_evaluator
    calibration = structural_evaluator.use_run_calibration(
        args.run_id, sources=("reference",) if args.calibration_start == "reference" else None)
    use_preform = not args.no_preform and preform_reachable()
    print(f"run {args.run_id}: mode {args.mode}, budget {budget.limits}, "
          f"PreForm {'connected' if use_preform else 'not used'}, calibration copy {calibration}")

    started = time.perf_counter()
    with contextlib.ExitStack() as stack:
        preform = None
        if use_preform:
            import preform_client
            preform = stack.enter_context(preform_client.connect())
        if args.mode in ("collective", "single"):
            from coordination import run_agents
            report = run_agents(args.run_id, budget, rounds=args.rounds, mode=args.mode,
                                preform=preform)
        else:
            import baselines
            method = baselines.optimizer_baseline if args.mode == "optimizer" else baselines.random_search
            report, _ = method(args.run_id, budget, seed=args.seed, preform=preform)
            path = store.RUNS_DIR / args.run_id / "selection.json"
            path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    print(report.summary)
    print(f"budget used: {budget.used}")
    if args.promote_calibration:
        added = structural_evaluator.promote_run_calibration(args.run_id)
        print(f"promoted {added} new FEA result(s) to the shared calibration")
    for a in report.candidates:
        print(f"  {a.candidate_id} {[a.params.plate_thickness, a.params.rib_height, a.params.rib_thickness]} "
              f"{a.mass_g and round(a.mass_g, 2)} g  {a.status}")
    summary = {"run_id": args.run_id, "mode": args.mode, "seed": args.seed, "rounds": args.rounds,
               "calibration_start": args.calibration_start, "selected": report.selected,
               "budget_limits": budget.limits, "budget_used": budget.used,
               "elapsed_s": round(time.perf_counter() - started, 1)}
    (store.RUNS_DIR / args.run_id / "run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    main()
