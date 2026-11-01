# 3-minute demo

Recorded version (no audio, on-screen captions, 2 min 50 s): `results/submission/demo.mp4` (GIF preview:
`results/submission/demo.gif`). Re-record with `.venv\Scripts\python.exe scripts\record_demo.py`.

## Before the demo (5 minutes earlier)

1. Start PreFormServer: `PreFormServer.exe --port 44388` (only needed if you click a live PreForm tool; the saved
   results are shown either way).
2. `.venv\Scripts\python.exe -m streamlit run app.py`, open the browser tab. The ★ print candidate
   (`a_pi5_standard__collective__generic__s0__claude-sonnet-5-5`) is pre-selected on every page. Click through
   Home → Story → 3D once so the meshes are cached (first cold load about 6 s, then about 3 s per page).
3. On the table: the printed enclosure if you have it, the hardware from `results/print/ASSEMBLY.md` (8 × M2.5
   screws, 2.0 mm hex key), the demo cables, a Raspberry Pi 5 with Active Cooler. Do the ruler checks in
   `results/print/RISK_CHECK.md` before printing.
4. Pick ONE of the two scripts below: **IF PRINTED** or **IF NOT PRINTED**. Only the first 20 s and the last
   30 s differ.

Numbers to say (all from the run log and result files):
- local check failures **20 → 0** (naive start → final, run log `results/demo_run/.../log.md`)
- real PreForm with PreForm's own auto-orient: **1 resin-trapping cup** in the base (the run's final check; the
  print job's auto-orient attempt also reported 1 colliding pair)
- print job, orientation chosen by the Manufacturing agent's orientation tool: **0 warnings, 23.0 min, 61.9 ml**,
  one Form Studio 1 platform, Alpha Fast V1, 0.15 mm (`results/print/formlabs/enclosure_a.json`)
- benchmark, Haiku, brief a, 3 seeds per mode: mean failures left **team 4, generalist 6, one shot 20**;
  **0/3 passed** in every mode; the team costs more per run ($0.032 vs $0.019 vs $0.001)
- Sonnet benchmark incomplete (4 of 18 runs, no team run): make **no** Sonnet benchmark claims

## Script A: IF PRINTED

| time | click | say |
|---|---|---|
| 0:00-0:20 | (hold up the printed base and lid, Pi inside) | "Four AI agents designed this Raspberry Pi 5 case and checked it before it was printed. Four specialists, no boss: Design, Assembly, Manufacturing and Fit, each owning one real tool." |
| 0:20-0:35 | **Home** | "Pick what to design, watch the agents work, get the print files. This is the best run: local check failures 20 down to 0." |
| 0:35-1:15 | **Story**, scroll v2 → v5 | "Every step is from the log. Assembly found the board couldn't drop in and the lid sat on the USB stack: it raised the lid, 20 to 13. The screws couldn't grip, which is Fit's part, so Assembly posted a need and Fit raised the standoffs from 1.5 to 2.9 mm: 13 to 5. Manufacturing added a vent over the fan. Edits that make anything worse are rejected by the rules, like this thinner floor that broke 4 screw checks." |
| 1:15-1:40 | **3D**: drag to rotate, slide the lid, tick *Board keep-out* | "First design versus final, rotating together: tabs, vent, open port tops, taller posts. Keep-out shows every connector zone the case must clear." |
| 1:40-2:00 | **Why** (opens on v3) | "Why this design is built only from the log: Assembly's need, Fit's fix, the numbers. The AI summary may not write numbers; they're filled in from the log." |
| 2:00-2:20 | **Print** | "Real PreForm. With PreForm's own auto-orient there was one resin-trapping cup. Manufacturing's orientation tool compared six orientations and picked flat: zero warnings, one platform, 23 minutes, 62 ml." |
| 2:20-2:40 | **Charts** | "Same model, tools and budget in all three modes, three seeds each: the team leaves 4 failures on average, one generalist 6, one shot 20. Honestly: no run passed every check, and the team costs more per run." |
| 2:40-3:00 | (screw the lid on, plug a cable) | "And here's the print: [say only what you saw: e.g. 'the board dropped in, the screws grip, the lid closes', or name what did not fit]. Download the .form and print it yourself." |

