"""The pages of the new UI. Every number shown comes from a run log or a result file."""
import io
import json
import os
import threading
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from cad.common import ROOT, print_settings
from ui import data as D
from ui import theme as T
from ui import viewer as V

PR = print_settings(D.CFG)
SETTINGS = f"{PR['printer']} · {PR['material']} · {PR['layer_thickness_mm']} mm layers"


# ---------------- shared ----------------

def run_picker():
    dirs = D.run_dirs()
    if not dirs:
        return None
    keys = [str(d) for d in dirs]
    if "run_pick" not in st.session_state or st.session_state["run_pick"] not in keys:
        want = st.query_params.get("run") or st.session_state.get("active_run") or D.BEST
        st.session_state["run_pick"] = next((k for k in keys if Path(k).name == want), keys[0])
    with st.sidebar:
        T.html_block('<div class="sc-brand">SCIENCECLAW</div>')
        st.selectbox("Run shown on every page", keys, key="run_pick", format_func=lambda k: D.run_label(Path(k)),
                     help="The ★ print candidate is pre-selected. Runs you start on the Run page appear here.")
        st.caption(f"Print setting: {SETTINGS}")
    return Path(st.session_state["run_pick"])


def run_bar(R):
    s = R["summary"]
    run = R["run"]
    bits = [f"<b>{T.esc(D.run_label(Path(R['dir'])))}</b>"]
    bits.append(T.pill("MOCK model", "mock") if run["mocked"] else T.pill("REAL model: " + str(run["model"]), "real"))
    if s:
        bits.append(T.pill("ALL CHECKS PASS", "pass") if s["final_passed"] else
                    T.pill(f"{sum(s['final_failures'].values())} check failure(s) left", "fail"))
        if s.get("experimental"):
            bits.append(T.pill("EXPERIMENTAL", "unv"))
    else:
        bits.append(T.pill(f"status: {run['status']}", "neutral"))
    T.html_block('<div class="sc-runbar">Run: ' + " ".join(bits) + "</div>")


def need_run():
    d = run_picker()
    if d is None:
        st.info("No runs yet. Start one on the Run page (Mock mode needs no API key).")
        st.stop()
    R = D.get_run(d)
    if not R["versions"]:
        st.info("This run has no design versions yet (it is starting, or it stopped early). Pick another run in "
                "the sidebar.")
        st.stop()
    return R


def final_vid(R):
    s = R["summary"]
    if s and s.get("final_version"):
        return s["final_version"]
    acc = [v["id"] for v in R["versions"] if v["status"] in ("accepted", "baseline")]
    return acc[-1] if acc else R["versions"][-1]["id"]


def version_label(R, vid):
    v = next(x for x in R["versions"] if x["id"] == vid)
    dec = D.decision_of(R, vid)
    st_ = (dec or {}).get("decision", v["status"])
    who = D.AGENTS.get(v["author"], {}).get("title", v["author"])
    what = ", ".join(D.plain_patch(v["patch"])[:2]) or ("naive starting design" if st_ == "baseline" else "edit")
    return f"v{vid} · {who} · {what} ({st_})"


# ---------------- Home ----------------

def home():
    T.html_block('<div class="sc-brand">SCIENCECLAW · ENCLOSURE AGENTS</div>')
    st.title("AI agents that design a print-ready Raspberry Pi 5 case")
    T.html_block(
        '<p class="sc-lead">Four specialist AI agents (Design, Assembly, Manufacturing and Fit) design a 3D-printed '
        'case for a Raspberry Pi 5 together, with no boss. Each agent owns one real engineering tool and only its own '
        'part of the design; when a fix belongs to someone else, it asks that agent. The result is a file that '
        'prints on a Formlabs printer, checked before printing, with every decision traceable in the log.</p>')
    cols = st.columns(3)
    steps = [("1", "Pick what to design", "Choose a board and who designs it. Mock mode is free and needs no API key.",
              "run", "Start a run"),
             ("2", "Watch the agents work", "See each problem, which agent fixed it, and the numbers, step by step.",
              "story", "Open the story"),
             ("3", "Get your print files", "Formlabs .form, STL for any resin printer, FDM STL, STEP for Onshape.",
              "downloads", "Go to downloads")]
    for c, (n, h, p, page, link) in zip(cols, steps):
        with c:
            T.html_block(f'<div class="sc-step"><div class="n">{n}</div><h3>{T.esc(h)}</h3><p>{T.esc(p)}</p></div>')
            st.page_link(PAGES[page], label=f"{link} →", use_container_width=True)
    st.write("")
    dirs = {d.name: d for d in D.run_dirs()}
    if D.BEST not in dirs:
        return
    R = D.get_run(dirs[D.BEST])
    s = R["summary"]
    left, right = st.columns([1.05, 1])
    with left:
        st.subheader("Best run, pre-selected on every page")
        st.caption(D.run_label(dirs[D.BEST]))
        f0 = D.failures_of(R, R["versions"][0]["id"])
        fl = D.failures_of(R, final_vid(R))
        job = D.print_job()
        m = st.columns(2)
        m[0].metric("Local check failures", f"{f0} → {fl}", help="Assembly, printability, fit and the quick PreForm "
                    "estimate, from the run log: naive start vs final design")
        m[1].metric("Design checks used", f"{s['evaluations_used']} of {s['budget']}",
                    help="Full evaluations of a design (the budget)")
        if job:
            m = st.columns(3)
            m[0].metric("PreForm warnings (print job)", len(job["warnings"]), help="Real PreForm, orientation chosen "
                        "per part by the Manufacturing agent's orientation tool")
            m[1].metric("Print time", f"{job['print_time_s'] / 60:.1f} min")
            m[2].metric("Resin incl. supports", f"{job['volume_ml']:.1f} ml")
        T.html_block(" ".join([T.pill("REAL PreForm 3.64", "real"), T.pill("REAL model: Sonnet", "real"),
                               T.pill(SETTINGS, "neutral")]))
        T.html_block('<div class="sc-callout warn">Honest status: with PreForm\'s own auto-orient the final design '
                     'still has <b>1 resin-trapping cup</b>, so the run log says "not all passing". The print job then '
                     'chose the orientation per part: <b>0 warnings</b>. Fit on a real board is only confirmed by a '
                     'print: several part sizes are ' + T.unverified() + ' (see the Risk check on the Print page).'
                     '</div>')
    with right:
        img = next((p for p in (D.SUBMIT_IMG / "render_assembled.png", D.PRINT / "render_assembled.png")
                    if p.exists()), None)
        if img:
            st.image(str(img), caption="Final design with the Raspberry Pi 5 (lid see-through)",
                     use_container_width=True)


