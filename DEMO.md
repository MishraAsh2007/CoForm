# 3-minute demo

Before the demo (5 min earlier): start PreFormServer (`PreFormServer.exe --port 44388`), then
`.venv\Scripts\python.exe -m streamlit run app.py`, open the browser tab, pick the print-candidate run
(`runs/a_pi5_standard__collective__generic__s0__claude-sonnet-5-5`) on the Versions page so it is cached.
Have the printed enclosure, the tolerance plate + key and a Raspberry Pi 5 with cooler on the table.

| time | click | say |
|---|---|---|
| 0:00-0:20 | (hold up the printed parts) | "Agents designed this Raspberry Pi 5 enclosure and checked it before printing: four specialists, no boss, each owning one tool: assembly geometry, PreForm + printability, measured fit tolerances, and the design itself." |
| 0:20-0:50 | **Run** page: brief `a_pi5_standard`, mode collective, untick Mock, model Sonnet, budget 4, **Run** | "Every mode starts from the same deliberately naive design. Watch the shared board: an agent finds a failure, and if the fix is in another agent's keys it posts a need to that owner." Point at a `need` row and an `accepted` decision. |
| 0:50-1:30 | **Versions** page, print-candidate run, slide through versions; tab **3D view** (rotate, tick *board keep-out*) | "This is version by version. Here the Fit agent raised the standoffs because Assembly reported screws that could not engage; here Manufacturing added a vent over the fan." |
| 1:30-2:00 | tab **Why this design** on the final version; **Write AI summary** | "The trail is built from the log: which check failed, the need, who proposed what and why, who objected with numbers, the decision. The AI summary is labelled, and every number in it is filled in from the log, not written by the model." |
| 2:00-2:20 | tab **PreForm** | "Real PreForm on the final design: zero cups, zero warnings, Alpha Fast at 0.15 mm on the Form Studio 1. That is the .form we printed." |
| 2:20-2:50 | **Charts** page, set `runs_sonnet` | "Same model, tools and budget in all three modes. The main chart is failures left; a design that fails a check never outranks one that passes. Every chart is plain code over the logs, and the caption says which runs, seeds and what was mocked." |
| 2:50-3:00 | (the tolerance plate) | "Next: the same enclosure with clearances measured on this plate instead of generic rules: does it assemble better? We only claim what the checks and the prints show." |

## Backup plan if the live demo fails

- **No internet / API error**: tick **Mock** on the Run page (rule-based fake model, labelled MOCK) and show the
  same flow; or skip the live run and go straight to the Versions page of the finished print-candidate run.
- **PreFormServer not running**: PreForm results fall back to MOCK (labelled). Show the saved real results instead:
  `results/print/enclosure_a.json` and the `.form` opened in PreForm.
- **Streamlit fails**: open `results/runs/<print candidate>/log.md` (full readable log), the PNGs next to it
  (`assembled.png`, `exploded.png`) and `results/benchmark/runs_sonnet_summary.md` with its chart PNGs.
- **Laptop fails**: printed parts + one slide with the 3-line problem/agent/fix note (`results/print/SLIDE_NOTE.md`).
