# FDM variant (Creality, 0.4 mm nozzle, PLA)

> **This variant is NOT checked by PreForm.** PreForm is an SLA tool, so it says nothing about FDM. Only our local
> assembly, printability and fit checks ran, with the FDM rules below. They all pass (`fdm_checks.json`).
> The FDM rules are generic published values plus our own margins. **No FDM part from this design has been
> printed or measured yet.**

Files:
- `base_fdm.stl`: open side up, floor on the bed. 93.5 x 76.0 x 23.3 mm, solid volume 18.0 cm3.
- `lid_fdm.stl`: flat top face on the bed, lip and screw ears pointing up. 93.5 x 64.5 x 5.0 mm, 11.2 cm3.
- `fdm_spec.json`: the design that was built. `fdm_checks.json`: check results, every spec change, and bridges.

Both STLs are already rotated. Import them and do **not** auto-orient.

## How it differs from the SLA print (no agent rerun)

`cad/fdm.py` took the print candidate's final spec and applied the FDM profile (`config.yaml` → `processes.fdm`):

| what | SLA print | FDM | why |
|---|---|---|---|
| board-to-wall gap, port clearance, lid fit (per side) | 0.6 / 0.5 mm | **0.8 mm** | 0.5 mm FDM mating-part clearance (sources below) + 0.3 mm margin for a desktop printer (UNVERIFIED) |
| M2.5 lid clearance hole | 2.9 | **3.1** | + 0.2 mm hole compensation (FDM holes print undersize; UNVERIFIED, typical 0.1-0.3) |
| M2.5 pilot holes (board standoffs, lid bosses) | 2.1 | **2.3** | + 0.2 mm hole compensation; should print at about 2.1 (UNVERIFIED) |
| robot-tab holes (M3) | 3.4 | **3.6** | + 0.2 mm hole compensation |
| minimum wall | 0.6 | **1.2** | 3 perimeters x 0.4 mm line width (your rule) |
| minimum hole / slot | 0.5 | **2.0** | UZH AMF FDM design guideline |
| assembly check: min gap to any part, lid gap | 0.3 | **0.5** | FDM position error is larger (UNVERIFIED) |
| standoff diameter | 5.5 | **5.4** | repair: keeps 0.5 mm from the USB-C keep-out box (wall around the pilot hole still 1.55 mm) |
| HDMI openings | +0.6/side | **+0.6/side** (port `extra` -0.2) | repair: at 0.8/side only 0.8 mm of wall was left between the two HDMI openings (< 1.2 min wall) |

Walls were already 1.2 mm (wall) and 2.0 mm (floor, lid), so they did not change.

## Suggested slicer settings (Cura / Creality Print; PrusaSlicer names in brackets)

| setting | value | note |
|---|---|---|
| layer height | **0.2 mm** (0.16 for a finer finish) | |
| line width | **0.4 mm** (perimeter extrusion width 0.4) | Important: the 1.2 mm walls are exactly 3 lines at 0.4. At 0.45 the slicer may print 2 lines + gap fill. |
| wall line count | **3** (perimeters 3) | |
| top / bottom layers | 5 / 5 (1.0 mm) | The floor and lid are 2.0 mm, so they come out almost solid anyway |
| infill | 20 % gyroid or grid | Only the floor, lid plate, tabs and posts have infill |
| nozzle / bed | 200-210 °C / 60 °C for PLA | Use your filament's label values first |
| hole / XY compensation | **off** | The model already adds 0.2 mm to every round hole. Don't add it twice. |
| initial-layer horizontal expansion (elephant foot comp.) | -0.1 to -0.2 mm | The lid's screw holes and vent slots start on the bed |
| brim | off (5 mm brim if the base's corners lift) | |
| supports | **see below** | |

### Where supports are needed

- **Lid** (flat face down): **no supports.** The vent slots are vertical, and the lip and screw ears point straight up.
- **Base** (open side up): every port notch on the -y and +x walls is open at the top, so it needs no support.
  The closed openings have a bridge across their top edge:

  | opening | wall | bridge span | support? |
  |---|---|---|---|
  | power button | -x | 6.6 mm | no (under 10 mm) |
  | status LED window | -x | 4.8 mm | no |
  | **microSD slot** | -x | **15.2 mm** | **yes** |
  | **GPIO ribbon slot** | +y | **53.6 mm** | **yes** |

  These two openings sit in the middle of a wall, so "touching build plate" supports do **not** reach them. Use
  **support "Everywhere"** with a support blocker over the rest of the base, or paint-on supports in the two slots
  only. Remove the support from the slots with flush cutters. If you skip support, the slot tops sag. That is
  cosmetic for the GPIO slot when no ribbon is used, but check that the microSD card still goes in.
  The mount tabs are at floor level and sit on the bed, so they need no support.

The 10 mm bridge limit comes from generic FDM guides (UNVERIFIED for your printer). Many Creality printers bridge
15 mm acceptably, so the microSD slot may be fine without support.

## Screws in PLA (UNVERIFIED)

M2.5 machine screws self-tapping into the 2.3 mm (as modelled) pilot holes. Turn them slowly and stop when they
are snug, because PLA strips easily. If a pilot hole is too tight, open it with a 2.2 mm drill bit by hand. If it
is too loose, use a slightly longer lid screw (M2.5x10). See `../ASSEMBLY.md`.

## Sources

- University of Zurich, AMF, *Design Guideline FDM*: 0.5 mm clearance between connecting or moving parts; minimum
  hole 2.0 mm. https://www.amf.uzh.ch/dam/jcr:fd1ee024-1a98-488b-89ec-ffd85befa786/Design-Guideline-FDM.pdf
- JLC3DP, *3D Printing Design Guideline*: 0.5 mm clearance for FDM mating parts. https://jlc3dp.com/help/article/3D-Printing-Design-Guideline
- Wevolver, *FDM 3D printing design tips*: bridges under about 10 mm print without support (generic guide). https://www.wevolver.com/article/fdm-3d-printing-design-tips-best-practices-and-techniques-for-stronger-prints
- Our choices, **UNVERIFIED**: the +0.3 mm desktop margin, 0.2 mm hole compensation, the 0.5 mm assembly-check gap,
  and the 2.3 mm pilot hole for M2.5 in PLA. Line width 0.4 = nozzle diameter is Cura's default.