# ---------------- Run ----------------

def _has_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    env = ROOT / ".env"
    return env.exists() and "ANTHROPIC_API_KEY=" in env.read_text(encoding="utf-8", errors="ignore") and \
        any(line.startswith("ANTHROPIC_API_KEY=") and len(line.strip()) > len("ANTHROPIC_API_KEY=")
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines())


def run_page():
    run_picker()
    st.title("1 · Pick what to design")
    st.caption("Every mode starts from the same deliberately naive design and uses the same tools and budget.")
    briefs = {p.stem: p for p in D.BRIEFS}
    c1, c2 = st.columns([1.3, 1])
    with c1:
        b = st.selectbox("What to design", list(briefs), format_func=lambda k: D.BRIEF_PLAIN.get(k, k),
                         help="A brief: the board, the requirements (ports, screws, vents, tabs) and the printer.")
        info = D.brief_info(briefs[b])
        st.caption(info.get("description", ""))
        mode = st.radio("Who designs it", list(D.MODES), format_func=D.MODES.get, horizontal=False,
                        help="The team is the project's idea. The other two are baselines for a fair comparison.")
    with c2:
        budget = st.slider("Budget: how many full design checks", 1, 16, 4,
                           help="Each check rebuilds the CAD and runs assembly, printability, fit and PreForm checks. "
                                "4 takes a few minutes.")
        mock = st.toggle("Mock model (free, no API key)", value=True,
                         help="A rule-based stand-in for the AI model. Everything it produces is labelled MOCK.")
        model = st.selectbox("AI model", ["claude-sonnet-5-5", "claude-haiku-5-5"], disabled=mock,
                             help="Same model for every agent in a run. Costs money (API key in .env).")
        with st.expander("Advanced"):
            seed = st.number_input("Seed", 0, 99, 0, help="Changes the random order of agent turns.")
            clear = st.selectbox("Clearances from", ["generic", "measured"],
                                 format_func={"generic": "Formlabs design guide",
                                              "measured": "our printed test plate (empty now: falls back to the guide)"}.get)
    if "freeform" in b:
        T.html_block(T.pill("EXPERIMENTAL", "unv") + " Free-form part: the Design agent writes CadQuery code.")
    if not mock and not _has_key():
        st.warning("No ANTHROPIC_API_KEY found in .env. Turn Mock on, or add a key.")
    if st.button("▶ Start the run", type="primary", disabled=(not mock and not _has_key())):
        import time
        name = f"{info['name']}__{mode}__{clear}__s{seed}{'__mock' if mock else ''}{'' if mock else '__' + model}"
        out = D.RUNS / name
        try:
            if out.exists():  # keep the old run: move it aside in one step (never half-delete it)
                out.rename(out.with_name(f"{name}__prev_{time.strftime('%Y%m%d_%H%M%S')}"))
        except OSError as ex:
            st.error(f"An earlier run with the same name is in use and could not be moved aside ({ex}). Change the "
                     "seed in Advanced, or restart the app.")
            st.stop()
        st.session_state["active_run"] = name
        st.session_state["launched"] = {"name": name, "budget": int(budget), "t": time.time()}
        st.session_state.pop("run_pick", None)
        kw = dict(mode=mode, brief_path=str(briefs[b]), budget=int(budget), seed=int(seed), mock=mock,
                  clearances=clear, quiet=True, model=None if mock else model)
        threading.Thread(target=_launch, args=(name, kw), daemon=True).start()
    if st.session_state.get("launched"):
        live_progress()


RUN_ERRORS = {}


def _launch(name, kw):
    import run as runner
    try:
        runner.run(**kw)
    except Exception as ex:  # noqa: BLE001 - shown on the page
        RUN_ERRORS[name] = f"{type(ex).__name__}: {ex}"


