# Risk check before a one-shot print

Every dimension below is one the design **depends on but nobody has measured on the real parts**. Margins are the
exact minimum distance between that item and the base or lid, computed by `cad/margins.py` on the real geometry
(`risk/margins_sla.json`, `risk/margins_fdm.json`), plus a few worked out by hand from the spec (marked *calc*).
"SLA" = the PreForm-checked print (`formlabs/`, `stl_sla/`). "FDM" = `fdm/`.

## A. What the design depends on, and how much room it leaves

| # | unverified dimension (assumed value, source) | where it bites | margin SLA | margin FDM |
|---|---|---|---|---|
| 1 | **USB-A plug width, upper pair** (16.0 mm overmold, typical, UNV) | corner lid-screw boss (Ø5.5 at x 86.5, y 57.5) sticks out past the +x wall next to the plug | **0.07 mm** | **0.07 mm** |
| 2 | **USB-C plug** overmold 12.4 x 6.5 (typical, UNV) | sides and bottom of its wall opening | **0.60** | **0.80** |
| 3 | **micro-HDMI plugs** 11.0 x 6.5 (typical, UNV) | sides and bottom of each opening | **0.60** | **0.60** (narrowed so ≥1.2 mm of wall stays between the two) |
| 4 | **Lid over the tallest part**: USB-A stack top z 17.53 (official STEP, not measured) | lid underside z 18.4 | **0.80** | **0.80** |
| 5 | **Screwdriver Ø6.0** (UNV) at the two board screws beside the cooler | cooler keep-out edge | **0.0** (*calc*) | **0.0** |
| 6 | **Board screw length** M2.5x6 (stock length) | screw tip vs underside of the base (pilot hole runs through) | **0.2** (*calc*: 6 − 1.3 PCB − 2.9 standoff − 2.0 floor = −0.2, so the tip stops 0.2 inside) | 0.2 |
| 7 | GPIO ribbon 52 mm wide (UNV), only if you plug one in | slot sides | **0.60** | **0.80** |
| 8 | Power-button access 5 x 5 mm (our choice, not a part) | opening | 0.60 | 0.80 |
| 9 | microSD card edge at the PCB edge (UNV) + 13 x 3 finger zone | opening | 0.70 | 0.70 |
| 10 | Screw head Ø4.5 (UNV) beside the cooler | cooler keep-out | 0.83 (*calc*) | 0.83 |
| 11 | **Active Cooler footprint** (scaled from the drawing, ±1 mm, UNV), edge at x 0.5 | −x wall | **1.00** | 1.30 |
| 12 | **Active Cooler height** 14.0 above PCB bottom (scaled, UNV) | lid lip edge / lid over the fan | 1.40 / 4.40 | 1.40 / 4.40 |
| 13 | Cooler push-pin tips 1.5 below PCB (UNV) | standoff / floor | 1.25 | 1.30 |
| 14 | USB-A plugs, lower pair | +x wall outer face (width does not matter here) | 1.30 | 1.00 |
| 15 | Ethernet RJ45 plug 14.0 x 13.5 (typical, UNV) | opening | 1.44 | 1.26 |
| 16 | Fan intake position Ø21.5 at (42.2, 33.3) (scaled, UNV) | vent 23.5 x 23.5 | 1.0 per side (airflow only, not fit) | same |
| 17 | M2.5 pilot hole 2.1 / FDM 2.3 (rule of thumb, UNV) | screw grip | test on the part | test on the part |

Board outline 85 x 56 and connector positions are from the official drawing and STEP (they agree within 0.1 mm).
They are not "unverified", but the gaps to them are small: board edge to wall is 0.5 (SLA) / 0.8 (FDM). The ruler
check of the board in B7 costs 10 seconds.

## B. Ruler checks for every margin under 1 mm

A ruler with 1 mm marks reads to about **±0.5 mm**. It can rule out gross misfits, but it cannot confirm a 0.07 mm
or 0.6 mm margin. For those, the last line is "try it on the printed part". Optional feeler: ordinary 80 g/m²
printer paper is about 0.1 mm per sheet, so a folded stack makes a rough gauge.
Do these with the **plugs and cables you will use in the demo**.

