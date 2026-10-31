r"""Benchmark metrics, charts and summary table.

Per run: requirement failures at the end (assembly), all check failures at the end, edits proposed /
accepted / rejected, evaluations needed to pass everything (None if never), PreForm warnings on the final
design (real if final_preform.json is real, else MOCK and marked), time, cost.

  .venv\Scripts\python.exe -m benchmark.metrics results\benchmark\runs
Writes results/benchmark/<runs folder name>_summary.{md,csv} and *.png charts.
"""
import csv
import json
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from core.board import Board  # noqa: E402

MODES = ["one_shot", "generalist", "collective"]
# validated reference palette, categorical slots 1-3 (dataviz skill: pass all-pairs CVD checks)
COLORS = {"one_shot": "#2a78d6", "generalist": "#eb6834", "collective": "#1baf7a"}
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"


def run_metrics(d):
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    b = Board(d / "board.sqlite")
    rid = 1
    versions = b.versions(rid)
    edits = [v for v in versions if v["author"] != "baseline"]
    dec = {x["version_id"]: x["decision"] for x in b.decisions(rid)}
    # evaluations until the first version with every check passing
    per_version = {}
    for c in b.checks(rid):
        per_version.setdefault(c["version_id"], []).append(c["passed"])
    evals_to_pass, n = None, 0
    for vid in sorted(per_version):
        n += 1
        if all(per_version[vid]) and len(per_version[vid]) == 4:
            evals_to_pass = n
            break
    if s["mode"] == "one_shot":
        evals_to_pass = 0 if s["final_passed"] else None
    pf_file = d / "final_preform.json"
    pf_warn, pf_real = None, False
    if pf_file.exists():
        pf = json.loads(pf_file.read_text(encoding="utf-8"))
        pf_real = not any(v["mocked"] for v in pf.values())
        pf_warn = sum(len([w for w in v["warnings"] if w["type"] != "mock"]) for v in pf.values())
    return {"brief": s["brief"], "mode": s["mode"], "seed": s["seed"], "model": s["model"],
            "mocked_llm": s["mocked_llm"], "clearances": s["clearances"],
            "material": s.get("print_settings", {}).get("material"),
            "layer_mm": s.get("print_settings", {}).get("layer_thickness_mm"),
            "requirement_failures": s["final_failures"].get("assembly", 0),
            "all_failures": sum(s["final_failures"].values()), "passed": s["final_passed"],
            "edits_proposed": len(edits), "edits_accepted": sum(dec.get(v["id"]) == "accepted" for v in edits),
            "edits_rejected": sum(dec.get(v["id"]) == "rejected" for v in edits),
            "evaluations_used": s["evaluations_used"], "evals_to_pass": evals_to_pass,
            "preform_warnings": pf_warn, "preform_real": pf_real,
            "seconds": s["seconds"], "cost_usd": s["usage"]["cost_usd"]}


def collect(root):
    rows = []
    for d in sorted(Path(root).iterdir()):
        if (d / "summary.json").exists():
            rows.append(run_metrics(d))
    return rows


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 2) if xs else None


def aggregate(rows):
    out = []
    briefs = sorted({r["brief"] for r in rows})
    for b in briefs:
        for m in MODES:
            rs = [r for r in rows if r["brief"] == b and r["mode"] == m]
            if not rs:
                continue
            out.append({"brief": b, "mode": m, "runs": len(rs),
                        "passed": f"{sum(r['passed'] for r in rs)}/{len(rs)}",
                        "requirement_failures": _mean([r["requirement_failures"] for r in rs]),
                        "all_failures": _mean([r["all_failures"] for r in rs]),
                        "all_failures_min": min(r["all_failures"] for r in rs),
                        "all_failures_max": max(r["all_failures"] for r in rs),
                        "edits_proposed": _mean([r["edits_proposed"] for r in rs]),
                        "edits_accepted": _mean([r["edits_accepted"] for r in rs]),
                        "evaluations_used": _mean([r["evaluations_used"] for r in rs]),
                        "evals_to_pass": _mean([r["evals_to_pass"] for r in rs]),
                        "preform_warnings": _mean([r["preform_warnings"] for r in rs]),
                        "preform_real": all(r["preform_real"] for r in rs),
                        "seconds": _mean([r["seconds"] for r in rs]),
                        "cost_usd": round(sum(r["cost_usd"] for r in rs) / len(rs), 4)})
    return out


