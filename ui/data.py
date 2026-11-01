"""Data for the UI: run discovery, plain-language labels, agent stats, story steps and cached meshes.
Everything here is read from the run logs (board.sqlite, summary.json) and result files. Nothing is invented."""
import json
from pathlib import Path

import streamlit as st

from cad.common import ROOT, load_config

CFG = load_config()
RUNS = ROOT / CFG["run"]["out_dir"]
DEMO = ROOT / "results" / "demo_run"
BENCH = ROOT / "results" / "benchmark"
PRINT = ROOT / "results" / "print"
SUBMIT_IMG = ROOT / "results" / "submission" / "images"
BRIEFS = sorted((ROOT / "benchmark" / "briefs").glob("*.yaml"))
BEST = "a_pi5_standard__collective__generic__s0__claude-sonnet-5-5"
PRINT_JOB = PRINT / "formlabs" / "enclosure_a.json"

AGENTS = {
    "design": {"title": "Design", "icon": "✏️",
               "role": "Starts the design and picks the board kit",
               "owns": "the part name and the board kit (Pi 5 + Active Cooler)",
               "tool": "CadQuery design generator (one-shot design from the brief)"},
    "assembly": {"title": "Assembly", "icon": "🧩",
                 "role": "Makes sure the board drops in, the lid closes and every port and screw is reachable",
                 "owns": "box size and lid height, port cutouts, cable slots, mounting tabs, lid screws",
                 "tool": "Assembly check: drop-in sweep, lid clearance, plug zones, screw reach and engagement"},
    "manufacturing": {"title": "Manufacturing", "icon": "🏭",
                      "role": "Makes it printable on the Formlabs printer, with little resin",
                      "owns": "wall and floor thickness, ribs, vents, drain holes",
                      "tool": "Printability check, real Formlabs PreForm, print-orientation tool, wall-thickness map"},
    "fit": {"title": "Fit", "icon": "📏",
            "role": "Sets clearances and fasteners that will really fit after printing",
            "owns": "clearances, screws and standoffs, lid fit, where clearances come from",
            "tool": "Fit rules: Formlabs design-guide clearances, or measured ones from our printed test plate"},
}

MODES = {"collective": "Team of 4 specialist agents (no boss)",
         "generalist": "One generalist agent (same tools)",
         "one_shot": "One shot: a single model call, no checking loop"}

CHECKS = {"assembly": "Assembly", "printability": "Printability", "preform": "PreForm (quick local estimate)",
          "fit": "Fit", "preform_real": "PreForm (real, asked by Manufacturing)",
          "preform_final": "PreForm (real, final design, auto-orient)"}

KEY_PLAIN = {
    "box/inner_top": "lid height", "box": "box size", "ports": "port cutouts", "cable_slots": "cable slots",
    "mount_tabs": "mounting tabs", "lid_screws": "lid screws", "walls/wall": "wall thickness",
    "walls/floor": "floor thickness", "walls": "walls", "ribs": "ribs", "vents": "lid vent", "drain_holes": "drain hole",
    "clearances": "clearances", "fasteners": "fasteners", "standoffs/height": "standoff height",
    "standoffs": "standoffs", "lid": "lid fit", "clearance_source": "clearance source", "name": "name",
    "bundle": "board kit",
}


def brief_info(path):
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


BRIEF_PLAIN = {"a_pi5_standard": "Raspberry Pi 5 + Active Cooler case (standard)",
               "b_pi5_tight_robot": "Raspberry Pi 5 case, tight fit for a robot",
               "c_arduino_uno": "Arduino Uno case",
               "d_rpi_pico": "Raspberry Pi Pico case",
               "e_freeform_peg_block": "Free-form peg block (EXPERIMENTAL)"}


def run_dirs():
    """Every run folder with a board log and at least one version; newest first; results/runs wins over the
    committed demo copy of the same run."""
    seen, out = set(), []
    roots = [RUNS, DEMO] + sorted(p for p in BENCH.glob("runs*") if p.is_dir())
    for r in roots:
        if not r.exists():
            continue
        for d in r.iterdir():
            if (d / "board.sqlite").exists() and d.name not in seen:
                seen.add(d.name)
                out.append(d)
    return sorted(out, key=lambda d: (d.name != BEST, -d.stat().st_mtime))


SHORT = {"a_pi5_standard": "Pi 5 case", "b_pi5_tight_robot": "Pi 5 tight robot case", "c_arduino_uno": "Arduino Uno case",
         "d_rpi_pico": "Pi Pico case", "e_freeform_peg_block": "Peg block (EXPERIMENTAL)"}


