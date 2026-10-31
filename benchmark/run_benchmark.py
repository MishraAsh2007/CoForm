r"""Benchmark: briefs x modes x seeds, same model, tools, brief, budget and naive starting spec in every cell.

  .venv\Scripts\python.exe -m benchmark.run_benchmark --briefs a b --seeds 3 --budget 12 --model claude-haiku-5-5 --resume
  .venv\Scripts\python.exe -m benchmark.run_benchmark --mock ...     # rule-based fake model, zero cost

Local checks on every version; real PreForm only when the Manufacturing agent / generalist asks and on every
final design (cached by spec, one PreForm job at a time across workers). Then benchmark/metrics.py makes the
table and charts from the logged data.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import yaml

from cad.common import ROOT, load_config

MODES = ["one_shot", "generalist", "collective"]


def _one(args):
    mode, brief, seed, mock, budget, clearances, out_root, model = args
    import run as runner
    try:
        return runner.run(mode, brief, budget, seed, mock, clearances, out_root=out_root, quiet=True, model=model)
    except Exception as e:  # one broken run must not stop the benchmark
        return {"run": f"{Path(brief).stem}__{mode}__s{seed}", "error": repr(e)}


def run_name(brief_path, mode, clearances, seed, mock, model):
    name = yaml.safe_load(Path(brief_path).read_text(encoding="utf-8"))["name"]
    return f"{name}__{mode}__{clearances}__s{seed}{'__mock' if mock else ''}{'__' + model if model else ''}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--budget", type=int, default=12)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--clearances", default="generic")
    ap.add_argument("--briefs", nargs="*", default=["a", "b"], help="brief file stems or their first letters")
    ap.add_argument("--modes", nargs="*", default=MODES)
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default="runs_v2")
    ap.add_argument("--resume", action="store_true", help="skip runs that already have a summary.json")
    a = ap.parse_args()
    cfg = load_config()
    all_briefs = sorted((ROOT / "benchmark" / "briefs").glob("*.yaml"))
    briefs = [str(p) for p in all_briefs if any(p.stem == b or p.stem.startswith(b + "_") for b in a.briefs)]
    out_root = ROOT / "results" / "benchmark" / (a.out + ("_mock" if a.mock else ""))
    out_root.mkdir(parents=True, exist_ok=True)
    jobs = [(m, b, s, a.mock, a.budget, a.clearances, str(out_root), a.model) for b in briefs for m in a.modes
            for s in range(a.seeds)]
    if a.resume:
        jobs = [j for j in jobs if not (out_root / run_name(j[1], j[0], a.clearances, j[2], a.mock, a.model)
                                        / "summary.json").exists()]
    print(f"{len(jobs)} runs to do, model {'MOCK' if a.mock else (a.model or cfg['llm']['model'])}", flush=True)
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(_one, j) for j in jobs]
        for f in as_completed(futs):
            r = f.result()
            if "error" in r:
                print(f"  ERROR {r['run']}: {r['error']}", flush=True)
            else:
                print(f"  {r['run']}: passed={r['final_passed']} quality={r['quality']['score']} "
                      f"failures {sum(r['final_failures'].values())}, evals {r['evaluations_used']}, "
                      f"${r['usage']['cost_usd']:.4f}, {r['seconds']} s", flush=True)
    from benchmark.metrics import report
    report(out_root)


if __name__ == "__main__":
    main()
