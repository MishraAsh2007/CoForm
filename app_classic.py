r"""CLASSIC web UI (fallback).  .venv\Scripts\python.exe -m streamlit run app_classic.py

Pages: Run (live board) | Versions (3D viewer, spec diff, checks, PreForm, why-this-design) | Downloads |
Charts (computed by code from logged data) | Tools (thickness heat map, PreForm orientations) | Fit test
"""
import json
import threading
from pathlib import Path

import jsonpatch
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import trimesh
import yaml

from cad.common import ROOT, load_config, print_settings
from core.board import Board

st.set_page_config(page_title="SLA enclosure agents", layout="wide")
cfg = load_config()
RUNS = ROOT / cfg["run"]["out_dir"]
BENCH = ROOT / "results" / "benchmark"
BRIEFS = sorted((ROOT / "benchmark" / "briefs").glob("*.yaml"))
pr = print_settings(cfg)
st.sidebar.markdown(f"**Print:** {pr['printer']}, {pr['material']}, {pr['layer_thickness_mm']} mm layers")
page = st.sidebar.radio("Page", ["Run", "Versions", "Downloads", "Charts", "Tools", "Fit test"])


def run_dirs():
    roots = [RUNS] + sorted(p for p in BENCH.glob("runs*") if p.is_dir())
    dirs = [d for r in roots if r.exists() for d in r.iterdir() if (d / "board.sqlite").exists()]
    return sorted(dirs, key=lambda d: d.stat().st_mtime, reverse=True)


def pick_run(key):
    dirs = run_dirs()
    if not dirs:
        st.info("No runs yet.")
        return None
    names = [f"{d.parent.name}/{d.name}" for d in dirs]
    active = st.session_state.get("active_run")
    idx = next((i for i, n in enumerate(names) if active and n.endswith(active)), 0)
    return dirs[names.index(st.selectbox("Run", names, index=idx, key=key))]


def mesh_trace(path, color, name, opacity=1.0, dz=0.0):
    m = trimesh.load(str(path))
    v, f = m.vertices, m.faces
    return go.Mesh3d(x=v[:, 0], y=v[:, 1], z=v[:, 2] + dz, i=f[:, 0], j=f[:, 1], k=f[:, 2], color=color,
                     opacity=opacity, name=name, flatshading=True, showlegend=True)


def box_traces(comp):
    import numpy as np
    out = []
    for k in comp["keepouts"]:
        x0, x1, y0, y1, z0, z1 = k["box"]
        xs = np.array([x0, x1, x1, x0, x0, x1, x1, x0])
        ys = np.array([y0, y0, y1, y1, y0, y0, y1, y1])
        zs = np.array([z0, z0, z0, z0, z1, z1, z1, z1])
        out.append(go.Mesh3d(x=xs, y=ys, z=zs, i=[0, 0, 4, 4, 0, 0, 1, 1, 0, 0, 3, 3],
                             j=[1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 2, 6], k=[2, 3, 6, 7, 5, 4, 6, 5, 7, 4, 6, 7],
                             color="#2e8b57" if k["name"] == "pcb" else "#4a90d9", opacity=0.35, name=k["name"],
                             showlegend=False, hovertext=k["name"], hoverinfo="text"))
    return out


def board_view(d):
    b = Board(d / "board.sqlite")
    run = b.run(1)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", run["status"])
    c2.metric("Versions", len(b.versions(1)))
    c3.metric("Evaluations", len({c["version_id"] for c in b.checks(1) if c["name"] == "assembly"}))
    c4.metric("Est. cost", f"${b.usage_total(1)['cost_usd']:.4f}")
    if run["mocked"]:
        st.warning("MOCK run: rule-based fake model. Its numbers are not from a real model.")
    s = d / "summary.json"
    if s.exists():
        summ = json.loads(s.read_text(encoding="utf-8"))
        if "quality" in summ:
            st.success(f"Done: {summ['stop_reason']}; passed={summ['final_passed']}; quality {summ['quality']['score']}; "
                       f"final failures {summ['final_failures']}; final PreForm "
                       f"{'MOCK' if summ['mocked_preform'] else 'real'}")
    dec = {x["version_id"]: x for x in b.decisions(1)}
    rows = []
    for v in b.versions(1):
        cs = {c["name"]: c for c in b.checks(1, v["id"])}
        row = {"version": v["id"], "author": v["author"], "status": dec.get(v["id"], {}).get("decision", v["status"]),
               "reason": v["reason"][:120]}
        for n in ("assembly", "printability", "preform", "fit"):
            if n in cs:
                row[n] = ("pass" if cs[n]["passed"] else f"{cs[n]['failures']} fail") + (" (MOCK)" if cs[n]["mocked"] else "")
        if "quality" in cs:
            row["quality"] = cs["quality"]["details"]["metrics"].get("score")
        rows.append(row)
    st.subheader("Edits")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.subheader("Board messages")
    msgs = pd.DataFrame([{k: m[k] for k in ("created_at", "version_id", "author", "kind", "text")} for m in b.messages(1)])
    st.dataframe(msgs, hide_index=True, width="stretch", height=320)


