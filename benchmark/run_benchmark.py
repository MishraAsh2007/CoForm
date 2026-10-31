r"""Every brief x every mode x N seeds, same model, tools, brief and budget per cell.

  .venv\Scripts\python.exe -m benchmark.run_benchmark            # real model (needs ANTHROPIC_API_KEY)
  .venv\Scripts\python.exe -m benchmark.run_benchmark --mock     # rule-based fake model, zero cost

To keep the run time bounded, PreForm inside the loop is the labelled MOCK (both modes are affected the
same way). Afterwards each run's FINAL base/lid goes through the real PreFormServer once (if it is up),
saved as final_preform.json in the run folder. Then benchmark/metrics.py writes charts and tables.
"""
import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from cad.common import ROOT, load_config

MODES = ["one_shot", "generalist", "collective"]


def _one(args):
    mode, brief, seed, mock, budget, clearances, out_root = args
    import run as runner
    try:
        return runner.run(mode, brief, budget, seed, mock, clearances, out_root=out_root, mock_preform=True,
                          quiet=True)
    except Exception as e:  # one broken run must not stop the benchmark
        return {"run": f"{Path(brief).stem}__{mode}__s{seed}", "error": repr(e)}


def final_preform(out_root, cfg):
    """Real PreForm on every distinct final design (deduplicated by file hash)."""
    from checks.preform_check import check
    seen = {}
    for d in sorted(Path(out_root).iterdir()):
        if not (d / "base.stl").exists():
            continue
        res = {}
        for part in ("base", "lid"):
            p = d / f"{part}.stl"
            h = hashlib.sha1(p.read_bytes()).hexdigest()
            if h not in seen:
                r = check(p, cfg)
                seen[h] = {k: r.get(k) for k in ("mocked", "mock_reason", "print_time_s", "volume_ml", "warnings",
                                                 "passed")}
            res[part] = seen[h]
        (d / "final_preform.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"  final PreForm {d.name}: " + ", ".join(
            f"{k} {'MOCK' if v['mocked'] else 'real'} {len([w for w in v['warnings'] if w['type'] != 'mock'])} warn"
            for k, v in res.items()), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--budget", type=int, default=None)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--clearances", default="generic")
    ap.add_argument("--briefs", nargs="*", default=None)
    ap.add_argument("--skip-final-preform", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    briefs = a.briefs or sorted(str(p) for p in (ROOT / "benchmark" / "briefs").glob("*.yaml"))
    out_root = ROOT / "results" / "benchmark" / ("runs_mock" if a.mock else "runs")
    out_root.mkdir(parents=True, exist_ok=True)
    jobs = [(m, b, s, a.mock, a.budget, a.clearances, str(out_root)) for b in briefs for m in MODES
            for s in range(a.seeds)]
    print(f"{len(jobs)} runs, model {'MOCK' if a.mock else cfg['llm']['model']}, PreForm in loop: MOCK", flush=True)
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(_one, j) for j in jobs]
        for f in as_completed(futs):
            r = f.result()
            if "error" in r:
                print(f"  ERROR {r['run']}: {r['error']}", flush=True)
            else:
                print(f"  {r['run']}: failures {sum(r['final_failures'].values())}, evals {r['evaluations_used']}, "
                      f"${r['usage']['cost_usd']:.4f}, {r['seconds']} s", flush=True)
    if not a.skip_final_preform:
        final_preform(out_root, cfg)
    from benchmark.metrics import report
    report(out_root)


if __name__ == "__main__":
    main()