def _style(ax, title, ylabel):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=12, pad=12)
    ax.set_ylabel(ylabel, color=INK2)
    ax.tick_params(colors=INK2, length=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def chart(agg, key, title, ylabel, path, err=None, na_note=False):
    briefs = sorted({a["brief"] for a in agg})
    fig, ax = plt.subplots(figsize=(9, 4.6), facecolor=SURFACE)
    w = 0.26
    for i, m in enumerate(MODES):
        xs, ys, lo, hi = [], [], [], []
        for j, b in enumerate(briefs):
            a = next((x for x in agg if x["brief"] == b and x["mode"] == m), None)
            if a is None:
                continue
            v = a[key]
            x = j + (i - 1) * w
            if v is None:
                if na_note:
                    ax.text(x, 0.05, "never\npassed", ha="center", va="bottom", fontsize=7, color=INK2)
                continue
            xs.append(x)
            ys.append(v)
            if err:
                lo.append(v - a[err[0]])
                hi.append(a[err[1]] - v)
        if xs:
            ax.bar(xs, ys, width=w, color=COLORS[m], label=m.replace("_", "-"), edgecolor=SURFACE, linewidth=2)
            if err:
                ax.errorbar(xs, ys, yerr=[lo, hi], fmt="none", ecolor=INK2, elinewidth=1, capsize=3)
    ax.set_xticks(range(len(briefs)))
    ax.set_xticklabels([b.replace("_", " ") for b in briefs], color=INK)
    _style(ax, title, ylabel)
    leg = ax.legend(frameon=False, ncol=3, loc="upper right", bbox_to_anchor=(1, 1.12))
    for t in leg.get_texts():
        t.set_color(INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def report(root):
    root = Path(root)
    rows = collect(root)
    if not rows:
        print("no runs found in", root)
        return
    agg = aggregate(rows)
    out = root.parent
    tag = root.name
    with open(out / f"{tag}_per_run.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    mocked = any(r["mocked_llm"] for r in rows)
    pf_real = all(a["preform_real"] for a in agg)
    lines = [f"# Benchmark summary ({tag})", "",
             f"- model: `{rows[0]['model']}`" + (" **MOCK (rule-based fake model, no API calls)**" if mocked else ""),
             f"- print: {rows[0]['material']}, {rows[0]['layer_mm']} mm layers; clearances {rows[0]['clearances']}",
             "- PreForm inside the loop: **MOCK** (time budget). PreForm warnings column: final design, "
             + ("real PreFormServer" if pf_real else "**MOCK or partly MOCK**"),
             f"- {len(rows)} runs. Means over seeds; failures are counted by our own checks (claims are limited to them).",
             "", "| brief | mode | passed | req. failures | all failures (min-max) | edits prop./acc. | evals used | "
             "evals to pass | PreForm warn | time s | cost $ |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in agg:
        lines.append(f"| {a['brief']} | {a['mode']} | {a['passed']} | {a['requirement_failures']} | {a['all_failures']} "
                     f"({a['all_failures_min']}-{a['all_failures_max']}) | {a['edits_proposed']}/{a['edits_accepted']} | "
                     f"{a['evaluations_used']} | {a['evals_to_pass'] if a['evals_to_pass'] is not None else 'never'} | "
                     f"{a['preform_warnings']} | {a['seconds']} | {a['cost_usd']} |")
    (out / f"{tag}_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sub = " (MOCK model)" if mocked else ""
    chart(agg, "all_failures", f"Check failures left at the end{sub}", "failures (mean, min-max)",
          out / f"{tag}_final_failures.png", err=("all_failures_min", "all_failures_max"))
    chart(agg, "evals_to_pass", f"Evaluations needed to pass every check{sub}", "evaluations (mean of passing runs)",
          out / f"{tag}_evals_to_pass.png", na_note=True)
    chart(agg, "cost_usd", f"Model cost per run{sub}", "USD (mean)", out / f"{tag}_cost.png")
    print("\n".join(lines))
    print("charts:", out)


if __name__ == "__main__":
    report(sys.argv[1] if len(sys.argv) > 1 else "results/benchmark/runs")