@st.fragment(run_every=2)
def live_progress():
    L = st.session_state["launched"]
    d = D.RUNS / L["name"]
    st.subheader("Progress")
    if L["name"] in RUN_ERRORS:
        st.error("The run stopped with an error: " + RUN_ERRORS[L["name"]])
        return
    if not (d / "board.sqlite").exists():
        st.info("Starting… (building the naive design)")
        return
    R = D.get_run(d)
    from datetime import datetime
    try:
        started = datetime.fromisoformat(R["run"]["started_at"]).timestamp()
    except (TypeError, ValueError):
        started = L["t"]
    if started < L["t"] - 5:
        st.info("Starting… (building the naive design)")
        return
    evals = len(D.evaluated_versions(R))
    done = R["run"]["status"] != "running"
    budget = R["run"]["budget"] or L["budget"]
    st.progress(min(1.0, evals / max(1, budget + 1)) if not done else 1.0,
                text=("Finished: " + (R["run"]["notes"] or R["run"]["status"])) if done else
                f"Running · {evals} design version(s) checked · budget {budget}")
    run_bar(R)
    stats = D.agent_stats(R)
    cols = st.columns(4)
    for c, (a, meta) in zip(cols, D.AGENTS.items()):
        s = stats[a]
        c.markdown(f"**{meta['icon']} {meta['title']}**  \n{s['accepted']} accepted · {s['rejected']} rejected · "
                   f"{s['needs_posted']} needs posted")
    msgs = [m for m in R["messages"] if m["kind"] != "breakdown"][-6:]
    for m in reversed(msgs):
        st.markdown(f"- **{m['author']}** ({m['kind']}): {m['text'].split(' | patch')[0].split(' | suggest')[0][:180]}")
    if done:
        st.success("Done. Show this run on every page, then open Story or 3D.")
        st.button("Show this run on every page", on_click=_select_run, args=(str(d),), type="primary")


def _select_run(path):
    st.session_state["run_pick"] = path


# ---------------- Agents ----------------

def agents_page():
    R = need_run()
    st.title("2 · Watch the agents work")
    run_bar(R)
    st.caption("No boss: each agent owns part of the design and one tool. A fix in someone else's part becomes a "
               "request (a 'need') to that owner. An edit is kept only if no check gets worse.")
    stats = D.agent_stats(R)
    cols = st.columns(4)
    for c, (a, meta) in zip(cols, D.AGENTS.items()):
        s = stats[a]
        did = "".join(f"<li>v{vid}: {T.esc(t)}</li>" for vid, t in s["changes"]) or "<li>No accepted edits</li>"
        extra = []
        if s["rejected"]:
            extra.append(f"{s['rejected']} edit(s) rejected by the protocol")
        if s["needs_posted"]:
            extra.append(f"posted {s['needs_posted']} need(s) to other agents")
        if s["needs_received"]:
            extra.append(f"received {s['needs_received']} need(s)")
        if s["declines"]:
            extra.append(f"declined {s['declines']} need(s) with numbers")
        if s["preform"]:
            extra.append(f"ran real PreForm {s['preform']} time(s) in the loop")
        if a == "design" and not s["proposed"]:
            extra.append("its keys were already right, so it passed every turn")
        with c:
            T.html_block(
                f'<div class="sc-agent {a}"><div class="top"><div class="av">{meta["icon"]}</div><div><b>{meta["title"]}'
                f'</b><small>{T.esc(meta["role"])}</small></div></div>'
                f'<div class="lbl">OWNS</div><p>{T.esc(meta["owns"])}</p>'
                f'<div class="lbl">TOOL</div><p>{T.esc(meta["tool"])}</p>'
                f'<div class="lbl">IN THIS RUN</div><p>{s["accepted"]} accepted · {s["rejected"]} rejected</p>'
                f'<ul>{did}</ul><p style="color:#99abae">{T.esc("; ".join(extra))}</p></div>')
    st.write("")
    st.subheader("The shared board")
    st.caption("Every message the agents posted, in order (newest last). Notes where an agent decided to pass are "
               "hidden here; see Details.")
    for m in R["messages"]:
        if m["kind"] in ("need", "decline", "propose") or (m["kind"] == "note" and not m["text"].startswith("pass")):
            who = D.AGENTS.get(m["author"], {}).get("title", m["author"])
            badge = {"need": T.pill("NEED", "unv"), "decline": T.pill("DECLINE", "fail"),
                     "propose": T.pill("PROPOSAL", "pass"), "note": T.pill("NOTE", "neutral")}[m["kind"]]
            v = f" · v{m['version_id']}" if m["version_id"] else ""
            T.html_block(f'<div style="margin:6px 0">{badge} <b>{T.esc(who)}</b>'
                         f'<span style="color:#99abae">{v}</span>: {T.esc(m["text"][:400])}</div>')
    with st.expander("Details: every board message (raw)"):
        st.dataframe(pd.DataFrame([{k: m[k] for k in ("created_at", "version_id", "author", "kind", "text")}
                                   for m in R["messages"]]), hide_index=True, use_container_width=True, height=360)
        st.caption(f"Model usage: {R['usage']['input_tokens']} tokens in, {R['usage']['output_tokens']} out, "
                   f"est. ${R['usage']['cost_usd']}")


# ---------------- Story ----------------