def why_panel(b, vid, mocked_run):
    from core.trail import ai_summary, trail
    entries, numbers = trail(b, vid)
    st.markdown("**Decision trail** (from the log, in order)")
    for e in entries:
        st.markdown(f"<a id='{e['id']}'></a>**[{e['id']}] {e['kind']}** by *{e['who']}*: {e['text']}",
                    unsafe_allow_html=True)
    st.caption("Numbers from the log: " + ", ".join(f"{k}={v}" for k, v in numbers.items()))
    if st.button("Write AI summary", key=f"sum_{vid}"):
        llm = None
        if not mocked_run:
            from core.llm import LLM
            try:
                llm = LLM(cfg)
            except RuntimeError as e:
                st.error(str(e))
        st.session_state[f"sum_{vid}"] = ai_summary(b, vid, llm, mock=llm is None)
    s = st.session_state.get(f"sum_{vid}") or next(
        (json.loads(a["path"]) for a in b.artifacts(1, vid) if a["kind"] == "ai_summary"), None)
    if s:
        label = "AI summary" + (" (MOCK template)" if s.get("mocked") else "") + (
            " - FLAGGED: model wrote digits, removed" if s.get("flagged") else "")
        links = " ".join(f"[{e}](#{e})" for e in s["entries_cited"])
        st.info(f"**{label}**: {s['text']}\n\nSummarizes: {links}. Every number was filled in from the log, not "
                f"written by the model.")


if page == "Run":
    st.title("Design run")
    c = st.columns(6)
    brief = c[0].selectbox("Brief", BRIEFS, format_func=lambda p: p.stem)
    info = yaml.safe_load(brief.read_text(encoding="utf-8"))
    c[0].caption(f"component: {info['bundle']}")
    mode = c[1].selectbox("Mode", ["collective", "generalist", "one_shot"])
    budget = c[2].number_input("Budget (evaluations)", 1, 50, cfg["run"]["default_budget"])
    seed = c[3].number_input("Seed", 0, 99, 0)
    clear = c[4].selectbox("Clearances", ["generic", "measured"])
    mock = c[5].checkbox("Mock model (no API cost)", value=True)
    model = c[5].selectbox("Model", ["claude-sonnet-5-5", "claude-haiku-5-5"], disabled=mock)
    st.caption(info.get("description", "") + " Every mode starts from the same naive spec.")
    if st.button("Run", type="primary"):
        import run as runner
        name = f"{info['name']}__{mode}__{clear}__s{seed}{'__mock' if mock else ''}{'' if mock else '__' + model}"
        st.session_state["active_run"] = name
        threading.Thread(target=runner.run, kwargs=dict(mode=mode, brief_path=str(brief), budget=int(budget),
                                                        seed=int(seed), mock=mock, clearances=clear, quiet=True,
                                                        model=None if mock else model), daemon=True).start()

    @st.fragment(run_every=3)
    def live():
        d = pick_run("live_run")
        if d:
            board_view(d)
    live()

