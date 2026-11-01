# M9: compares the agent collective with a single agent, an optimizer and random
# search under identical budgets, all starting from the reference-only calibration.
#
#   python compare.py run --prefix m9 --seeds 3     # resumable; one main.py process per run
#   python compare.py report --prefix m9            # writes runs/<prefix>-report.{md,json}
#
# Runs go one at a time (shared PreForm server; parallel FEA would distort timings),
# ordered by seed so an interrupted batch still has balanced results.
import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

import config
import selection
import store

METHODS = ["collective", "single", "optimizer", "random"]
BUDGET = {"l1": 3000, "l2_fea": 4, "preform_prep": 2, "llm_calls": 24}
ROUNDS = 3   # an upper bound; the llm_calls budget normally ends agent runs first
INPUT_USD_PER_TOKEN, OUTPUT_USD_PER_TOKEN = 4e-6, 20e-6   # Opus 5.5 list price, cache discounts ignored
HERE = Path(__file__).resolve().parent


def run_id(prefix, method, seed):
    return f"{prefix}-{method}-s{seed}"


def run(prefix, seeds):
    import main
    if not main.preform_reachable():
        sys.exit("PreForm server is not reachable; without it no method can select a design.")
    for seed in range(1, seeds + 1):
        for method in METHODS:
            rid = run_id(prefix, method, seed)
            run_dir = store.RUNS_DIR / rid
            if (run_dir / "run.json").exists():
                print(f"{rid}: done, skipping", flush=True)
                continue
            if run_dir.exists():   # interrupted earlier: keep it aside and start clean
                n = 1
                while (store.RUNS_DIR / f"{rid}-incomplete{n}").exists():
                    n += 1
                shutil.move(str(run_dir), str(store.RUNS_DIR / f"{rid}-incomplete{n}"))
            cmd = [sys.executable, "-u", str(HERE / "main.py"), "--run-id", rid, "--mode", method,
                   "--seed", str(seed), "--rounds", str(ROUNDS), "--calibration-start", "reference"]
            for kind, limit in BUDGET.items():
                cmd += [f"--budget-{kind.replace('_', '-')}", str(limit)]
            print(f"{rid}: starting", flush=True)
            with open(store.RUNS_DIR / f"{rid}.log", "w", encoding="utf-8") as log:
                code = subprocess.run(cmd, cwd=HERE, stdout=log, stderr=subprocess.STDOUT).returncode
            summary = json.loads((run_dir / "run.json").read_text()) if (run_dir / "run.json").exists() else {}
            print(f"{rid}: exit {code}, selected {summary.get('selected')}, "
                  f"{summary.get('elapsed_s')} s", flush=True)


# --- report ---------------------------------------------------------------------------

POSITION_CHANGE = re.compile(r"\b(disagree|instead|rather than|withdraw|agree with|changed? my|reconsider)\w*",
                             re.IGNORECASE)


def run_metrics(rid):
    run_dir = store.RUNS_DIR / rid
    summary = json.loads((run_dir / "run.json").read_text())
    records = store.list_records(rid)
    report = selection.select(records)
    by_id = {a.candidate_id: a for a in report.candidates}
    fea = [(rec, r) for rec in records for r in rec.structural if r.level == 2]
    qualified = [rec for rec in records
                 if by_id[rec.candidate_id].status in ("selected", "feasible", "print_not_evaluated")]
    turns = store.read_turns(rid)
    tokens_in = sum(t["input_tokens"] for t in turns)
    tokens_out = sum(t["output_tokens"] for t in turns)
    selected = by_id.get(report.selected)
    return {
        "run_id": rid, "mode": summary["mode"], "seed": summary["seed"],
        "selected_mass_g": selected.mass_g if selected else None,
        "selected_params": [selected.params.plate_thickness, selected.params.rib_height,
                            selected.params.rib_thickness] if selected else None,
        "lightest_structurally_qualified_g": min((r.mass.value for r in qualified), default=None),
        "candidates": len(records),
        "fea_runs": summary["budget_used"]["l2_fea"],
        "fea_selection_grade": sum(1 for _, r in fea
                                   if r.tip_deflection.counts_for_selection and r.max_stress.counts_for_selection),
        "preform_runs": summary["budget_used"]["preform_prep"],
        "llm_calls": summary["budget_used"]["llm_calls"],
        "elapsed_min": round(summary["elapsed_s"] / 60, 1),
        "est_cost_usd": round(tokens_in * INPUT_USD_PER_TOKEN + tokens_out * OUTPUT_USD_PER_TOKEN, 2),
        "position_changes": [{"round": m.round, "agent": m.agent, "excerpt": _excerpt(m.text)}
                             for m in store.read_messages(rid) if POSITION_CHANGE.search(m.text)],
    }


