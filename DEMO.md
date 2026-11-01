# 3-minute demo

Before the demo (5 min earlier): start PreFormServer (`PreFormServer.exe --port 44388`), then
`.venv\Scripts\python.exe -m streamlit run app.py`, open the browser tab, pick the print-candidate run
(`runs/a_pi5_standard__collective__generic__s0__claude-sonnet-5-5`) on the Versions page so it is cached.
Have the printed enclosure (SLA or the FDM variant), the hardware from `results/print/ASSEMBLY.md` (8 x M2.5 screws, 2.0 mm hex key), the demo plugs/cables, and a Raspberry Pi 5 with cooler on the table. Do the ruler checks in `results/print/RISK_CHECK.md` before printing.

| time | click | say |
|---|---|---|
| 0:00-0:20 | (hold up the printed parts) | "Agents designed this Raspberry Pi 5 enclosure and checked it before printing: four specialists, no boss, each owning one tool: assembly geometry, PreForm + printability, measured fit tolerances, and the design itself." |
| 0:20-0:50 | **Run** page: brief `a_pi5_standard`, mode collective, untick Mock, model Sonnet, budget 4, **Run** | "Every mode starts from the same deliberately naive design. Watch the shared board: an agent finds a failure, and if the fix is in another agent's keys it posts a need to that owner." Point at a `need` row and an `accepted` decision. |
| 0:50-1:30 | **Versions** page, print-candidate run, slide through versions; tab **3D view** (rotate, tick *board keep-out*) | "This is version by version. Here the Fit agent raised the standoffs because Assembly reported screws that could not engage; here Manufacturing added a vent over the fan." |
| 1:30-2:00 | tab **Why this design** on the final version; **Write AI summary** | "The trail is built from the log: which check failed, the need, who proposed what and why, who objected with numbers, the decision. The AI summary is labelled, and every number in it is filled in from the log, not written by the model." |
| 2:00-2:20 | tab **PreForm** (final version) | "Real PreForm on the final design. With PreForm's own auto-orient it found one resin-trapping cup; the print job then picked the orientation per part: zero warnings, one Form Studio 1 platform, Alpha Fast V1 at 0.15 mm, 23 minutes, 62 ml of resin. That is `results/print/formlabs/enclosure_a.form`." Then **Downloads** page: "The same design as a .form, SLA STLs, an FDM version for a Creality, and STEP for Onshape." |
| 2:20-2:50 | **Charts** page, set `runs_haiku_v2` (the most complete set, 11/18 runs) | "Same model, tools and budget in all three modes. The main chart is failures left; a design that fails a check never outranks one that passes. Every chart is plain code over the logs." Be honest if asked: Sonnet finished only 4/18 benchmark runs and no Sonnet collective run in the benchmark (`results/benchmark/sonnet_vs_haiku.md`); no run passes all checks including real PreForm. |
| 2:50-3:00 | (the tolerance plate) | "Next: the same enclosure with clearances measured on this plate instead of generic rules: does it assemble better? We only claim what the checks and the prints show." |

## Backup plan if the live demo fails

- **No internet / API error**: tick **Mock** on the Run page (rule-based fake model, labelled MOCK) and show the
  same flow; or skip the live run and go straight to the Versions page of the finished print-candidate run.
- **PreFormServer not running**: PreForm results fall back to MOCK (labelled). Show the saved real results instead:
  `results/print/formlabs/enclosure_a.json` and the `.form` opened in PreForm.
- **Streamlit fails**: open `results/runs/<print candidate>/log.md` (full readable log), the slide renders
  (`results/print/render_assembled.png`, `render_exploded.png`, `render_lid_off.png`) and `results/benchmark/runs_sonnet_summary.md` with its chart PNGs.
- **Laptop fails**: printed parts + one slide with the 3-line problem/agent/fix note (`results/print/SLIDE_NOTE.md`).

## Final files and numbers (print candidate `a_pi5_standard__collective__generic__s0__claude-sonnet-5-5`)

Start at `results/print/README.md` (which file for which printer or tool).

| what | path | numbers |
|---|---|---|
| Formlabs (prints as is) | `results/print/formlabs/enclosure_a.form` + `PRINT_SETTINGS.json` | Form Studio 1, Alpha Fast V1, 0.15 mm; 0 PreForm warnings; 23.0 min; 61.9 ml incl. supports |
| SLA STL (exact .form geometry) | `results/print/stl_sla/base.stl`, `lid.stl` | base 18.06 ml, lid 11.10 ml |
| **FDM option** (Creality 0.4 mm, PLA) | `results/print/fdm/base_fdm.stl`, `lid_fdm.stl`, `FDM_NOTES.md` | same spec with FDM rules (`config.yaml` `processes.fdm`), local checks pass, **not checked by PreForm**; supports only in the microSD and GPIO slots |
| **Onshape option** | `results/print/onshape/*.step`, `ONSHAPE_FIT_CHECK.md` | base, lid, official Pi 5 STEP (MIT, not in git), cooler keep-out (approx), plug zones, combined assembly; one frame |
| Risk check | `results/print/RISK_CHECK.md` | top risk: wide USB-A plug in the upper pair (0.07 mm); USB-C/HDMI plug margins 0.6 mm |
| Assembly kit + demo checklist | `results/print/ASSEMBLY.md` | 4 x M2.5x6 (board) + 4 x M2.5x8 (lid), no nuts |
| Slide images | `results/print/render_assembled.png`, `render_exploded.png`, `render_lid_off.png` | |
| Benchmark | `results/benchmark/sonnet_vs_haiku.md` | Sonnet 4/18 finished, Haiku 11/18; missing runs listed |
| API cost so far | from every run's logged usage | $1.70 total ($1.20 Sonnet, of which $0.82 the print-candidate run) |