def story_page():
    R = need_run()
    st.title("The story of this run")
    run_bar(R)
    steps = D.story(R)
    acc = [s for s in steps if s["status"] == "accepted"]
    rej = [s for s in steps if s["status"] == "rejected"]
    f0 = D.failures_of(R, steps[0]["vid"])
    ff = D.failures_of(R, final_vid(R))
    T.html_block(f'<p class="sc-lead">From <b>{f0}</b> problems in the naive design to <b>{ff}</b> in the final one: '
                 f'{len(acc)} edits kept, {len(rej)} rejected by the rules. Each step: the problem found, who fixed it, '
                 f'and the result, with numbers from the log.</p>')
    for s in steps:
        who = D.AGENTS.get(s["author"], {}).get("title", s["author"])
        snap = D.artifact(R, s["vid"], "assembled")
        txt, img = st.columns([2.2, 1])
        with txt:
            if s["status"] == "baseline":
                g = D.group_fails([f for c in s["checks"].values() if c["name"] in ("assembly", "printability", "fit")
                                   for f in [x["msg"] for x in c["details"].get("failures", [])]])
                T.html_block(
                    f'<div class="sc-tl base"><h3>Start · v{s["vid"]}: the naive design</h3>'
                    f'<div class="row"><span class="k">PROBLEMS FOUND</span>{D.failures_of(R, s["vid"])} by the '
                    f'checks</div><div class="row"><span class="k">BIGGEST</span>'
                    + T.esc("; ".join(f"{k} ({n})" for k, n in g[:4])) + '</div></div>')
            else:
                probs = D.group_fails(s["problems"])
                needs = [n for n in s["needs"] if n["kind"] == "need"]
                need_txt = ""
                if needs:
                    tos = sorted({n["text"].split(":", 1)[0].replace("to ", "").split(" ")[0] for n in needs})
                    need_txt = (f'<div class="row"><span class="k">ASKED</span>{len(needs)} need(s) posted to '
                                f'{T.esc(", ".join(D.AGENTS.get(t, {}).get("title", t) for t in tos))}</div>')
                bd = s["breakdown"] or {"fixed": [], "new": []}
                res = (T.pill("KEPT", "pass") if s["status"] == "accepted" else T.pill("REJECTED", "fail"))
                nums = []
                if s["before"] is not None and s["after"] is not None:
                    nums.append(T.num("problems", f"{s['before']} → {s['after']}"))
                if s["q_before"] is not None and s["q_after"] is not None and s["before"] == 0:
                    nums.append(T.num("quality", f"{s['q_before']} → {s['q_after']}"))
                nums.append(T.num("fixed", len(bd.get("fixed", []))))
                if bd.get("new"):
                    nums.append(T.num("new problems", len(bd["new"])))
                problem = T.esc("; ".join(f"{k} ({n})" for k, n in probs[:3])) if probs else \
                    "all checks pass: improve the design (less resin, more margin)"
                cls = "" if s["status"] == "accepted" else " rej"
                T.html_block(
                    f'<div class="sc-tl{cls}"><h3>v{s["vid"]} · {T.esc(who)} {res}</h3>'
                    f'<div class="row"><span class="k">PROBLEM</span>{problem}</div>{need_txt}'
                    f'<div class="row"><span class="k">WHAT {T.esc(who.upper())} DID</span>'
                    f'{T.esc("; ".join(s["patch"]) or "edit")}</div>'
                    f'<div class="row"><span class="k">RESULT</span>{" ".join(nums)}</div>'
                    f'<div class="row" style="color:#99abae;font-size:12px">Rule: {T.esc(s["decision_reason"])}</div>'
                    f'</div>')
            with st.expander(f"Details v{s['vid']}: the agent's own reason, fixed / new problems, raw patch"):
                st.markdown(f"**Reason ({who}):** {s['reason']}")
                if s["breakdown"]:
                    st.json(s["breakdown"], expanded=False)
                st.code(json.dumps(s["raw_patch"], indent=1), language="json")
        with img:
            if snap:
                st.image(str(snap), use_container_width=True)
    if D.print_job_matches(R):
        job = D.print_job()
        T.html_block(
            '<div class="sc-tl"><h3>After the run · Manufacturing: print job ' + T.pill("REAL PreForm", "real") +
            '</h3><div class="row"><span class="k">PROBLEM</span>real PreForm auto-orient found '
            f'{T.esc("; ".join(w["description"] for w in job["attempts"][0]["warnings"]))}</div>'
            '<div class="row"><span class="k">WHAT IT DID</span>orientation tool compared flat / tilt 30° / tilt 45° '
            'per part in PreForm and picked ' + T.esc(", ".join(f"{k.replace('.stl', '')}: {v}" for k, v in
                                                                 job["orientation_chosen"].items())) + '</div>'
            f'<div class="row"><span class="k">RESULT</span>{T.num("warnings", len(job["warnings"]))}'
            f'{T.num("print time", f"{job["print_time_s"] / 60:.1f} min")}{T.num("resin", f"{job["volume_ml"]:.1f} ml")}'
            f'{T.num("platforms", 1)}</div></div>')


# ---------------- 3D ----------------