def run_label(d):
    n = d.name
    parts = n.split("__")
    brief = BRIEF_PLAIN.get(parts[0], parts[0])
    mode = {"collective": "4 agents", "generalist": "1 generalist", "one_shot": "one shot"}.get(
        parts[1] if len(parts) > 1 else "", "")
    model = "MOCK model" if "mock" in parts else ("Sonnet" if "sonnet" in n else "Haiku" if "haiku" in n else "")
    seed = next((p for p in parts if p.startswith("s") and p[1:].isdigit()), "")
    star = "★ Print candidate · " if n == BEST else ""
    brief = SHORT.get(parts[0], brief)
    where = "" if d.parent in (RUNS, DEMO) else f" · {d.parent.name}"
    return f"{star}{brief} · {mode} · {model} · seed {seed[1:]}{where}"


def board(d):
    from core.board import Board
    return Board(Path(d) / "board.sqlite")


def mtime(d):
    p = Path(d) / "board.sqlite"
    return p.stat().st_mtime if p.exists() else 0


@st.cache_data(show_spinner=False, max_entries=64)
def load_run(d, _mtime):
    """Everything the pages need from one run, as plain dicts (cached until the board file changes)."""
    d = Path(d)
    b = board(d)
    run = b.run(1)
    vs = b.versions(1)
    checks = b.checks(1)
    msgs = b.messages(1)
    decs = b.decisions(1)
    arts = b.artifacts(1)
    usage = b.usage_total(1)
    summ = json.loads((d / "summary.json").read_text(encoding="utf-8")) if (d / "summary.json").exists() else None
    return {"dir": str(d), "name": d.name, "run": run, "versions": vs, "checks": checks, "messages": msgs,
            "decisions": decs, "artifacts": arts, "usage": usage, "summary": summ}


def get_run(d):
    return load_run(str(d), mtime(d))


def checks_of(R, vid):
    return {c["name"]: c for c in R["checks"] if c["version_id"] == vid}


def failures_of(R, vid, names=("assembly", "printability", "preform", "fit")):
    cs = checks_of(R, vid)
    return sum(len(cs[n]["details"].get("failures", [])) for n in names if n in cs) if cs else None


def quality_of(R, vid):
    q = checks_of(R, vid).get("quality")
    return q["details"].get("metrics", {}).get("score") if q else None


def decision_of(R, vid):
    return next((x for x in R["decisions"] if x["version_id"] == vid), None)


def artifact(R, vid, kind):
    """Path of a version's artifact: the logged path, else the demo-bundle copy."""
    p = next((a["path"] for a in R["artifacts"] if a["version_id"] == vid and a["kind"] == kind), None)
    if p and Path(p).exists():
        return Path(p)
    name = {"base": "base.stl", "lid": "lid.stl", "assembled": "snapshot.png"}.get(kind)
    alt = Path(R["dir"]) / "versions" / f"v{vid}" / name if name else None
    return alt if alt and alt.exists() else None


def evaluated_versions(R):
    return [v["id"] for v in R["versions"] if checks_of(R, v["id"])]


def plain_patch(patch):
    """'replace /standoffs/height 2.9' -> 'standoff height → 2.9 mm' (short, plain)."""
    out = []
    counts = {}
    for op in patch or []:
        path = op.get("path", "").strip("/")
        parts = path.split("/")
        key2 = "/".join(parts[:2])
        label = KEY_PLAIN.get(key2) or KEY_PLAIN.get(parts[0], parts[0])
        val = op.get("value")
        if parts[0] == "ports" and len(parts) > 2 and parts[2] == "extra":
            out.append(f"a port cutout widened by {val} mm")
            continue
        if parts[0] == "cable_slots" and len(parts) > 2 and parts[2] == "enabled":
            out.append("cable slot opened" if val else "cable slot closed")
            continue
        if parts[0] == "mount_tabs" and len(parts) > 2 and parts[2] in ("side", "offset"):
            counts["a mounting tab moved"] = 1
            continue
        if parts[0] == "clearance_source" and val == "measured":
            out.append("clearances from the measured test plate (no data yet: guide values used)")
            continue
        if parts[0] == "ports" and len(parts) > 2 and parts[2] == "open_top":
            counts["port cutouts opened at the top"] = counts.get("port cutouts opened at the top", 0) + 1
            continue
        if op.get("op") == "add" and parts[0] in ("mount_tabs", "vents", "drain_holes"):
            counts[f"{label} added"] = counts.get(f"{label} added", 0) + 1
            continue
        if parts[0] == "clearances":
            counts[f"clearances → {val} mm"] = counts.get(f"clearances → {val} mm", 0) + 1
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out.append(f"{label} → {val} mm" if parts[0] in ("box", "walls", "standoffs") else f"{label} → {val}")
        elif isinstance(val, str):
            out.append(f"{label} → {val}")
        else:
            out.append(f"{label} changed")
    out += [f"{n} {k}" if n > 1 else k for k, n in counts.items()]
    seen = []
    for x in out:
        if x not in seen:
            seen.append(x)
    return seen


