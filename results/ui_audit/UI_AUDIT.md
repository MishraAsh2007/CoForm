# UI audit

Script: `scripts/ui_audit_before.py` (Playwright, headless Chromium 1440x900, Streamlit headless on port 8599).
Every page and every tab of the classic app (`app.py` at commit c14d038, kept as `app_classic.py`) was opened,
with the print-candidate run `a_pi5_standard__collective__generic__s0__claude-sonnet-5-5` selected where a run is
needed. Screenshots: `results/ui_audit/before/`. Times: `before/timings.json` (seconds from the click until
Streamlit's "running" indicator went away; a Plotly chart can still be drawing after that).

## Before: problems, ranked by how much they would hurt a live demo

| # | problem | screenshot | why it hurts |
|---|---|---|---|
| 1 | **Charts page crashes** with `KeyError: 'rank'` when the run set `runs_haiku_v2` (the one the demo script uses) is picked. `app.py` builds rows with `metrics.run_data` but never assigns ranks (`metrics.collect` does). | `04b_charts_haiku_v2.png` | A red traceback on stage, at the step meant to show the benchmark. |
| 2 | **Charts page opens on an empty set** ("runs": no runs with quality scores). | `04_charts_default.png` | First impression of the page is "nothing here". |
| 3 | **3D view is an empty grey box** when the page says it is done (3.4 s). The full-resolution meshes (base 10,122 + lid 3,894 triangles) are reloaded from disk and re-sent to Plotly on every rerun, and every slider move reruns the page. | `02_versions_3d.png` | The most visual part of the demo looks broken or slow. |
| 4 | **Every run picker defaults to the newest folder**, which is a stopped benchmark run (`runs_sonnet/...__s1__claude-sonnet-5-5`, status "running", 0 versions, $0.0000). The presenter has to find the print candidate in a long list of internal folder names. | `01_run.png`, `03_downloads.png`, `05_tools.png` | Dead data on the first screen; fumbling with a dropdown live. |
| 5 | **No landing page**. The app opens on a form full of internal terms (Brief `a_pi5_standard`, component `rpi5_cooler`, Seed, Clearances generic/measured, Budget (evaluations)). Nothing says what the project is. | `01_run.png` | A judge cannot tell what they are looking at. |
| 6 | **Internal names everywhere**: spec keys (`/clearances/board_gap`), check codes (`preform_real`, `preform_final`), raw JSON (`{"warnings": 0, "print_time_s": 8586, "mocked": true}`), agent message text with JSON patches. | `02b_*`, `02c_*`, `02d_*`, `02e_*` | Needs explaining at every step. |
| 7 | **The PreForm tab leads with the in-loop MOCK estimate** (0 warnings, 8586 s), then the final real result (1 warning), with the print-job result (0 warnings, 23 min) further down. Seconds instead of minutes. | `02d_versions_preform.png` | The three PreForm numbers look contradictory unless explained. |
| 8 | **No agent view**: which agent owns what and what each did is only visible as rows in a table of board messages. | `01_run.png`, `02e_*` | The core idea (specialists, no boss) is not visible. |
| 9 | **"Why this design" on the final version shows only the last small edit** (+0.1 mm clearances), not the story of the run (20 failures -> 0, who fixed what). Breakdown lines read "fixed 0: \| broke 0:". | `02e_versions_why_this_design.png` | The strongest story in the log is hidden. |
| 10 | **Downloads**: four buttons with long technical labels; nothing says "use this one if you have a Formlabs printer". The lower half repeats the stale default run. | `03_downloads.png` | OK for us, slow for a judge. |
| 11 | **Fit test page is empty** (every result "Choose an option") because the tolerance plate has not been printed. Nothing says so. | `06_fit_test.png` | Looks unfinished. |
| 12 | **Tools page** has two buttons and no explanation; it also defaults to the stale run (heat map would fail without STLs). | `05_tools.png` | Low (not in the demo script). |
| 13 | Default Streamlit look (red accent, light theme, "Deploy" button) does not match the teammates' dark ScienceClaw UI. | all | Two halves of the team project look like different products. |
| 14 | First page load 8.8 s (cold start, imports CadQuery stack). | `01_run.png` | Start the app before the demo. |

No browser console errors were logged.

## After: new UI (`app.py`)

Script: `scripts/ui_audit_after.py` (every page by URL) and `scripts/ui_audit_run.py` (clicks **Start the run** with
Mock on, budget 4, and screenshots the live progress). Screenshots: `results/ui_audit/after/`. Times in
`after/timings.json` now include waiting until every 3D canvas has been drawn.

| # | before | after | screenshot |
|---|---|---|---|
| 1 | Charts crashed (`KeyError: 'rank'`) | Fixed in both apps (ranks assigned as in `metrics.collect`). Charts page leads with the honest summary, then the clean step-4 charts, then a live explorer that opens on `runs_haiku_v2` | `09_charts.png` |
| 2 | Charts opened on an empty set | Opens on `runs_haiku_v2` | `09_charts.png` |
| 3 | 3D view empty / slow, full rerun on every slider move | In-browser viewer adapted from the team's `viewer3d.js`: meshes cached, rotate / lid lift / keep-out toggle without any rerun; first-vs-final side by side; base, lid, assembled | `05_viewer.png` |
| 4 | Pickers defaulted to a stopped run with 0 versions | ★ print candidate pre-selected on every page; empty runs hidden; `?run=` works | all |
| 5 | No landing page | Home: 3 sentences, 3 big steps, the best run's numbers, honest status | `01_home.png` |
| 6 | Internal names and raw JSON | Plain labels ("board can't drop in: a connector hits the wall (4)"), tooltips, raw JSON only in "Details" expanders | `04_story.png`, `06_why.png` |
| 7 | Contradictory PreForm numbers | Print page tells it in 3 steps: auto-orient 2 warnings (1 cup, 1 collision) → orientation tool (6 orientations) → 0 warnings, 23.0 min, 61.9 ml, one platform; MOCK estimates in Details | `07_print.png` |
| 8 | No agent view | 4 agent cards (owns, tool, what it did in this run, from the log) + the board as a readable feed | `03_agents.png` |
| 9 | "Why" showed only the last tiny edit | Story page: whole run as a timeline with a snapshot per step; Why page opens on v3 (Assembly's need → Fit raises the standoffs) and shows the earlier need | `04_story.png`, `06_why.png` |
| 10 | Downloads unclear | 4 cards: "Formlabs printer / any resin printer / Creality FDM / Onshape", each with badges (REAL PreForm, NOT checked by PreForm, APPROXIMATE) | `08_downloads.png` |
| 11 | Empty fit test unexplained | Callout: plate not printed yet, measured clearances fall back to the guide | `10_workshop.png` (tab 2) |
| 13 | Default Streamlit look | Team look: dark panels, mint accent, pills, 12 px cards (`.streamlit/config.toml`, `ui/theme.py`) | all |
| new | Re-running a run name that already existed failed silently on Windows: the old `board.sqlite` was held open, so `run.py`'s `rmtree` half-deleted the old folder | The UI closes every database handle after reading, moves an old folder aside in one step (`__prev_<time>`, hidden), shows thread errors on the page, and ignores a stale "done" status | `11_run_progress_1.png` |

Load times after (s): see `after/timings.json`; every page 2.6 to 3.5 s including drawing the 3D canvases, first
cold load 6.4 s. Start the app before the demo.

Remaining known issues (low priority):
- The canvas viewer sorts triangles (painter's algorithm), so where the see-through lid overlaps the board you can
  see small jagged edges. Lift the lid or untick it for a clean look.
- Story snapshots are the renders saved during the run (white background).
- During my first progress test the old MOCK run `a_pi5_standard__collective__generic__s0__mock` lost 4 files
  (base STL/.form and two PNGs) because of the bug above. It is a MOCK run, not used anywhere in the submission;
  rerun it with `run.py --mock` if you want it back (the UI's test rerun replaced it already).