def viewer_page():
    R = need_run()
    st.title("3D views")
    run_bar(R)
    ev = [v for v in D.evaluated_versions(R) if D.artifact(R, v, "base")]
    if not ev:
        st.info("No 3D files for this run (the STLs are kept in results/cache, which is not in git). "
                "Pick the ★ print candidate.")
        return
    first, final = ev[0], final_vid(R) if final_vid(R) in ev else ev[-1]
    st.subheader("First design vs final design")
    st.caption("Both rotate together; the lid is lifted 30 mm so you can see inside (slider below). The naive start "
               "has no tabs, vents or cable slots, a low lid and short posts.")
    V.show([{"title": f"First (v{first}): naive start", "subtitle": f"{D.failures_of(R, first)} problems found",
             "meshes": D.scene(R, first)},
            {"title": f"Final (v{final})", "subtitle": f"{D.failures_of(R, final)} local problems left",
             "meshes": D.scene(R, final)}], height=400, lift=30, hidden=("keepout",))
    st.subheader("Final design: base, lid and assembled with the Pi")
    st.caption("Lift the lid with the slider. Tick Board keep-out to see connector and cooler zones the case must "
               "clear (from component data; cooler size approximate).")
    meshes = D.scene(R, final)
    V.show([{"title": "Base", "subtitle": "with the board inside",
             "meshes": [m for m in meshes if m["group"] != "lid"]},
            {"title": "Lid", "subtitle": "vent over the fan", "meshes": [m for m in meshes if m["group"] == "lid"]},
            {"title": "Assembled with the Raspberry Pi 5", "subtitle": "lid see-through", "meshes": meshes}],
           height=330, lift=25, hidden=("keepout",))
    st.subheader("Any version")
    v = st.select_slider("Version", ev, value=final, format_func=lambda x: f"v{x}")
    st.caption(version_label(R, v))
    V.show([{"meshes": D.scene(R, v)}], height=420, lift=20, hidden=("keepout",))


# ---------------- Why ----------------

def why_page():
    from core.trail import ai_summary, trail
    R = need_run()
    st.title("Why this design")
    run_bar(R)
    st.caption("Each version explains its own edit, built only from the log. The Story page shows the whole run.")
    ids = [v["id"] for v in R["versions"]]
    default = 3 if R["name"] == D.BEST and 3 in ids else final_vid(R)
    vid = st.selectbox("Version", ids, index=ids.index(default) if default in ids else len(ids) - 1,
                       format_func=lambda x: version_label(R, x))
    b = D.board(R["dir"])
    entries, numbers = trail(b, vid)
    ver = b.version(vid)
    who = D.AGENTS.get(ver["author"], {}).get("title", ver["author"])
    dec = numbers.get("decision")
    T.html_block(" ".join([T.pill(str(dec).upper(), "pass" if dec in ("accepted", "baseline") else "fail"),
                           T.num("problems before", numbers.get("failures_before")),
                           T.num("after", numbers.get("failures_after")), T.num("fixed", numbers.get("fixed")),
                           T.num("broke", numbers.get("broke")), T.num("objections", numbers.get("objections")),
                           T.num("quality", f"{numbers.get('quality_before')} → {numbers.get('quality_after')}")]))
    kinds = {"failed_check": ("PROBLEM", "fail"), "need": ("NEED", "unv"), "decline": ("DECLINE", "fail"),
             "proposal": ("PROPOSAL", "pass"), "object": ("OBJECTION", "fail"), "support": ("SUPPORT", "pass"),
             "breakdown": ("RESULT", "real"), "decision": ("DECISION", "neutral")}
    st.write("")
    RR = D.get_run(R["dir"])
    prev = [v["id"] for v in RR["versions"] if v["author"] == ver["author"] and v["id"] < vid]
    lo = min([x["id"] for x in RR["messages"] if x["version_id"] == prev[-1]] or [0]) if prev else 0
    hi = min([x["id"] for x in RR["messages"] if x["version_id"] == vid] or [10 ** 9])
    earlier = [m for m in RR["messages"] if m["kind"] == "need" and lo <= m["id"] < hi
               and m["text"].startswith(f"to {ver['author']}")]
    for m in earlier:
        T.html_block(f'<div style="margin:8px 0">{T.pill("EARLIER NEED", "unv")} <b>'
                     f'{T.esc(D.AGENTS.get(m["author"], {}).get("title", m["author"]))} → {T.esc(who)}</b> '
                     f'<span style="color:#99abae">(posted at v{m["version_id"]})</span> '
                     f'{T.esc(m["text"].split(": ", 1)[-1].split(" | suggest")[0][:300])}</div>')
    for e in entries:
        k, cls = kinds.get(e["kind"], (e["kind"].upper(), "neutral"))
        name = D.AGENTS.get(e["who"], {}).get("title", e["who"])
        text = e["text"]
        if e["kind"] == "proposal":
            text = text.split(" | patch")[0]
        if e["kind"] == "failed_check":
            g = D.group_fails([x for x in text.split("e.g. ", 1)[-1].split("; ")])
            text = text.split(", e.g.")[0] + ". For example: " + "; ".join(k2 for k2, _ in g)
        if e["kind"] == "breakdown":
            bd = D.breakdown(D.get_run(R["dir"]), vid) or {}
            fx = "; ".join(f"{k} ({n})" for k, n in D.group_fails(bd.get("fixed", [])))
            nw = "; ".join(f"{k} ({n})" for k, n in D.group_fails(bd.get("new", [])))
            text = (f"fixed {len(bd.get('fixed', []))}" + (f": {fx}" if fx else "")
                    + (f" · new {len(bd.get('new', []))}: {nw}" if bd.get("new") else "")
                    + (" · nothing fixed or broken (an improvement step)" if not bd.get("fixed") and not bd.get("new")
                       else ""))
        T.html_block(f'<div style="margin:8px 0">{T.pill(k, cls)} <b>{T.esc(name)}</b> '
                     f'<span style="color:#99abae">[{e["id"]}]</span> {T.esc(text)}</div>')
    st.write("")
    if st.button("✨ Write AI summary", help="2-3 plain sentences. The model may not write numbers: every number is "
                                            "filled in from the log, and any digit it writes is removed and flagged."):
        llm = None
        if not R["run"]["mocked"] and _has_key():
            from core.llm import LLM
            try:
                llm = LLM(D.CFG)
            except RuntimeError as ex:
                st.error(str(ex))
        st.session_state[f"sum_{R['name']}_{vid}"] = ai_summary(b, vid, llm, mock=llm is None)
    s = st.session_state.get(f"sum_{R['name']}_{vid}") or next(
        (json.loads(a["path"]) for a in b.artifacts(1, vid) if a["kind"] == "ai_summary"), None)
    if s:
        label = ("AI summary " + (T.pill("MOCK template", "mock") if s.get("mocked") else T.pill("REAL model", "real"))
                 + (T.pill("FLAGGED: digits removed", "fail") if s.get("flagged") else ""))
        T.html_block(f'<div class="sc-callout good">{label}<br>{T.esc(s["text"])}<br><span style="color:#99abae;'
                     f'font-size:12px">Cites {T.esc(", ".join(s["entries_cited"]))}. Numbers filled in from the log.'
                     f'</span></div>')
    with st.expander("Details: what changed in the design (spec diff)"):
        import jsonpatch
        parent = ver.get("parent_id")
        if parent:
            a = b.version(parent)["spec"]
            diff = jsonpatch.make_patch(a, ver["spec"]).patch
            st.dataframe(pd.DataFrame([{"setting": o["path"], f"v{parent}": json.dumps(
                jsonpatch.JsonPointer(o["path"]).resolve(a, None))[:80], f"v{vid}": json.dumps(o.get("value"))[:80]}
                for o in diff]), hide_index=True, use_container_width=True)
        st.json(ver["spec"], expanded=False)
    with st.expander("Details: every check on this version"):
        for c in b.checks(1, vid):
            st.markdown(f"**{D.CHECKS.get(c['name'], c['name'])}** "
                        + ("" if c["name"] == "quality" else ("pass" if c["passed"] else f"{c['failures']} failures"))
                        + (" (MOCK)" if c["mocked"] else ""))
            for f in c["details"].get("failures", []):
                st.write(f"- {f['msg']}")
            st.json(c["details"].get("metrics", {}), expanded=False)
    with st.expander("Details: raw decision trail"):
        st.json(entries, expanded=False)
        st.json(numbers, expanded=False)


