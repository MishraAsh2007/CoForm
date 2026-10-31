r"""Web UI.  .venv\Scripts\python.exe -m streamlit run app.py

Pages: Run (pick brief + mode + budget, run, live board), Versions (pictures + checks per version),
Downloads (final STLs, .form files), Fit test (enter data/fit_test_results.yaml by hand), Benchmark.
"""
import json
import threading
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from cad.common import ROOT, load_config, print_settings
from core.board import Board

st.set_page_config(page_title="SLA enclosure agents", layout="wide")
cfg = load_config()
RUNS = ROOT / cfg["run"]["out_dir"]
BRIEFS = sorted((ROOT / "benchmark" / "briefs").glob("*.yaml"))
pr = print_settings(cfg)
st.sidebar.markdown(f"**Print:** {pr['printer']}, {pr['material']}, {pr['layer_thickness_mm']} mm layers")
page = st.sidebar.radio("Page", ["Run", "Versions", "Downloads", "Fit test", "Benchmark"])


def run_dirs():
    return sorted([d for d in RUNS.glob("*") if (d / "board.sqlite").exists()], key=lambda d: d.stat().st_mtime,
                  reverse=True)


def pick_run(key):
    dirs = run_dirs()
    if not dirs:
        st.info("No runs yet.")
        return None
    default = st.session_state.get("active_run")
    names = [d.name for d in dirs]
    idx = names.index(default) if default in names else 0
    return RUNS / st.selectbox("Run", names, index=idx, key=key)


def board_view(d):
    b = Board(d / "board.sqlite")
    run = b.run(1)
    s = d / "summary.json"
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", run["status"])
    c2.metric("Versions", len(b.versions(1)))
    c3.metric("Evaluations", len({c["version_id"] for c in b.checks(1)}))
    c4.metric("Est. cost", f"${b.usage_total(1)['cost_usd']:.4f}")
    if run["mocked"]:
        st.warning("MOCK run: rule-based fake model, mock PreForm. Numbers are not from a real model or PreForm.")
    if s.exists():
        summ = json.loads(s.read_text(encoding="utf-8"))
        st.success(f"Done: {summ['stop_reason']}; final failures {summ['final_failures']}")
    dec = {x["version_id"]: x for x in b.decisions(1)}
    rows = []
    for v in b.versions(1):
        cs = {c["name"]: c for c in b.checks(1, v["id"])}
        rows.append({"version": v["id"], "author": v["author"], "status": dec.get(v["id"], {}).get("decision", v["status"]),
                     "reason": v["reason"][:120],
                     **{n: ("pass" if c["passed"] else f"{c['failures']} fail") + (" (MOCK)" if c["mocked"] else "")
                        for n, c in cs.items()}})
    st.subheader("Edits")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.subheader("Board messages")
    msgs = pd.DataFrame([{k: m[k] for k in ("created_at", "version_id", "author", "kind", "text")} for m in b.messages(1)])
    st.dataframe(msgs, hide_index=True, width="stretch", height=320)


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
    mock = c[5].checkbox("Mock (no API cost)", value=True)
    real_pf = c[5].checkbox("Real PreForm in loop", value=False)
    st.caption(info.get("description", ""))
    if st.button("Run", type="primary"):
        import run as runner
        name = f"{info['name']}__{mode}__{clear}__s{seed}{'__mock' if mock else ''}"
        st.session_state["active_run"] = name
        t = threading.Thread(target=runner.run, kwargs=dict(mode=mode, brief_path=str(brief), budget=int(budget),
                                                             seed=int(seed), mock=mock, clearances=clear,
                                                             mock_preform=not real_pf, quiet=True), daemon=True)
        t.start()
        st.session_state["thread"] = t

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
        vs = b.versions(1)
        v = st.select_slider("Version", [x["id"] for x in vs], value=vs[-1]["id"])
        ver = next(x for x in vs if x["id"] == v)
        st.markdown(f"**v{v} by {ver['author']}**: {ver['reason']}")
        if ver["patch"]:
            st.code(json.dumps(ver["patch"], indent=1), language="json")
        arts = {a["kind"]: a["path"] for a in b.artifacts(1, v)}
        cols = st.columns(4)
        for col, kind in zip(cols, ["assembled", "exploded", "base_top", "lid_bottom"]):
            if kind in arts and Path(arts[kind]).exists():
                col.image(arts[kind], caption=kind)
        for c in b.checks(1, v):
            with st.expander(f"{c['name']}: {'pass' if c['passed'] else str(c['failures']) + ' failures'}"
                             + (" (MOCK)" if c["mocked"] else "")):
                for f in c["details"].get("failures", []):
                    st.write(f"- {f['msg']}")
                st.json(c["details"].get("metrics", {}))

elif page == "Downloads":
    st.title("Downloads")
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
        for col, (k, v) in zip(cols, list(vals.items())):
            choice = col.selectbox(str(k), words, index=words.index(v) if v in words else 0, key=f"{section}_{k}")
            vals[k] = choice or None
    if st.button("Save", type="primary"):
        header = "".join(l + "\n" for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("#"))
        data["meta"] = meta
        path.write_text(header + "\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        from checks.fit_rules import rules
        st.success("Saved.")
        st.write(rules(cfg, "measured")["notes"])

elif page == "Benchmark":
    st.title("Benchmark")
    out = ROOT / "results" / "benchmark"
    for md in sorted(out.glob("*_summary.md")):
        st.markdown(md.read_text(encoding="utf-8"))
        tag = md.name.replace("_summary.md", "")
        cols = st.columns(3)
        for col, k in zip(cols, ["final_failures", "evals_to_pass", "cost"]):
            p = out / f"{tag}_{k}.png"
            if p.exists():
                col.image(str(p))