def plain_fail(msg):
    """Short plain wording of a check failure message (the full text stays in Details)."""
    m = msg.replace("assembly: ", "").replace("preform_final: ", "").replace("preform_real: ", "")
    rules = [("hits the base while lowering", "board can't drop in: a connector hits the wall"),
             ("no stock length gives", "board screws can't grip: posts too short"),
             ("lid within", "lid touches a part on the board"), ("lid underside only", "lid sits on the tallest part"),
             ("vents open", "no vent over the fan"), ("has no slot", "a cable has no way out"),
             ("mounting tabs", "robot mounting tabs missing"), ("screwdriver blocked", "a screw can't be reached"),
             ("base within", "board too close to the base"), ("plug zone hits", "a plug can't go in"),
             ("cup", "a cup traps resin during printing")]
    for k, v in rules:
        if k in m:
            return v
    return m[:90]


def group_fails(msgs):
    """Count failures by plain wording, biggest first: [('board can't drop in...', 5), ...]."""
    c = {}
    for m in msgs:
        k = plain_fail(m)
        c[k] = c.get(k, 0) + 1
    return sorted(c.items(), key=lambda kv: -kv[1])


def breakdown(R, vid):
    m = next((x for x in R["messages"] if x["version_id"] == vid and x["kind"] == "breakdown"), None)
    return json.loads(m["text"]) if m else None


def agent_stats(R):
    """Per agent: proposals, accepted, rejected, needs posted / received, declines, notes, what they changed."""
    out = {a: {"proposed": 0, "accepted": 0, "rejected": 0, "needs_posted": 0, "needs_received": 0, "declines": 0,
               "passes": 0, "changes": [], "preform": 0} for a in AGENTS}
    for v in R["versions"]:
        a = v["author"]
        if a not in out:
            continue
        out[a]["proposed"] += 1
        dec = decision_of(R, v["id"])
        st_ = (dec or {}).get("decision", v["status"])
        if st_ == "accepted":
            out[a]["accepted"] += 1
            out[a]["changes"].append((v["id"], ", ".join(plain_patch(v["patch"])) or "edit"))
        elif st_ == "rejected":
            out[a]["rejected"] += 1
    for m in R["messages"]:
        a = m["author"]
        if m["kind"] == "need":
            if a in out:
                out[a]["needs_posted"] += 1
            to = m["text"].split(":", 1)[0].replace("to ", "").split(" ")[0].strip()
            if to in out:
                out[to]["needs_received"] += 1
        elif m["kind"] == "decline" and a in out:
            out[a]["declines"] += 1
        elif m["kind"] == "note" and a in out and m["text"].startswith("pass"):
            out[a]["passes"] += 1
        elif m["kind"] == "note" and a == "manufacturing" and m["text"].startswith("PreForm on"):
            out[a]["preform"] += 1
    return out


def story(R):
    """One step per version after the baseline: problem found -> who acted -> result, numbers from the log."""
    steps = []
    vs = R["versions"]
    for i, v in enumerate(vs):
        vid = v["id"]
        cs = checks_of(R, vid)
        dec = decision_of(R, vid)
        status = (dec or {}).get("decision", v["status"])
        parent = v.get("parent_id")
        before = failures_of(R, parent) if parent else None
        after = failures_of(R, vid)
        pcs = checks_of(R, parent) if parent else {}
        prob = [f["msg"] for n in ("assembly", "printability", "fit") if n in pcs
                for f in pcs[n]["details"].get("failures", [])]
        real = [f["msg"] for n in ("preform_real", "preform_final") if n in pcs
                for f in pcs[n]["details"].get("failures", [])]
        needs = [m for m in R["messages"] if parent and m["version_id"] == parent and m["kind"] in ("need", "decline")]
        bd = breakdown(R, vid)
        steps.append({"vid": vid, "author": v["author"], "status": status, "reason": v["reason"],
                      "patch": plain_patch(v["patch"]), "raw_patch": v["patch"],
                      "before": before, "after": after, "q_before": quality_of(R, parent) if parent else None,
                      "q_after": quality_of(R, vid), "problems": prob + real, "needs": needs, "breakdown": bd,
                      "decision_reason": (dec or {}).get("reason", ""), "checks": cs,
                      "real_preform": [c for n, c in cs.items() if n in ("preform_real", "preform_final")]})
    return steps


