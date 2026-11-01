# Print package: Raspberry Pi 5 + Active Cooler enclosure

Design: final version of run `a_pi5_standard__collective__generic__s0__claude-sonnet-5-5` (four agents, Sonnet,
generic clearances). The same design comes in three manufacturing forms:

| you have | use | folder | checked by |
|---|---|---|---|
| **Formlabs Form Studio 1 + Alpha Fast V1** | `enclosure_a.form`: open in PreForm and print as is | `formlabs/` | **real PreForm**: 0 warnings, 23.0 min, 61.9 ml; our local checks |
| another SLA / resin printer, or a print service | `base.stl`, `lid.stl`: the exact meshes inside the .form | `stl_sla/` | our local checks. The PreForm result only applies to the Formlabs setting in `PRINT_SETTINGS.json` |
| **Creality (or any) FDM, 0.4 mm nozzle, PLA** | `base_fdm.stl`, `lid_fdm.stl`: FDM rules, pre-rotated | `fdm/` (read `FDM_NOTES.md`) | our local checks with FDM rules only. **Not checked by PreForm** |
| **Onshape** (or any CAD) | STEP of base and lid, the official Pi 5 board, the Active Cooler keep-out (approximate), plug zones, one combined assembly | `onshape/` (read `ONSHAPE_FIT_CHECK.md`) | for your own fit check |

Other files:
- `RISK_CHECK.md`: every unverified dimension, its margin (SLA and FDM), and the ruler checks to do **before** a
  one-shot print. **One real risk: a wide USB-A plug in the upper pair (0.07 mm margin).**
- `ASSEMBLY.md`: hardware (8 x M2.5 screws, no nuts), tools, steps, the demo checklist and the PreForm numbers.
- `render_assembled.png`, `render_exploded.png`, `render_lid_off.png`: slide images.
- `SLIDE_NOTE.md`: the 3-line problem / agent / fix note.
- `risk/margins_*.json`: raw clearance distances.
- Zips for the app's Downloads page: `stl_sla.zip`, `fdm.zip`, `onshape_step.zip` (the last is not in git; it
  holds the 77 MB official Pi STEP).

Regenerate everything (one light step per command):

```
.venv\Scripts\python.exe -m cad.print_package layout
.venv\Scripts\python.exe -m cad.fdm results\runs\a_pi5_standard__collective__generic__s0__claude-sonnet-5-5 results\print\fdm
.venv\Scripts\python.exe -m cad.print_package step
.venv\Scripts\python.exe -m cad.margins results\runs\a_pi5_standard__collective__generic__s0__claude-sonnet-5-5\final_spec.json results\print\risk\margins_sla.json sla
.venv\Scripts\python.exe -m cad.margins results\print\fdm\fdm_spec.json results\print\risk\margins_fdm.json fdm
.venv\Scripts\python.exe -m cad.print_package renders
.venv\Scripts\python.exe -m cad.print_package zips
```

Note: the run folder (`results/runs/`) is not in git. The `.form`, the STLs, the STEP files (except the official Pi
one) and `fdm/fdm_spec.json` are, so the package works without it.
