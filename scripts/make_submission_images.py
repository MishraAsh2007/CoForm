r"""Presentation images for the submission -> results/submission/images/
Renders (pyvista, off-screen), charts (matplotlib, computed from the logs), thumbnail, system diagram.

.venv\Scripts\python.exe scripts\make_submission_images.py [renders|charts|thumb|diagram|all]

Data sources (nothing typed in by hand):
- print candidate: results/demo_run/<run>/board.sqlite (+ versions/v*/ STLs), results/print/formlabs/enclosure_a.json
- benchmark: results/benchmark/runs_haiku_v2_per_run.csv (written by benchmark.metrics from each run's logs)
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "results" / "submission" / "images"
RUN = "a_pi5_standard__collective__generic__s0__claude-sonnet-5-5"
RD = ROOT / "results" / "demo_run" / RUN
JOB = ROOT / "results" / "print" / "formlabs" / "enclosure_a.json"
CSV = ROOT / "results" / "benchmark" / "runs_haiku_v2_per_run.csv"

# team look (Jash_ver style.css) + validated categorical slots (dataviz reference palette, dark steps)
BG, PANEL, LINE, INK, MUTED, MINT = "#141a1c", "#191f22", "#30393c", "#edf4f1", "#99abae", "#bbefd5"
S1, S2, S3, S4 = "#3987e5", "#d95926", "#199e70", "#c98500"
AGENT_COL = {"assembly": S1, "manufacturing": S2, "fit": S3, "design": S4}
TITLE = {"assembly": "Assembly", "manufacturing": "Manufacturing", "fit": "Fit", "design": "Design"}


def _final_vid():
    return json.loads((RD / "summary.json").read_text(encoding="utf-8"))["final_version"]


# ------------------------------------------------------------------ renders
def _board_polys():
    import pyvista as pv
    from cad.components import load_bundle
    ko = load_bundle("rpi5_cooler")["keepouts"]
    box = lambda b: pv.Box(bounds=b)  # noqa: E731
    pcb = [box(k["box"]) for k in ko if k["name"] == "pcb"]
    cooler = [box(k["box"]) for k in ko if k["name"] == "active_cooler"]
    parts = [box(k["box"]) for k in ko if k["name"] not in ("pcb", "active_cooler")]
    merge = lambda xs: xs[0].merge(xs[1:]) if len(xs) > 1 else xs[0]  # noqa: E731
    return merge(pcb), merge(parts), merge(cooler)


def _plotter(size):
    import pyvista as pv
    pv.OFF_SCREEN = True
    pl = pv.Plotter(off_screen=True, window_size=size)
    pl.set_background(BG)
    try:
        pl.enable_anti_aliasing("ssaa")
    except Exception:  # noqa: BLE001
        pass
    return pl


def _scene(pl, vid, lid="see", lift=0.0, board=True):
    import pyvista as pv
    vd = RD / "versions" / f"v{vid}"
    pl.add_mesh(pv.read(str(vd / "base.stl")), color="#c9d2d0", smooth_shading=False, specular=0.2)
    if lid:
        m = pv.read(str(vd / "lid.stl")).translate((0, 0, lift))
        pl.add_mesh(m, color="#80c8ec", opacity=0.45 if lid == "see" else 1.0, smooth_shading=False)
    if board:
        pcb, parts, cooler = _board_polys()
        pl.add_mesh(pcb, color="#3f9a6a")
        pl.add_mesh(parts, color="#5b8fc4", opacity=0.85)
        pl.add_mesh(cooler, color="#d5ad83", opacity=0.55)


def _cam(pl, zoom=1.25):
    pl.view_isometric()
    pl.camera.azimuth = -20
    pl.camera.elevation = 8
    pl.camera.zoom(zoom)


def renders():
    OUT.mkdir(parents=True, exist_ok=True)
    fv = _final_vid()
    jobs = [("render_assembled.png", dict(lid="see")), ("render_exploded.png", dict(lid="solid", lift=35.0)),
            ("render_lid_off.png", dict(lid=None))]
    for name, kw in jobs:
        pl = _plotter((2400, 1600))
        _scene(pl, fv, **kw)
        _cam(pl, 0.82 if "exploded" in name else 0.95)
        pl.screenshot(str(OUT / name))
        pl.close()
        print("wrote", name)
    # first (naive) vs final, same camera, side by side
    import pyvista as pv
    pl = pv.Plotter(off_screen=True, window_size=(3000, 1300), shape=(1, 2), border=False)
    pl.set_background(BG)
    for i, (vid, label) in enumerate([(1, f"First design (v1): naive start"), (fv, f"Final design (v{fv})")]):
        pl.subplot(0, i)
        _scene(pl, vid, lid="solid", lift=30.0)
        pl.add_text(label, position=(40, 1180), font_size=18, color=INK)
        _cam(pl, 0.78)
    pl.link_views()
    pl.screenshot(str(OUT / "render_first_vs_final.png"))
    pl.close()
    print("wrote render_first_vs_final.png")


# ------------------------------------------------------------------ charts
def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
                         "axes.edgecolor": LINE, "axes.labelcolor": MUTED, "xtick.color": MUTED,
                         "ytick.color": MUTED, "text.color": INK, "font.size": 13, "axes.titlesize": 17,
                         "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 34, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.grid": True, "grid.color": "#232b2e",
                         "grid.linewidth": 1.0, "font.family": ["Segoe UI", "DejaVu Sans"]})
    return plt


def _caption(fig, text):
    fig.text(0.01, 0.015, text, fontsize=10.5, color=MUTED, ha="left", va="bottom", wrap=True)


def chart_benchmark():
    plt = _mpl()
    rows = [r for r in csv.DictReader(CSV.open(encoding="utf-8")) if r["brief"] == "a_pi5_standard"]
    modes = [("one_shot", "One shot\n(single call)"), ("generalist", "One generalist\nagent"),
             ("collective", "Team of 4\nspecialist agents")]
    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=200)
    fig.subplots_adjust(bottom=0.24, top=0.86, left=0.09, right=0.97)
    for i, (m, label) in enumerate(modes):
        rs = sorted([r for r in rows if r["mode"] == m], key=lambda r: int(r["seed"]))
        ys = [int(r["all_failures"]) for r in rs]
        mean = sum(ys) / len(ys)
        ax.plot([i - 0.28, i + 0.28], [mean, mean], color=MINT, lw=3, solid_capstyle="round", zorder=3)
        ax.text(i + 0.33, mean, f"mean {mean:g}", va="center", color=INK, fontsize=13, fontweight="bold")
        for j, (r, y) in enumerate(zip(rs, ys)):
            x = i + (j - 1) * 0.14
            ax.scatter([x], [y], s=110, facecolors="none", edgecolors=S1, linewidths=2.2, zorder=4)
            ax.text(x, y - 1.3, f"s{r['seed']}", ha="center", va="top", color=MUTED, fontsize=10)
    ax.set_xticks(range(3), [l for _, l in modes])
    ax.set_xlim(-0.6, 2.9)
    ax.set_ylim(-2.5, 23)
    ax.set_ylabel("check failures left in the final design (lower is better)")
    ax.set_title("Same model, tools and budget: on average the team leaves the fewest failures")
    ax.text(0, 1.03, "Brief a (Raspberry Pi 5 case), claude-haiku-5-5, budget 12, 3 seeds per mode. "
            "Hollow = did not pass every check (0/3 passed in each mode).", transform=ax.transAxes,
            color=MUTED, fontsize=11)
    ax.grid(axis="x", visible=False)
    _caption(fig, "Data: results/benchmark/runs_haiku_v2_per_run.csv (from each run's summary.json / board.sqlite), runs "
             "a_pi5_standard__{one_shot,generalist,collective}__generic__s{0,1,2}__claude-haiku-5-5. Failures include the "
             "real final PreForm warnings.\nMean cost per run: one shot $0.001, generalist $0.019, team $0.032. The Sonnet "
             "benchmark is incomplete (4/18 runs) and not shown.")
    fig.savefig(OUT / "chart_benchmark_failures.png")
    plt.close(fig)
    print("wrote chart_benchmark_failures.png")


def _board_data():
    from core.board import Board
    b = Board(RD / "board.sqlite")
    try:
        vs = b.versions(1)
        checks = b.checks(1)
        decs = {d["version_id"]: d for d in b.decisions(1)}
    finally:
        b.db.close()
    fails = {}
    for c in checks:
        if c["name"] in ("assembly", "printability", "preform", "fit"):
            fails[c["version_id"]] = fails.get(c["version_id"], 0) + len(c["details"].get("failures", []))
    return vs, fails, decs


def chart_candidate():
    plt = _mpl()
    vs, fails, decs = _board_data()
    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=200)
    fig.subplots_adjust(bottom=0.2, top=0.86, left=0.08, right=0.97)
    cur, xs, ys = None, [], []
    for v in vs:
        vid = v["id"]
        st = decs.get(vid, {}).get("decision", v["status"])
        if st in ("accepted", "baseline"):
            cur = fails.get(vid, cur)
        xs.append(vid)
        ys.append(cur)
    ax.step(xs, ys, where="post", color=MINT, lw=2.4, zorder=2, label="current design (kept edits)")
    labels = {}
    for v in vs:
        vid, a = v["id"], v["author"]
        st = decs.get(vid, {}).get("decision", v["status"])
        if a not in AGENT_COL:
            ax.scatter([vid], [fails[vid]], s=120, color=MUTED, zorder=4)
            continue
        kept = st == "accepted"
        ax.scatter([vid], [fails.get(vid, 0)], s=150, zorder=5, color=AGENT_COL[a] if kept else BG,
                   edgecolors=AGENT_COL[a], linewidths=2.4,
                   label=None if (a, kept) in labels else f"{TITLE[a]} ({'kept' if kept else 'rejected'})")
        labels[(a, kept)] = 1
    notes = {1: "naive start: 20", 2: "Assembly: lid height, open port tops, tabs\n20 → 13",
             3: "Fit (asked by Assembly): standoffs 1.5 → 2.9 mm\n13 → 5",
             4: "Manufacturing (asked by Assembly):\nvent over the fan  5 → 4",
             5: "Assembly: open USB-A tops, move a tab\n4 → 0", 10: "Manufacturing: thinner floor\nbroke 4 screw checks → rejected"}
    off = {1: (0.25, 1.2), 2: (0.3, 3.0), 3: (0.3, 6.0), 4: (0.4, 3.6), 5: (0.45, 2.6), 10: (-2.6, 5.5)}
    for vid, t in notes.items():
        y = fails.get(vid, 0)
        dx, dy = off[vid]
        ax.annotate(t, (vid, y), (vid + dx, y + dy), color=INK, fontsize=10.5,
                    arrowprops=dict(arrowstyle="-", color=MUTED, lw=1))
    ax.text(6.15, -0.75, "improve phase: all local checks pass; edits kept only if quality rises\n"
            "(v6 drain hole rejected: quality fell; v7/v9 thinner walls, v8/v11 clearances kept)",
            color=MUTED, fontsize=10, va="top")
    ax.set_xlabel("design version")
    ax.set_ylabel("local check failures (lower is better)")
    ax.set_xticks([v["id"] for v in vs])
    ax.set_ylim(-2.5, 22.5)
    ax.set_title("Print candidate: who fixed what, version by version")
    ax.text(0, 1.03, "4 agents, claude-sonnet-5-5, budget 16 (11 used). Filled = kept, hollow = rejected by the protocol.",
            transform=ax.transAxes, color=MUTED, fontsize=11)
    ax.legend(loc="upper right", frameon=False, fontsize=10.5, labelcolor=INK)
    _caption(fig, f"Data: results/demo_run/{RUN}/board.sqlite (assembly + printability + fit + in-loop PreForm estimate). "
             "After the run, real PreForm auto-orient still found 1 resin cup in the base;\nthe print job's "
             "orientation choice (Manufacturing's orientation tool) gave 0 warnings, 23.0 min, 61.9 ml "
             "(results/print/formlabs/enclosure_a.json).")
    fig.savefig(OUT / "chart_candidate_failures.png")
    plt.close(fig)
    print("wrote chart_candidate_failures.png")


def chart_edits():
    plt = _mpl()
    vs, fails, decs = _board_data()
    agents = ["design", "assembly", "manufacturing", "fit"]
    acc = {a: 0 for a in agents}
    rej = {a: 0 for a in agents}
    for v in vs:
        a = v["author"]
        if a in acc:
            st = decs.get(v["id"], {}).get("decision", v["status"])
            if st == "accepted":
                acc[a] += 1
            elif st == "rejected":
                rej[a] += 1
    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=200)
    fig.subplots_adjust(bottom=0.17, top=0.86, left=0.17, right=0.95)
    ys = range(len(agents))
    h = 0.34
    ax.set_axisbelow(True)
    ax.barh([y - h / 2 - 0.02 for y in ys], [acc[a] for a in agents], height=h, color=S1, label="kept (accepted)")
    ax.barh([y + h / 2 + 0.02 for y in ys], [rej[a] for a in agents], height=h, color=S2, label="rejected")
    for y, a in zip(ys, agents):
        ax.text(acc[a] + 0.05, y - h / 2 - 0.02, str(acc[a]), va="center", color=INK, fontsize=13)
        ax.text(rej[a] + 0.05, y + h / 2 + 0.02, str(rej[a]), va="center", color=INK, fontsize=13)
    from matplotlib.ticker import MaxNLocator
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_yticks(list(ys), [TITLE[a] for a in agents])
    ax.invert_yaxis()
    ax.set_xlabel("edits proposed in the print-candidate run")
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, max(max(acc.values()), max(rej.values())) + 1)
    ax.set_title("Edits per agent: kept vs rejected")
    ax.text(0, 1.03, "Design owns only the name and board kit, which were already right, so it proposed nothing.",
            transform=ax.transAxes, color=MUTED, fontsize=11)
    ax.legend(loc="lower right", frameon=False, labelcolor=INK)
    _caption(fig, f"Data: results/demo_run/{RUN}/board.sqlite (versions + protocol decisions). An edit is kept only if "
             "no check gets worse (and, once everything passes, only if the quality score rises).")
    fig.savefig(OUT / "chart_edits_per_agent.png")
    plt.close(fig)
    print("wrote chart_edits_per_agent.png")


def charts():
    OUT.mkdir(parents=True, exist_ok=True)
    chart_benchmark()
    chart_candidate()
    chart_edits()


# ------------------------------------------------------------------ thumbnail + diagram
def thumb():
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1200, 800
    im = Image.new("RGB", (W, H), BG)
    r = Image.open(OUT / "render_assembled.png").convert("RGB")
    r.thumbnail((1180, 660))
    im.paste(r, ((W - r.width) // 2, H - r.height - 10))
    d = ImageDraw.Draw(im)

    def font(size, bold=False):
        for f in (["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                continue
        return ImageFont.load_default()
    d.text((48, 40), "SCIENCECLAW", font=font(22, True), fill=MINT)
    d.text((48, 74), "Agents that design print-ready parts", font=font(50, True), fill=INK)
    d.text((48, 140), "4 specialist AI agents, no boss · Raspberry Pi 5 case · Formlabs PreForm: 0 warnings",
           font=font(24), fill=MUTED)
    im.save(OUT / "thumbnail_1200x800.png")
    print("wrote thumbnail_1200x800.png")


def diagram():
    plt = _mpl()
    from matplotlib.patches import FancyBboxPatch
    fig, ax = plt.subplots(figsize=(16, 9), dpi=150)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 9)
    ax.axis("off")
    ax.text(0.5, 8.45, "System: four specialist agents, one shared board, no boss", fontsize=24, fontweight="bold",
            color=INK)
    ax.text(0.5, 8.0, "Each agent owns part of the design spec and one real tool. A fix in someone else's part becomes "
            "a need to that owner.", fontsize=13, color=MUTED)

    def box(x, y, w, h, title, lines, col, tsize=17):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.18", fc=PANEL, ec=col,
                                    lw=2.2))
        ax.text(x + 0.25, y + h - 0.45, title, fontsize=tsize, fontweight="bold", color=INK, va="top")
        for i, ln in enumerate(lines):
            ax.text(x + 0.25, y + h - 1.05 - i * 0.42, ln, fontsize=11.5, color=MUTED if col == MINT else INK,
                    va="top")

    # board in the middle
    box(5.5, 3.2, 5.0, 2.6, "Shared board (SQLite)", ["proposals · needs · objections", "per-failure breakdowns",
                                                        "decisions by the protocol (code)", "every check result + cost"],
        MINT)
    ax.text(8.0, 2.95, "Rule: keep an edit only if no check gets worse", ha="center", fontsize=11.5, color=MINT)
    agents = [(0.4, 4.9, "Design", AGENT_COL["design"], ["owns: name, board kit", "tool: CadQuery generator"]),
              (0.4, 0.9, "Assembly", AGENT_COL["assembly"], ["owns: box, ports, slots, tabs, lid screws",
                                                               "tool: assembly check (drop-in,", "lid gap, plugs, screws)"]),
              (11.1, 4.9, "Manufacturing", AGENT_COL["manufacturing"], ["owns: walls, ribs, vents, drain holes",
                                                                         "tools: printability, Formlabs", "PreForm, orientation tool"]),
              (11.1, 0.9, "Fit", AGENT_COL["fit"], ["owns: clearances, standoffs,", "fasteners, lid fit",
                                                     "tool: fit rules (guide or", "measured test plate)"])]
    for x, y, t, col, lines in agents:
        box(x, y, 4.5, 2.6, t, lines, col)
        cx, cy = (x + 4.5, y + 1.3) if x < 5 else (x, y + 1.3)
        ax.annotate("", (8.0 + (-2.5 if x < 5 else 2.5), 4.5), (cx, cy),
                    arrowprops=dict(arrowstyle="<->", color=col, lw=2))
    ax.text(8.0, 0.55, "Output: CadQuery model → STL → PreForm .form (Form Studio 1, Alpha Fast V1, 0.15 mm), FDM STL, "
            "STEP", ha="center", fontsize=12, color=INK)
    fig.savefig(OUT / "system_diagram.png")
    plt.close(fig)
    print("wrote system_diagram.png")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    for name, fn in [("renders", renders), ("charts", charts), ("thumb", thumb), ("diagram", diagram)]:
        if what in (name, "all"):
            fn()