elif page == "Versions":
    st.title("Design versions")
    d = pick_run("ver_run")
    if d:
        b = Board(d / "board.sqlite")
        run = b.run(1)
        vs = b.versions(1)
        ids = [x["id"] for x in vs]
        if not ids:
            st.info("This run has no versions (it stopped before the first design was logged). Pick another run.")
            st.stop()
        v = st.select_slider("Version", ids, value=ids[-1]) if len(ids) > 1 else ids[0]
        ver = b.version(v)
        st.markdown(f"**v{v} by {ver['author']}** ({ver['status']}): {ver['reason']}")
        tabs = st.tabs(["3D view", "Spec diff", "Checks", "PreForm", "Why this design"])
        arts = {a["kind"]: a["path"] for a in b.artifacts(1, v)}
        with tabs[0]:
            c1, c2, c3 = st.columns(3)
            show_base, show_lid = c1.checkbox("base", True), c2.checkbox("lid", True)
            show_ko = c3.checkbox("board keep-out", False)
            explode = st.slider("lift lid (mm)", 0, 60, 30)
            traces = []
            if show_base and arts.get("base") and Path(arts["base"]).exists():
                traces.append(mesh_trace(arts["base"], "#c8c8c8", "base"))
            if show_lid and arts.get("lid") and Path(arts["lid"]).exists():
                traces.append(mesh_trace(arts["lid"], "#9ec5e8", "lid", 0.8, explode))
            if show_ko:
                from cad.components import load_bundle
                traces += box_traces(load_bundle(ver["spec"].get("bundle", "rpi5_cooler")))
            if traces:
                fig = go.Figure(traces)
                fig.update_layout(scene=dict(aspectmode="data"), height=650, margin=dict(l=0, r=0, t=0, b=0))
                st.plotly_chart(fig, width="stretch")
            else:
                st.info("No STLs for this version (not evaluated, or a joint edit rejected before evaluation).")
        with tabs[1]:
            other = st.selectbox("Compare with version", ids, index=max(0, ids.index(v) - 1))
            a, bspec = b.version(other)["spec"], ver["spec"]
            diff = jsonpatch.make_patch(a, bspec).patch
            st.dataframe(pd.DataFrame([{"op": o["op"], "path": o["path"],
                                        f"v{other}": json.dumps(jsonpatch.JsonPointer(o["path"]).resolve(a, None))[:80],
                                        f"v{v}": json.dumps(o.get("value"))[:80]} for o in diff]),
                         hide_index=True, width="stretch")
            left, right = st.columns(2)
            left.markdown(f"**v{other}**")
            left.json(a, expanded=False)
            right.markdown(f"**v{v}**")
            right.json(bspec, expanded=False)
        with tabs[2]:
            for c in b.checks(1, v):
                if c["name"].startswith("quality"):
                    st.markdown(f"**{c['name']}**")
                    st.json(c["details"]["metrics"])
                    continue
                with st.expander(f"{c['name']}: {'pass' if c['passed'] else str(c['failures']) + ' failures'}"
                                 + (" (MOCK)" if c["mocked"] else "")):
                    for f in c["details"].get("failures", []):
                        st.write(f"- {f['msg']}")
                    st.json(c["details"].get("metrics", {}))
        with tabs[3]:
            pfs = [c for c in b.checks(1, v) if c["name"].startswith("preform")]
            for c in pfs:
                det = c["details"]
                kind = {"preform": "in-loop local estimate (MOCK)", "preform_real": "real PreForm (requested)",
                        "preform_final": "final design PreForm"}.get(c["name"], c["name"])
                st.markdown(f"**{kind}**{' - MOCK' if det.get('mocked') else ''}")
                st.write({"warnings": [f["msg"] for f in det.get("failures", [])], **det.get("metrics", {})})
            if not pfs:
                st.info("No PreForm result for this version.")
            pj = ROOT / "results" / "print" / "formlabs" / "enclosure_a.json"
            if v == ids[-1] and pj.exists():
                job = json.loads(pj.read_text(encoding="utf-8"))
                if any(d.name in p for p in job.get("parts", [])):
                    st.markdown("**Print job (real PreForm): final design, base + lid on one platform, orientation "
                                "chosen per part** (`results/print/formlabs/enclosure_a.form`)")
                    st.write({"attempts": [f"{a['strategy']}: {[w['description'] for w in a['warnings']] or 'no warnings'}"
                                           for a in job["attempts"]],
                              "orientation": job.get("orientation_chosen"), "warnings": job["warnings"],
                              "print_time_min": round(job["print_time_s"] / 60, 1),
                              "resin_ml_incl_supports": round(job["volume_ml"], 1), **job["print_settings"]})
        with tabs[4]:
            why_panel(b, v, bool(run["mocked"]))