Fill the last line from the demo checklist in `results/print/ASSEMBLY.md`. Do not claim a fit you did not test.

## Script B: IF NOT PRINTED

| time | click | say |
|---|---|---|
| 0:00-0:20 | **Home** | "Four AI agents design a 3D-printed Raspberry Pi 5 case and check it before it is printed. Four specialists, no boss, each owning one real tool. We haven't printed it yet, so I'll show what the checks show and how we would verify the fit." |
| 0:20-2:20 | same as Script A, 0:35-2:40 (Story, 3D, Why, Print, Charts) | same words |
| 2:20-2:50 | **Downloads**, then the Onshape document (prepared from `results/print/onshape/ONSHAPE_FIT_CHECK.md`) | "Without a print, the fit check is geometric: the base and lid as STEP with the official Raspberry Pi 5 model in one frame. Boolean intersect with keep tools: empty means no overlap. Measured: the lid sits 4.4 mm over the fan keep-out and 0.8 mm over the USB stack. The one real risk is a wide USB-A plug in the upper pair: 0.07 mm, so wide plugs go in the lower pair." |
| 2:50-3:00 | (the tolerance plate, if printed; else stay on Downloads) | "Next: print the tolerance plate and switch the Fit agent to measured clearances. We only claim what the checks and the prints show." |

Say clearly that the cooler keep-out and plug zones are APPROXIMATE / UNVERIFIED (typical sizes, not measured).

## Backup plan if the live demo fails

- **Streamlit or the laptop browser fails**: play `results/submission/demo.mp4` (same walkthrough, captions on screen).
- **The new UI breaks**: `.venv\Scripts\python.exe -m streamlit run app_classic.py` (previous UI, same data).
- **No internet / API error**: nothing in the walkthrough needs the API. The AI-summary button falls back to the
  labelled MOCK template. For a live run, keep **Mock** on (Run page, default) with budget 4.
- **PreFormServer not running**: all PreForm numbers shown are saved real results; only the Workshop page's live
  orientation tool falls back to MOCK (labelled).
- **Laptop fails**: the slides (`results/submission/slides.pdf`) carry the renders, the Story chart and the
  benchmark chart; the printed parts + `results/print/SLIDE_NOTE.md` (3-line problem / agent / fix note).

## Final files and numbers (print candidate `a_pi5_standard__collective__generic__s0__claude-sonnet-5-5`)

Start at `results/print/README.md` (which file for which printer or tool).

| what | path | numbers |
|---|---|---|
| Formlabs (prints as is) | `results/print/formlabs/enclosure_a.form` + `PRINT_SETTINGS.json` | Form Studio 1, Alpha Fast V1, 0.15 mm; 0 PreForm warnings; 23.0 min; 61.9 ml incl. supports |
| SLA STL (exact .form geometry) | `results/print/stl_sla/base.stl`, `lid.stl` | base 18.06 ml, lid 11.10 ml |
| **FDM option** (Creality 0.4 mm, PLA) | `results/print/fdm/base_fdm.stl`, `lid_fdm.stl`, `FDM_NOTES.md` | same spec with FDM rules, local checks pass, **not checked by PreForm** |
| **Onshape option** | `results/print/onshape/*.step`, `ONSHAPE_FIT_CHECK.md` | base, lid, official Pi 5 STEP (MIT, not in git), cooler keep-out (approx), plug zones, combined assembly; one frame |
| Risk check | `results/print/RISK_CHECK.md` | top risk: wide USB-A plug in the upper pair (0.07 mm); USB-C/HDMI plug margins 0.6 mm |
| Assembly kit + demo checklist | `results/print/ASSEMBLY.md` | 4 × M2.5x6 (board) + 4 × M2.5x8 (lid), no nuts |
| Pictures and charts | `results/submission/images/` | made by `scripts/make_submission_images.py` from the logs |
| Benchmark | `results/benchmark/sonnet_vs_haiku.md` | Sonnet 4/18 finished, Haiku 11/18; missing runs listed |
| API cost so far | from every run's logged usage | $1.70 total ($1.20 Sonnet, of which $0.82 the print-candidate run) |