# ---------------- Print ----------------

def print_page():
    R = need_run()
    st.title("Print result (Formlabs PreForm)")
    run_bar(R)
    job = D.print_job() if D.print_job_matches(R) else None
    if job:
        ps = job["print_settings"]
        T.html_block(T.pill("REAL PreForm 3.64.2", "real") + T.pill("0 WARNINGS" if not job["warnings"] else
                                                                    f"{len(job['warnings'])} WARNINGS",
                                                                    "pass" if not job["warnings"] else "fail"))
        m = st.columns(4)
        m[0].metric("PreForm warnings", len(job["warnings"]))
        m[1].metric("Print time", f"{job['print_time_s'] / 60:.1f} min", help="PreForm estimate")
        m[2].metric("Resin incl. supports", f"{job['volume_ml']:.1f} ml")
        m[3].metric("Build platforms", 1, help="Base and lid together on one platform")
        st.caption(f"{ps['printer']} · {ps['material']} ({ps['material_code']}) · {ps['layer_thickness_mm']} mm "
                   f"layers · file results/print/formlabs/enclosure_a.form")
        st.subheader("How the resin cup was fixed")
        a0 = job["attempts"][0]
        c = st.columns(3)
        with c[0]:
            T.html_block(f'<div class="sc-card"><p>STEP 1 · PreForm auto-orient</p><div class="big">'
                         f'{len(a0["warnings"])} warnings</div><p>{T.esc("; ".join(w["description"] for w in a0["warnings"]))}'
                         f'</p><p>Both parts auto-oriented together. The run&#39;s own final check (auto-orient) found the same cup in '
                         'the base, so the run log says "not all passing".</p></div>')
        with c[1]:
            T.html_block('<div class="sc-card"><p>STEP 2 · Manufacturing\'s orientation tool</p><div class="big">'
                         f'{sum(len(v) for v in job["orientation_compare"].values())} orientations</div><p>compared in '
                         'real PreForm: flat, tilted 30° and 45°, for base and lid (cups, supports, time)</p></div>')
        with c[2]:
            ch = ", ".join(f"{k.replace('.stl', '')}: {v}" for k, v in job["orientation_chosen"].items())
            T.html_block(f'<div class="sc-card"><p>STEP 3 · Chosen</p><div class="big">0 warnings</div><p>{T.esc(ch)}'
                         f'. Both parts on one platform, {job["print_time_s"] / 60:.1f} min, {job["volume_ml"]:.1f} ml.'
                         '</p></div>')
        rows = []
        for part, opts in job["orientation_compare"].items():
            for o in opts:
                rows.append({"part": part.replace(".stl", ""), "orientation": o["name"], "cups": o["cups"],
                             "warnings": "; ".join(o["warnings"]) or "none", "print min": round(o["print_time_s"] / 60, 1),
                             "resin ml (part + supports)": o["total_ml"],
                             "chosen": "✔" if job["orientation_chosen"].get(part) == o["name"] else ""})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        st.caption("Single-part numbers above are each part alone on a platform; the job total is both together.")
    else:
        st.info("The saved print job belongs to the ★ print candidate. Below: the PreForm results logged in this run.")
    st.subheader("All PreForm results logged in this run")
    rows = []
    for c in R["checks"]:
        if c["name"].startswith("preform"):
            m = c["details"].get("metrics", {})
            rows.append({"version": c["version_id"], "which": D.CHECKS.get(c["name"], c["name"]),
                         "source": "MOCK estimate" if c["details"].get("mocked") else "REAL PreForm",
                         "warnings": "; ".join(f["msg"] for f in c["details"].get("failures", [])) or "none",
                         "print min": round(m["print_time_s"] / 60, 1) if m.get("print_time_s") else None,
                         "resin ml": m.get("volume_ml")})
    if rows:
        df = pd.DataFrame(rows)
        real = df[df["source"] == "REAL PreForm"]
        st.dataframe(real if len(real) else df, hide_index=True, use_container_width=True)
        with st.expander("Details: the quick local estimate used inside the loop (MOCK, every version)"):
            st.dataframe(df[df["source"] != "REAL PreForm"], hide_index=True, use_container_width=True)
    risk = D.PRINT / "RISK_CHECK.md"
    if risk.exists() and R["name"] == D.BEST:
        with st.expander("Risk check before printing: unverified dimensions and ruler checks"):
            st.markdown(risk.read_text(encoding="utf-8"))


