r"""Benchmark metrics and charts, computed by plain code from logged data only (each run's board.sqlite and
summary.json). No model writes or summarizes anything here. Each chart gets a caption, also built by code,
naming the runs and seeds it includes and whether anything was mocked.

  .venv\Scripts\python.exe -m benchmark.metrics results\benchmark\runs_v2
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
MAIN = ("assembly", "printability", "preform", "fit")
# validated reference palette, categorical slots 1-3 (dataviz skill, all-pairs CVD checks pass for 3 slots)
COLORS = {"one_shot": "#2a78d6", "generalist": "#eb6834", "collective": "#1baf7a"}
AGENT_ORDER = ["design", "assembly", "manufacturing", "fit", "generalist"]
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"


def run_data(d):
    """Everything the charts need for one run, read from its board and summary."""
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    b = Board(d / "board.sqlite")
    versions = {v["id"]: v for v in b.versions(1)}
    dec = {x["version_id"]: x["decision"] for x in b.decisions(1)}
    per = {}
    for c in b.checks(1):
        if c["name"] in MAIN:
            per.setdefault(c["version_id"], {})[c["name"]] = c
    # failures of the CURRENT (accepted) design after each evaluation
    curve, current, n, evals_to_pass = [], None, 0, None
    for vid in sorted(per):
        n += 1
        if versions[vid]["author"] == "baseline" or dec.get(vid) == "accepted":
            current = vid
        fails = sum(c["failures"] for c in per[current].values()) if current in per else None
        curve.append(fails)
        if evals_to_pass is None and current in per and all(c["passed"] for c in per[current].values()):
            evals_to_pass = n
    if s["mode"] == "one_shot":
        evals_to_pass = 0 if s["final_passed"] else None
    edits = {}
    for v in versions.values():
        if v["author"] in ("baseline",):
            continue
        e = edits.setdefault(v["author"], {"accepted": 0, "rejected": 0})
        if dec.get(v["id"]) in e:
            e[dec[v["id"]]] += 1
    pf = next((c["details"] for c in b.checks(1) if c["name"] == "preform_final"), {})
    return {"run": s["run"], "brief": s["brief"], "mode": s["mode"], "seed": s["seed"], "model": s["model"],
            "mocked_llm": s["mocked_llm"], "mocked_preform": s["mocked_preform"], "clearances": s["clearances"],
            "material": s["print_settings"]["material"], "layer_mm": s["print_settings"]["layer_thickness_mm"],
            "passed": s["final_passed"], "quality": s["quality"]["score"],
            "all_failures": sum(s["final_failures"].values()),
            "requirement_failures": s["final_failures"].get("assembly", 0),
            "evaluations_used": s["evaluations_used"], "evals_to_pass": evals_to_pass,
            "edits_proposed": sum(e["accepted"] + e["rejected"] for e in edits.values()),
            "edits_accepted": sum(e["accepted"] for e in edits.values()),
            "preform_warnings": s["final_failures"].get("preform"),
            "print_time_min": round((pf.get("metrics", {}).get("print_time_s") or 0) / 60, 1),
            "resin_ml": pf.get("metrics", {}).get("volume_ml"),
            "seconds": s["seconds"], "cost_usd": s["usage"]["cost_usd"], "curve": curve, "edits": edits}


def rank_key(r):
    """Ranking rule: a design that fails any check never outranks one that passes.
    1) passes everything, 2) fewest failures left, 3) highest quality score."""
    return (0 if r["passed"] else 1, r["all_failures"], -r["quality"])


def collect(root):
    rows = [run_data(d) for d in sorted(Path(root).iterdir()) if (d / "summary.json").exists()]
    rows.sort(key=rank_key)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def caption(rows, extra=""):
    briefs = sorted({r["brief"] for r in rows})
    seeds = sorted({r["seed"] for r in rows})
    models = sorted({r["model"] for r in rows})
    mock_llm = sum(r["mocked_llm"] for r in rows)
    mock_pf = sum(r["mocked_preform"] for r in rows)
    return (f"{len(rows)} runs; briefs {', '.join(briefs)}; seeds {seeds}; model {', '.join(models)}"
            f"{' (MOCK model in ' + str(mock_llm) + ' runs)' if mock_llm else ''}; final PreForm "
            f"{'real in all runs' if not mock_pf else 'MOCK in ' + str(mock_pf) + ' runs'}; "
            f"in-loop PreForm check: local MOCK estimate. {extra}").strip()


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


def _legend(ax, ncol=3):
    leg = ax.legend(frameon=False, ncol=ncol, loc="upper right", fontsize=9)
    for t in leg.get_texts():
        t.set_color(INK)


def _fig(cap):
    fig, ax = plt.subplots(figsize=(9, 4.8), facecolor=SURFACE)
    fig.text(0.01, 0.01, cap, fontsize=7, color=INK2, wrap=True)
    return fig, ax


def chart_failures_left(rows):
    cap = caption(rows, "MAIN RESULT. Every run is a point: check failures left in the final design (real PreForm "
                        "warnings included). Filled = passes everything. Lower is better; ranking is pass, then "
                        "failures left, then quality.")
    fig, ax = _fig(cap)
    groups = sorted({(r["brief"], r["mode"]) for r in rows}, key=lambda g: (g[0], MODES.index(g[1])))
    for i, (b, m) in enumerate(groups):
        pts = [r for r in rows if r["brief"] == b and r["mode"] == m]
        for j, r in enumerate(pts):
            x = i + (j - (len(pts) - 1) / 2) * 0.08
            ax.scatter([x], [r["all_failures"]], s=64, color=COLORS[m] if r["passed"] else SURFACE,
                       edgecolors=COLORS[m], linewidths=2, zorder=3)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([f"{b.split('_')[0]}\n{m.replace('_', '-')}" for b, m in groups], color=INK, fontsize=8)
    ax.set_ylim(bottom=-0.5)
    _style(ax, "Failures left in the final design", "failures left (lower is better)")
    fig.subplots_adjust(bottom=0.25)
    return fig, cap


def chart_failures_vs_evals(rows):
    cap = caption(rows, "Lines: failures of the current accepted design after each evaluation, one line per run.")
    fig, ax = _fig(cap)
    seen = set()
    for r in rows:
        if r["mode"] == "one_shot" or not r["curve"]:
            continue
        lab = r["mode"] if r["mode"] not in seen else None
        seen.add(r["mode"])
        ax.plot(range(1, len(r["curve"]) + 1), r["curve"], color=COLORS[r["mode"]], linewidth=2, alpha=0.7,
                label=lab, linestyle="-" if r["brief"].startswith("a") else "--")
    _style(ax, "Check failures vs. evaluations (solid: brief a, dashed: others)", "failures")
    ax.set_xlabel("evaluation", color=INK2)
    _legend(ax)
    fig.subplots_adjust(bottom=0.2)
    return fig, cap


def chart_quality(rows):
    cap = caption(rows, "Every run is a point (filled = all checks pass, hollow = not). Quality only ranks designs "
                        "with the same pass status and failures left; a failing design never outranks a passing one.")
    fig, ax = _fig(cap)
    groups = sorted({(r["brief"], r["mode"]) for r in rows}, key=lambda g: (g[0], MODES.index(g[1])))
    for i, (b, m) in enumerate(groups):
        pts = [r for r in rows if r["brief"] == b and r["mode"] == m]
        for j, r in enumerate(pts):
            x = i + (j - (len(pts) - 1) / 2) * 0.08
            ax.scatter([x], [r["quality"]], s=64, color=COLORS[m] if r["passed"] else SURFACE,
                       edgecolors=COLORS[m], linewidths=2, zorder=3)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([f"{b.split('_')[0]}\n{m.replace('_', '-')}" for b, m in groups], color=INK, fontsize=8)
    _style(ax, "Quality score per run", "quality score (higher is better)")
    fig.subplots_adjust(bottom=0.25)
    return fig, cap


def chart_cost(rows):
    cap = caption(rows, "Estimated model cost per run from logged token counts and config prices.")
    fig, ax = _fig(cap)
    rs = sorted(rows, key=lambda r: (r["brief"], MODES.index(r["mode"]), r["seed"]))
    ax.bar(range(len(rs)), [r["cost_usd"] for r in rs], color=[COLORS[r["mode"]] for r in rs],
           edgecolor=SURFACE, linewidth=2)
    ax.set_xticks(range(len(rs)))
    ax.set_xticklabels([f"{r['brief'][0]} {r['mode'][:3]} s{r['seed']}" for r in rs], rotation=90, fontsize=7,
                       color=INK)
    for m in MODES:
        ax.bar([], [], color=COLORS[m], label=m.replace("_", "-"))
    _style(ax, "Model cost per run", "USD")
    _legend(ax)
    fig.subplots_adjust(bottom=0.3)
    return fig, cap


def chart_print(rows):
    rs = [r for r in rows if r["resin_ml"] is not None]
    cap = caption(rs, "Final designs only; print time and resin (incl. supports) from the final PreForm run.")
    fig, ax = _fig(cap)
    for m in MODES:
        pts = [r for r in rs if r["mode"] == m]
        ax.scatter([r["resin_ml"] for r in pts], [r["print_time_min"] for r in pts], s=64, color=COLORS[m],
                   edgecolors=SURFACE, linewidths=2, label=m.replace("_", "-"), zorder=3)
    _style(ax, "Print time and resin of final designs (base + lid)", "print time (min)")
    ax.set_xlabel("resin (ml)", color=INK2)
    _legend(ax)
    fig.subplots_adjust(bottom=0.2)
    return fig, cap


def chart_edits(rows):
    cap = caption(rows, "Edits that were evaluated or put to a joint-edit vote, by proposing agent.")
    fig, ax = _fig(cap)
    acc = {a: sum(r["edits"].get(a, {}).get("accepted", 0) for r in rows) for a in AGENT_ORDER}
    rej = {a: sum(r["edits"].get(a, {}).get("rejected", 0) for r in rows) for a in AGENT_ORDER}
    xs = range(len(AGENT_ORDER))
    ax.bar([x - 0.18 for x in xs], [acc[a] for a in AGENT_ORDER], width=0.34, color="#1baf7a", label="accepted",
           edgecolor=SURFACE, linewidth=2)
    ax.bar([x + 0.18 for x in xs], [rej[a] for a in AGENT_ORDER], width=0.34, color="#6250d6", label="rejected",
           edgecolor=SURFACE, linewidth=2)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(AGENT_ORDER, color=INK)
    _style(ax, "Accepted vs. rejected edits per agent", "edits")
    _legend(ax, 2)
    fig.subplots_adjust(bottom=0.2)
    return fig, cap


CHARTS = {"failures_left": chart_failures_left, "failures_vs_evals": chart_failures_vs_evals,
          "quality": chart_quality, "cost": chart_cost,
          "print": chart_print, "edits": chart_edits}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 2) if xs else None


def table(rows):
    out = []
    for b in sorted({r["brief"] for r in rows}):
        for m in MODES:
            rs = [r for r in rows if r["brief"] == b and r["mode"] == m]
            if rs:
                out.append({"brief": b, "mode": m, "runs": len(rs), "passed": f"{sum(r['passed'] for r in rs)}/{len(rs)}",
                            "failures_left_mean": _mean([r["all_failures"] for r in rs]),
                            "best_rank": min(r["rank"] for r in rs),
                            "quality_mean": _mean([r["quality"] for r in rs]),
                            "quality_min_max": f"{min(r['quality'] for r in rs)}..{max(r['quality'] for r in rs)}",
                            "evals_to_pass": _mean([r["evals_to_pass"] for r in rs]),
                            "edits_prop_acc": f"{_mean([r['edits_proposed'] for r in rs])}/{_mean([r['edits_accepted'] for r in rs])}",
                            "preform_warnings": _mean([r["preform_warnings"] for r in rs]),
                            "print_min": _mean([r["print_time_min"] for r in rs]),
                            "resin_ml": _mean([r["resin_ml"] for r in rs]),
                            "seconds": _mean([r["seconds"] for r in rs]), "cost_usd": _mean([r["cost_usd"] for r in rs])})
    return out


def report(root):
    root = Path(root)
    rows = collect(root)
    if not rows:
        print("no runs in", root)
        return
    out, tag = root.parent, root.name
    with open(out / f"{tag}_per_run.csv", "w", newline="", encoding="utf-8") as f:
        keys = [k for k in rows[0] if k not in ("curve", "edits")]
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    t = table(rows)
    t.sort(key=lambda a: (a["brief"], -int(a["passed"].split("/")[0]), a["failures_left_mean"], -(a["quality_mean"] or 0)))
    lines = [f"# Benchmark summary ({tag})", "", f"_{caption(rows)}_", "",
             f"Print: {rows[0]['material']}, {rows[0]['layer_mm']} mm layers; clearances {rows[0]['clearances']}; "
             "every mode starts from the same naive spec. Failures are counted by our own checks.",
             "Ranking: passes everything, then fewest failures left, then quality score (best_rank = best run's rank "
             "among all runs in this set).", "",
             "| " + " | ".join(t[0]) + " |", "|" + "---|" * len(t[0])]
    lines += ["| " + " | ".join(str(v) for v in r.values()) + " |" for r in t]
    for name, fn in CHARTS.items():
        fig, cap = fn(rows)
        fig.savefig(out / f"{tag}_{name}.png", dpi=150, facecolor=SURFACE)
        plt.close(fig)
        lines += ["", f"![{name}]({tag}_{name}.png)", f"_{cap}_"]
    (out / f"{tag}_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:len(t) + 8]))


if __name__ == "__main__":
    report(sys.argv[1] if len(sys.argv) > 1 else "results/benchmark/runs_v2")