elif page == "Downloads":
    st.title("Downloads")
    pr = ROOT / "results" / "print"
    pkg = [(pr / "formlabs" / "enclosure_a.form", "Formlabs .form (PreForm, Form Studio 1, Alpha Fast V1 0.15 mm; 0 warnings)"),
           (pr / "stl_sla.zip", "SLA STL set (base.stl, lid.stl: exact geometry in the .form)"),
           (pr / "fdm.zip", "FDM STL set for a Creality 0.4 mm / PLA (pre-rotated; NOT checked by PreForm)"),
           (pr / "onshape_step.zip", "STEP zip for Onshape (base, lid, Pi 5 board, Active Cooler keep-out APPROX, assembly)")]
    if any(p.exists() for p, _ in pkg):
        st.subheader("Print package: print candidate (a_pi5_standard, collective, Sonnet)")
        st.caption("Which file for which printer or tool: results/print/README.md")
        for p, label in pkg:
            if p.exists():
                st.download_button(f"{label} ({p.stat().st_size / 1e6:.1f} MB)", p.read_bytes(), file_name=p.name,
                                   key=f"pkg_{p.name}")
        st.divider()
        st.subheader("Files of one run")
    d = pick_run("dl_run")
    if d:
        ps = d / "PRINT_SETTINGS.json"
        if ps.exists():
            st.info("Print with: " + ", ".join(f"{k} {v}" for k, v in json.loads(ps.read_text()).items() if k != "note"))
        for f in sorted(list(d.glob("*.stl")) + list(d.glob("*.form")) + [d / "log.md", d / "final_spec.json"]):
            if f.exists():
                st.download_button(f"Download {f.name}", f.read_bytes(), file_name=f"{d.name}_{f.name}")
        if (d / "log.md").exists():
            st.markdown((d / "log.md").read_text(encoding="utf-8"))

elif page == "Charts":
    st.title("Charts (computed by code from logged data)")
    from benchmark import metrics
    roots = [p for p in sorted(BENCH.glob("runs*")) if p.is_dir()] + [RUNS]
    root = st.selectbox("Run set", roots, format_func=lambda p: p.name)
    rows = [metrics.run_data(x) for x in sorted(root.iterdir()) if (x / "summary.json").exists()
            and "quality" in json.loads((x / "summary.json").read_text(encoding="utf-8"))]
    if not rows:
        st.info("No runs with quality scores in this set.")
    else:
        rows.sort(key=metrics.rank_key)  # same ranking as metrics.collect (fixes KeyError: 'rank')
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        st.dataframe(pd.DataFrame(metrics.table(rows)), hide_index=True, width="stretch")
        for name, fn in metrics.CHARTS.items():
            fig, cap = fn(rows)
            st.pyplot(fig)
            st.caption(cap)

elif page == "Tools":
    st.title("Manufacturing tools (not used in the benchmark)")
    d = pick_run("tool_run")
    if d:
        part = st.radio("Part", ["base", "lid"], horizontal=True)
        stl = d / f"{part}.stl"
        c1, c2 = st.columns(2)
        if c1.button("Wall-thickness heat map"):
            from checks.thickness_map import heatmap
            r = heatmap(stl, d / f"{part}_thickness.png", cfg)
            c1.image(r["png"])
            c1.write({k: val for k, val in r.items() if k != "png"})
        if c2.button("Compare orientations in PreForm (2-6 min)"):
            from checks.orientations import best, compare
            rows = compare(stl, cfg)
            c2.dataframe(pd.DataFrame(rows), hide_index=True)
            c2.success(f"Best by cups, then supports, then time: {best(rows)['name']}"
                       + (" (MOCK estimates)" if rows[0]["mocked"] else ""))

elif page == "Fit test":
    st.title("Fit test results")
    st.caption("Fill in after printing results/test_print (see HOW_TO_TEST.md). Saved to data/fit_test_results.yaml.")
    path = ROOT / "data" / "fit_test_results.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    words = ["", "free", "snug", "press", "no_fit", "cracked", "failed_to_print"]
    meta = data.get("meta", {})
    m1, m2, m3 = st.columns(3)
    meta["printed_on"] = m1.text_input("Printed on", meta.get("printed_on", ""))
    meta["orientation"] = m2.text_input("Orientation", meta.get("orientation", ""))
    meta["post_cure"] = m3.text_input("Post-cure", meta.get("post_cure", ""))
    meta["notes"] = st.text_area("Notes", meta.get("notes", ""))
    st.caption(f"Material/layer recorded: {meta.get('resin')} / {meta.get('layer_thickness_mm')} mm "
               f"(config: {pr['material']} / {pr['layer_thickness_mm']} mm)")
    for section, vals in data.items():
        if section == "meta":
            continue
        st.subheader(section)
        cols = st.columns(len(vals))
        for col, (k, val) in zip(cols, list(vals.items())):
            choice = col.selectbox(str(k), words, index=words.index(val) if val in words else 0, key=f"{section}_{k}")
            vals[k] = choice or None
    if st.button("Save", type="primary"):
        header = "".join(line + "\n" for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("#"))
        data["meta"] = meta
        path.write_text(header + "\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        from checks.fit_rules import rules
        st.success("Saved.")
        st.write(rules(cfg, "measured")["notes"])