# ---------------- Downloads ----------------

def _step_zip():
    z = D.PRINT / "onshape_step.zip"
    if z.exists():
        return z.read_bytes(), z.name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted((D.PRINT / "onshape").rglob("*")):
            if f.is_file() and f.name != "rpi5_board_official.step":
                zf.write(f, f.relative_to(D.PRINT / "onshape"))
    return buf.getvalue(), "onshape_step.zip"


def _job_numbers():
    j = D.print_job()
    return f"{j['print_time_s'] / 60:.1f} min · {j['volume_ml']:.1f} ml" if j else "see results/print"


def downloads_page():
    run_picker()
    st.title("3 · Get your print files")
    st.caption("All files are the ★ print candidate's final design. Which one do you need?")
    cards = [
        ("FORMLABS PRINTER", "Formlabs .form: open in PreForm and print", D.PRINT / "formlabs" / "enclosure_a.form",
         "Form Studio 1, Alpha Fast V1, 0.15 mm. Base and lid on one platform, supports and orientation done.",
         [T.pill("REAL PreForm: 0 warnings", "real"), T.pill(_job_numbers(), "neutral")]),
        ("ANY RESIN (SLA) PRINTER", "STL files (base + lid)", D.PRINT / "stl_sla.zip",
         "The exact meshes inside the .form. Use your own slicer for supports.",
         [T.pill("Local checks pass", "pass"), T.pill("PreForm result applies to Formlabs only", "neutral")]),
        ("CREALITY / FDM PRINTER", "FDM STL files (0.4 mm nozzle, PLA)", D.PRINT / "fdm.zip",
         "Same design with FDM rules (bigger holes and gaps), pre-rotated for printing.",
         [T.pill("Local FDM checks pass", "pass"), T.pill("NOT checked by PreForm", "mock")]),
        ("ONSHAPE / ANY CAD", "STEP files", None,
         "Base, lid, simplified Pi 5 board, Active Cooler keep-out, plug zones and one combined assembly.",
         [T.pill("Cooler keep-out APPROXIMATE", "unv")]),
    ]
    cols = st.columns(2)
    for i, (who, title, path, desc, pills) in enumerate(cards):
        with cols[i % 2]:
            T.html_block(f'<div class="sc-dl"><div class="who">{who}</div><h3>{T.esc(title)}</h3><p>{T.esc(desc)}</p>'
                         + " ".join(pills) + "</div>")
            if path is None:
                data, name = _step_zip()
                st.download_button(f"⬇ Download {name} ({len(data) / 1e6:.1f} MB)", data, file_name=name,
                                   key="dl_step", use_container_width=True)
            elif path.exists():
                st.download_button(f"⬇ Download {path.name} ({path.stat().st_size / 1e6:.1f} MB)", path.read_bytes(),
                                   file_name=path.name, key=f"dl_{path.name}", use_container_width=True)
            else:
                st.caption(f"{path.name} not found (regenerate with `python -m cad.print_package zips`).")
    st.write("")
    st.markdown("Before printing, read the **assembly kit** (8 × M2.5 screws, no nuts) and the **risk check** "
                "(ruler checks for every unverified size).")
    c = st.columns(2)
    for col, f in zip(c, ("ASSEMBLY.md", "RISK_CHECK.md")):
        p = D.PRINT / f
        if p.exists():
            col.download_button(f"⬇ {f}", p.read_bytes(), file_name=f, key=f"dl_{f}", use_container_width=True)
    with st.expander("Details: files of the run selected in the sidebar"):
        d = Path(st.session_state.get("run_pick", ""))
        if d.exists():
            for f in sorted(list(d.glob("*.stl")) + list(d.glob("*.form")) + [d / "log.md", d / "final_spec.json"]):
                if f.exists():
                    st.download_button(f"⬇ {f.name}", f.read_bytes(), file_name=f"{d.name}_{f.name}",
                                       key=f"run_{f.name}")
            if (d / "log.md").exists():
                st.markdown((d / "log.md").read_text(encoding="utf-8"))


# ---------------- Charts ----------------