def print_job():
    return json.loads(PRINT_JOB.read_text(encoding="utf-8")) if PRINT_JOB.exists() else None


def print_job_matches(R):
    job = print_job()
    return bool(job and any(R["name"] in p for p in job.get("parts", [])))


# ---------------- meshes for the 3D viewer ----------------

@st.cache_data(show_spinner=False, max_entries=48)
def mesh_payload(path, _mtime, decimals=2):
    """STL -> {'v': flat xyz list, 'f': flat triangle index list} (rounded, merged; cached)."""
    import trimesh
    m = trimesh.load(str(path), force="mesh")
    m.merge_vertices()
    return {"v": [round(float(x), decimals) for x in m.vertices.reshape(-1)],
            "f": [int(i) for i in m.faces.reshape(-1)]}


def stl(path):
    p = Path(path)
    return mesh_payload(str(p), p.stat().st_mtime)


def _box(b, cell=4.0):
    """Closed box as triangles; faces are split into ~4 mm cells so the painter-sorted viewer draws it in the
    right order next to the (finely meshed) enclosure walls."""
    import math
    x0, x1, y0, y1, z0, z1 = b
    V, F = [], []

    def face(o, du, dv, flip):
        nu = max(1, math.ceil(math.hypot(*du) / cell))
        nv = max(1, math.ceil(math.hypot(*dv) / cell))
        base = len(V) // 3
        for j in range(nv + 1):
            for i in range(nu + 1):
                V.extend(o[k] + du[k] * i / nu + dv[k] * j / nv for k in range(3))
        for j in range(nv):
            for i in range(nu):
                a = base + j * (nu + 1) + i
                b_, c, d = a + 1, a + nu + 2, a + nu + 1
                F.extend([a, c, b_, a, d, c] if flip else [a, b_, c, a, c, d])

    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    face((x0, y0, z0), (dx, 0, 0), (0, dy, 0), True)    # bottom
    face((x0, y0, z1), (dx, 0, 0), (0, dy, 0), False)   # top
    face((x0, y0, z0), (dx, 0, 0), (0, 0, dz), False)   # front
    face((x0, y1, z0), (dx, 0, 0), (0, 0, dz), True)    # back
    face((x0, y0, z0), (0, dy, 0), (0, 0, dz), True)    # left
    face((x1, y0, z0), (0, dy, 0), (0, 0, dz), False)   # right
    return [round(v, 2) for v in V], F


def _boxes(boxes):
    V, F = [], []
    for b in boxes:
        v, f = _box(b)
        F += [i + len(V) // 3 for i in f]
        V += v
    return {"v": V, "f": F}


@st.cache_data(show_spinner=False)
def board_meshes(bundle):
    """The board from component data: PCB, connectors (blue), Active Cooler keep-out (orange, approximate)."""
    from cad.components import load_bundle
    comp = load_bundle(bundle)
    ko = comp["keepouts"]
    pcb = [k["box"] for k in ko if k["name"] == "pcb"]
    cooler = [k["box"] for k in ko if k["name"] == "active_cooler"]
    parts = [k["box"] for k in ko if k["name"] not in ("pcb", "active_cooler")]
    return {"pcb": _boxes(pcb), "parts": _boxes(parts), "cooler": _boxes(cooler)}


def scene(R, vid, with_board=True):
    """Meshes for one version: base, lid, and optionally the board (from the spec's board kit)."""
    meshes = []
    b, l = artifact(R, vid, "base"), artifact(R, vid, "lid")
    if b:
        meshes.append({"name": "Base", "group": "base", "color": "#c9d2d0", "opacity": 1.0, **stl(b)})
    if l:
        meshes.append({"name": "Lid", "group": "lid", "color": "#80c8ec", "opacity": 0.55, **stl(l)})
    if with_board and (b or l):
        spec = next((v["spec"] for v in R["versions"] if v["id"] == vid), {})
        try:
            bm = board_meshes(spec.get("bundle", "rpi5_cooler"))
        except Exception:  # noqa: BLE001 - a free-form part has no board
            bm = None
        if bm:
            meshes += [{"name": "Raspberry Pi 5 board", "group": "board", "color": "#3f9a6a", "opacity": 1.0, **bm["pcb"]},
                       {"name": "Connectors (keep-out)", "group": "keepout", "color": "#5b8fc4", "opacity": 0.7,
                        **bm["parts"]},
                       {"name": "Active Cooler (approx.)", "group": "keepout", "color": "#d5ad83", "opacity": 0.6,
                        **bm["cooler"]}]
    return meshes
