"""Look and small HTML helpers. Colours, radii and type follow the team's ScienceClaw web UI
(Jash_ver: scienceclaw/static/style.css): dark panels, mint accent, pill badges, 12 px cards."""
import html

import streamlit as st

C = {"bg": "#101416", "panel": "#191f22", "panel2": "#141a1c", "line": "#30393c", "muted": "#99abae",
     "ink": "#edf4f1", "mint": "#bbefd5", "cyan": "#80c8ec", "purple": "#c3b1e7", "warn": "#d5ad83",
     "bad": "#e59a7a"}

CSS = """
<style>
:root{--bg:#101416;--panel:#191f22;--panel-2:#141a1c;--line:#30393c;--muted:#99abae;--ink:#edf4f1;--mint:#bbefd5;
--cyan:#80c8ec;--purple:#c3b1e7;--warn:#d5ad83;--bad:#e59a7a}
html,body,[class*="css"]{font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.block-container{padding-top:4.2rem;max-width:1500px}
h1{font-size:28px!important;font-weight:650!important;letter-spacing:-.2px}
h2{font-size:21px!important;font-weight:600!important}h3{font-size:16px!important;font-weight:600!important}
[data-testid="stHeader"]{background:var(--panel-2);border-bottom:1px solid var(--line)}
[data-testid="stSidebar"]{background:var(--panel-2);border-right:1px solid var(--line)}
[data-testid="stMetric"]{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 14px}
[data-testid="stMetricLabel"] p{color:var(--muted);font-size:12px}
[data-testid="stExpander"] details{border:1px solid var(--line);border-radius:10px;background:var(--panel-2)}
[data-testid="stExpander"] summary p{color:var(--muted);font-size:13px}
[data-testid="stBaseButton-primary"]{color:#10251d!important;font-weight:650}
[data-testid="stBaseButton-primary"] p{color:#10251d!important;font-weight:650}
.stButton>button,.stDownloadButton>button,[data-testid="stPageLink"] a{border-radius:6px}
.sc-brand{color:var(--mint);font-size:13px;letter-spacing:3px;font-weight:750;margin-bottom:2px}
.sc-lead{color:var(--ink);font-size:17px;line-height:1.55;max-width:980px;margin:4px 0 14px}
.sc-sub{color:var(--muted);font-size:13px;margin:0 0 10px}
.sc-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:12px;height:100%}
.sc-card h3{margin:0 0 6px;font-size:16px;color:var(--ink)}
.sc-card p{color:var(--muted);font-size:13px;margin:4px 0}
.sc-card .big{font-size:26px;font-weight:650;color:var(--ink);line-height:1.2}
.sc-step{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px;min-height:150px}
.sc-step .n{width:34px;height:34px;border-radius:50%;background:var(--mint);color:#10251d;display:inline-flex;
align-items:center;justify-content:center;font-weight:700;margin-bottom:8px}
.sc-step h3{margin:2px 0 6px;font-size:18px}.sc-step p{color:var(--muted);font-size:13px;margin:0}
.sc-pill{display:inline-block;border:1px solid #6d5a46;color:var(--warn);border-radius:20px;padding:2px 10px;
font-size:11px;font-weight:600;letter-spacing:.3px;white-space:nowrap;margin:0 4px 4px 0;vertical-align:middle}
.sc-pill.pass{border-color:#4f6d61;color:var(--mint);background:#16261f}
.sc-pill.fail{border-color:#805649;color:var(--bad);background:#2a211d}
.sc-pill.real{border-color:#3d6177;color:var(--cyan);background:#15232b}
.sc-pill.mock{border-color:#6d5a46;color:var(--warn);background:#2a2419}
.sc-pill.unv{border-color:#5c5291;color:var(--purple);background:#1f1d2b}
.sc-pill.neutral{border-color:var(--line);color:var(--muted)}
.sc-agent{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;height:100%}
.sc-agent .top{display:flex;gap:10px;align-items:center;margin-bottom:8px}
.sc-agent .av{flex:none;display:grid;place-items:center;width:36px;height:36px;border-radius:8px;font-size:17px;
background:#28423a;color:var(--mint)}
.sc-agent.manufacturing .av{background:#40392f;color:#dfc69d}.sc-agent.fit .av{background:#343449;color:var(--purple)}
.sc-agent.design .av{background:#1f3341;color:var(--cyan)}
.sc-agent b{font-size:15px}.sc-agent small{display:block;font-size:12px;color:var(--muted)}
.sc-agent .lbl{font-size:10px;letter-spacing:1.4px;color:var(--mint);font-weight:650;margin-top:10px}
.sc-agent p{font-size:13px;color:var(--ink);margin:3px 0}.sc-agent li{font-size:13px;color:var(--ink)}
.sc-agent ul{margin:4px 0;padding-left:18px}
.sc-tl{border-left:2px solid var(--line);margin-left:14px;padding:0 0 4px 22px;position:relative}
.sc-tl:before{content:"";position:absolute;left:-9px;top:4px;width:16px;height:16px;border-radius:50%;
background:var(--panel);border:2px solid var(--mint)}
.sc-tl.rej:before{border-color:var(--bad)}.sc-tl.base:before{border-color:var(--muted)}
.sc-tl h3{margin:0 0 4px}.sc-tl .row{color:var(--ink);font-size:14px;margin:4px 0}
.sc-tl .k{display:inline-block;min-width:118px;color:var(--muted);font-size:11px;letter-spacing:1.2px;font-weight:650}
.sc-num{display:inline-block;background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:2px 9px;
margin:2px 4px 2px 0;font-size:12px;color:var(--ink);font-variant-numeric:tabular-nums}
.sc-callout{border-left:3px solid var(--purple);background:#1f1d2b;padding:12px 16px;border-radius:6px;color:var(--ink);
font-size:14px;margin:8px 0 14px}
.sc-callout.good{border-left-color:var(--mint);background:#12241d}.sc-callout.warn{border-left-color:var(--warn);background:#2a2419}
.sc-dl{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:6px}
.sc-dl h3{margin:0 0 4px}.sc-dl .who{color:var(--mint);font-size:11px;letter-spacing:1.4px;font-weight:650}
.sc-dl p{color:var(--muted);font-size:13px;margin:4px 0}
.sc-runbar{background:var(--panel-2);border:1px solid var(--line);border-radius:10px;padding:8px 14px;margin:0 0 14px;
font-size:13px;color:var(--muted)}.sc-runbar b{color:var(--ink)}
</style>
"""


def apply():
    st.markdown(CSS, unsafe_allow_html=True)


def esc(s):
    return html.escape(str(s))


def pill(text, kind="neutral", tip=None):
    t = f' title="{esc(tip)}"' if tip else ""
    return f'<span class="sc-pill {kind}"{t}>{esc(text)}</span>'


def passfail(ok, tip=None):
    return pill("PASS", "pass", tip) if ok else pill("FAIL", "fail", tip)


def realmock(mocked, tip=None):
    return pill("MOCK", "mock", tip or "Simulated stand-in, not a real tool or model result") if mocked else \
        pill("REAL", "real", tip or "Result from the real tool or model")


def unverified(tip="Dimension taken from a drawing or a typical part, not measured on the real part"):
    return pill("UNVERIFIED", "unv", tip)


def num(label, value):
    return f'<span class="sc-num">{esc(label)} <b>{esc(value)}</b></span>'


def html_block(s):
    st.markdown(s, unsafe_allow_html=True)