| check | how to measure with a ruler | OK | **won't fit** |
|---|---|---|---|
| **B1. USB-A plug for the upper pair** (the pair nearest the GPIO header, at the corner) | Lay the plug flat and measure the overmold's **width** (the wide side, parallel to the board) right where it meets the metal | ≤ 15.5 mm | **≥ 16 mm: it touches the corner screw boss.** Plug that cable into the **lower pair** (next to Ethernet), which has no width limit from the case. If both pairs need wide plugs, see section C. |
| **B2. USB-C power plug** | Overmold width (wide side) and thickness | SLA: width ≤ 13 and thickness ≤ 7. FDM: ≤ 13.5 and ≤ 7.5 | SLA: **width ≥ 14 or thickness ≥ 8**. FDM: **width ≥ 14.5 or thickness ≥ 8.5** |
| **B3. micro-HDMI plugs** (same for SLA and FDM) | Overmold width and thickness | width ≤ 11.5 and thickness ≤ 7 | **width ≥ 12.5 or thickness ≥ 8** (both HDMI used at once: the plugs also need ≤ 13 mm each, because the ports are 13.4 mm apart) |
| **B4. USB-A stack height** | Stand the ruler on the PCB top surface beside the USB-A stack. Lay a flat card across the top of the stack and read where it crosses the ruler | 16 to 16.5 mm (nominal 16.2) | **≥ 17 mm above the PCB top**: the lid will not close |
| **B5. Screwdriver at the cooler** | No measurement: use a **2.0 mm hex key** (shaft Ø2) or fit the board screws **before** the cooler (ASSEMBLY.md does this) | | A screwdriver over 6 mm across cannot reach the two screws beside the cooler once the cooler is on |
| **B6. Board screws** | Measure the screw from under the head to the tip | 6 mm | **8 mm or longer**: sticks out 1.8 mm under the base (the case rocks). 5 mm or shorter: < 4 mm of thread in the post |
| **B7. Board outline** | Length and width of the bare PCB (not the connectors) | 85 x 56 | SLA: **≥ 86 x 57**. FDM: **≥ 86.5 x 57.5** |
| **B8. Active Cooler overhang** (footprint, margin 1.0 SLA) | Hold the ruler's edge flat against the PCB's short edge at the power-button end and look along it | the cooler stays behind the PCB edge | SLA: **cooler sticks out ≥ 0.5 mm past the PCB edge**. FDM: ≥ 0.8 mm |
| **B9. GPIO ribbon** (only if used) | Ribbon width | ≤ 52.5 | SLA: **≥ 53.5**. FDM: **≥ 54**. Also not modelled: the 2x20 IDC socket. If it is wider than about **7.4 mm** (SLA) / **7.6 mm** (FDM) across the header (centred on it), it hits the +y wall |
| B10. Power button, microSD | No ruler: on the printed base, press the button with a pen tip through its window, and push the card in and out with a fingernail | works | doesn't |

Above 1 mm (not required, but cheap): **cooler top** must be < 14 mm above the PCB top surface (nominal 12.7; the
lid lip clears it by 1.4). At ≥ 14 mm the lid lip touches the cooler's edge.

## C. The one real risk: B1 (0.07 mm)

The design passes our checks only because the assumed 16.0 mm USB-A plug just clears the corner lid-screw boss.
Real USB-A overmolds are often 12-15 mm (cable plugs) but can be 16-20 mm (flash drives, some keyboard dongles).
Options, from cheapest:
1. Use the lower USB-A pair for wide plugs. No reprint needed.
2. For the FDM print, move or shrink that corner boss before printing (`lid_screws.positions[3]`). This is a spec
   change, so rerun `cad.fdm` and the checks. The SLA `.form` would need a new PreForm run, and none is possible
   remotely.

## D. Not covered by any check here

- PLA/FDM screw grip and the 2.3 mm pilot holes: test by hand on the first screw.
- FDM shrink and warping: no measured data for your printer.
- Whether the lid vents cool well enough: not tested thermally.