def _excerpt(text, width=220):
    match = POSITION_CHANGE.search(text)
    start = max(0, match.start() - width // 2)
    return ("..." if start else "") + " ".join(text[start:start + width].split()) + "..."


def _fmt(v, digits=2):
    return "-" if v is None else (f"{v:.{digits}f}" if isinstance(v, float) else str(v))


def report(prefix):
    runs = sorted(p.name for p in store.RUNS_DIR.glob(f"{prefix}-*")
                  if p.is_dir() and (p / "run.json").exists() and "incomplete" not in p.name)
    rows = [run_metrics(rid) for rid in runs]
    lines = [f"# M9 comparison: {prefix}", "",
             f"Identical budget per run: {BUDGET}; agent rounds <= {ROUNDS}; every run starts from the "
             "reference-only L1 calibration. Mass is the selected design's CAD mass; a run that selects "
             "nothing has no mass. Costs use list prices without cache discounts (an upper bound).", "",
             "## Per method", "",
             "| Method | Runs | Selected | Best selected (g) | Mean selected (g) | Mean FEA used | "
             "Median time (min) | Mean cost ($) |", "|---|---|---|---|---|---|---|---|"]
    summary = {}
    for method in METHODS:
        group = [r for r in rows if r["mode"] == method]
        if not group:
            continue
        masses = [r["selected_mass_g"] for r in group if r["selected_mass_g"] is not None]
        summary[method] = {"runs": len(group), "selected": len(masses),
                           "best_g": min(masses, default=None),
                           "mean_g": statistics.mean(masses) if masses else None,
                           "mean_fea": statistics.mean(r["fea_runs"] for r in group),
                           "mean_min": statistics.mean(r["elapsed_min"] for r in group),
                           "median_min": statistics.median(r["elapsed_min"] for r in group),
                           "mean_cost": statistics.mean(r["est_cost_usd"] for r in group)}
        s = summary[method]
        lines.append(f"| {method} | {s['runs']} | {s['selected']}/{s['runs']} | {_fmt(s['best_g'])} | "
                     f"{_fmt(s['mean_g'])} | {_fmt(s['mean_fea'], 1)} | {_fmt(s['median_min'], 1)} | "
                     f"{_fmt(s['mean_cost'])} |")
    lines += ["", "## Per run", "",
              "| Run | Selected (g) | Design (mm) | Lightest FEA-qualified (g) | Candidates | FEA used / "
              "selection-grade | PreForm | LLM calls | Time (min) | Cost ($) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['run_id']} | {_fmt(r['selected_mass_g'])} | {r['selected_params'] or '-'} | "
                     f"{_fmt(r['lightest_structurally_qualified_g'])} | {r['candidates']} | "
                     f"{r['fea_runs']} / {r['fea_selection_grade']} | {r['preform_runs']} | {r['llm_calls']} | "
                     f"{r['elapsed_min']} | {r['est_cost_usd']} |")
    lines += ["", "## Moments where an agent changed or challenged a position", "",
              "Keyword matches in agent messages (disagree, instead, rather than, withdraw, ...). "
              "Read them in context in runs/<run>/messages.jsonl before drawing conclusions.", ""]
    for r in rows:
        for c in r["position_changes"]:
            lines.append(f"- **{r['run_id']}**, round {c['round']}, {c['agent']}: {c['excerpt']}")
    out = store.RUNS_DIR / f"{prefix}-report"
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps({"budget": BUDGET, "summary": summary, "runs": rows},
                                                   indent=2), encoding="utf-8")
    print("\n".join(lines[:8 + len(summary)]))
    print(f"\nwrote {out.with_suffix('.md')} and .json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="M9 method comparison")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--prefix", default="m9")
    p_run.add_argument("--seeds", type=int, default=3)
    p_report = sub.add_parser("report")
    p_report.add_argument("--prefix", default="m9")
    args = parser.parse_args()
    run(args.prefix, args.seeds) if args.command == "run" else report(args.prefix)