CHART_FILES = [("chart_benchmark_failures.png", "Benchmark: failures left per mode (Haiku, brief a, 3 seeds each)"),
               ("chart_candidate_failures.png", "Print candidate: problems left after each version, and who fixed what"),
               ("chart_edits_per_agent.png", "Print candidate: accepted vs rejected edits per agent")]


def charts_page():
    run_picker()
    st.title("Charts (computed by code from the logs)")
    st.caption("Every chart is plain Python over the logged data (board.sqlite, summary.json, per-run CSV). "
               "Code: scripts/make_submission_images.py and benchmark/metrics.py.")
    shown = 0
    for f, cap in CHART_FILES:
        p = D.SUBMIT_IMG / f
        if p.exists():
            st.subheader(cap)
            st.image(str(p), use_container_width=True)
            shown += 1
    T.html_block('<div class="sc-callout warn">Honest reading: only the Haiku set for brief a is complete '
                 '(3 seeds per mode). No run passed every check (0/3 in each mode). Mean failures left: team of 4 '
                 'agents 4, one generalist 6, one shot 20. The Sonnet benchmark is incomplete (4 of 18 runs, no team '
                 'run), so no Sonnet benchmark claims. The team also costs more per run.</div>')
    with st.expander("Explore a run set (live from the logs)", expanded=not shown):
        from benchmark import metrics
        roots = [p for p in sorted(D.BENCH.glob("runs*")) if p.is_dir()]
        if not roots:
            st.info("The benchmark run folders are not in git. The committed tables are in results/benchmark/*.md.")
            for f in sorted(D.BENCH.glob("runs_haiku_v2_*.png")):
                st.image(str(f), caption=f.name)
            return
        names = [p.name for p in roots]
        root = roots[names.index("runs_haiku_v2")] if "runs_haiku_v2" in names else roots[0]
        root = st.selectbox("Run set", roots, index=roots.index(root), format_func=lambda p: p.name)
        rows = [metrics.run_data(x) for x in sorted(root.iterdir()) if (x / "summary.json").exists()
                and "quality" in json.loads((x / "summary.json").read_text(encoding="utf-8"))]
        if not rows:
            st.info("No finished runs with quality scores in this set.")
            return
        rows.sort(key=metrics.rank_key)
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        st.dataframe(pd.DataFrame(metrics.table(rows)), hide_index=True, use_container_width=True)
        for name, fn in metrics.CHARTS.items():
            fig, cap = fn(rows)
            st.pyplot(fig)
            st.caption(cap)


# ---------------- Workshop ----------------

def workshop_page():
    import yaml
    R = need_run()
    st.title("Workshop: extra tools")
    run_bar(R)
    tab1, tab2 = st.tabs(["Manufacturing tools", "Fit test results"])
    with tab1:
        st.caption("Not used in the benchmark. Run on the final design of the selected run.")
        d = Path(R["dir"])
        part = st.radio("Part", ["base", "lid"], horizontal=True)
        stl = d / f"{part}.stl"
        c1, c2 = st.columns(2)
        if c1.button("Wall-thickness heat map", disabled=not stl.exists()):
            from checks.thickness_map import heatmap
            r = heatmap(stl, d / f"{part}_thickness.png", D.CFG)
            c1.image(r["png"])
            with c1.expander("Details"):
                st.write({k: val for k, val in r.items() if k != "png"})
        if c2.button("Compare print orientations in PreForm (2-6 min)", disabled=not stl.exists()):
            from checks.orientations import best, compare
            rows = compare(stl, D.CFG)
            c2.dataframe(pd.DataFrame(rows), hide_index=True)
            c2.success(f"Best by cups, then supports, then time: {best(rows)['name']}"
                       + (" (MOCK estimates)" if rows[0]["mocked"] else ""))
    with tab2:
        path = ROOT / "data" / "fit_test_results.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        filled = any(v for k, sec in data.items() if k != "meta" for v in sec.values())
        if not filled:
            T.html_block('<div class="sc-callout warn">The tolerance test plate (results/test_print) has not been '
                         'printed and measured yet, so every "measured" clearance falls back to the Formlabs design '
                         'guide. Fill this in after printing it.</div>')
        words = ["", "free", "snug", "press", "no_fit", "cracked", "failed_to_print"]
        meta = data.get("meta", {})
        m1, m2, m3 = st.columns(3)
        meta["printed_on"] = m1.text_input("Printed on", meta.get("printed_on", ""))
        meta["orientation"] = m2.text_input("Orientation", meta.get("orientation", ""))
        meta["post_cure"] = m3.text_input("Post-cure", meta.get("post_cure", ""))
        meta["notes"] = st.text_area("Notes", meta.get("notes", ""))
        for section, vals in data.items():
            if section == "meta":
                continue
            st.markdown(f"**{section.replace('_', ' ')}** (nominal mm)")
            cols = st.columns(len(vals))
            for col, (k, val) in zip(cols, list(vals.items())):
                choice = col.selectbox(str(k), words, index=words.index(val) if val in words else 0,
                                       key=f"{section}_{k}")
                vals[k] = choice or None
        if st.button("Save fit test", type="primary"):
            header = "".join(line + "\n" for line in path.read_text(encoding="utf-8").splitlines()
                             if line.startswith("#"))
            data["meta"] = meta
            path.write_text(header + "\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            from checks.fit_rules import rules
            st.success("Saved.")
            st.write(rules(D.CFG, "measured")["notes"])


PAGES = {}
