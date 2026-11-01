# Assembly kit: Raspberry Pi 5 + Active Cooler enclosure

Print candidate: run `a_pi5_standard__collective__generic__s0__claude-sonnet-5-5`. Do the ruler checks in
`RISK_CHECK.md` (section B) **before** printing.

## Parts

| part | SLA (Formlabs) | FDM (Creality, PLA) |
|---|---|---|
| base | in `formlabs/enclosure_a.form` (also `stl_sla/base.stl`) | `fdm/base_fdm.stl` |
| lid | in `formlabs/enclosure_a.form` (also `stl_sla/lid.stl`) | `fdm/lid_fdm.stl` |

Raspberry Pi 5 + official Active Cooler (its push pins and thermal pads come in its box).

## Hardware

| item | qty (+spare) | size | where | notes |
|---|---|---|---|---|
| socket head cap screw, ISO 4762 | **4** (+2) | **M2.5 x 6 mm** | board → standoffs | **Use 6 mm only**: 8 mm pokes 1.8 mm out under the base, and 5 mm leaves < 4 mm of thread. Head Ø4.5 (the size the checks assume). Self-taps into the 2.1 mm pilot hole (FDM: 2.3 modelled) |
| socket head cap screw, ISO 4762 | **4** (+2) | **M2.5 x 8 mm** | lid → corner bosses of the base | 6 mm of thread in the boss. 6 to 25 mm all fit (the boss is 23 mm tall). FDM: use **x10** if x8 feels loose in PLA |
| nuts | **0** | | | Both fasteners self-tap into pilot holes. No nut pockets in this design |
| *optional, robot mounting* | 4 bolts + 4 nuts | M3, length = 3 mm tab + your plate + ~3 mm | 4 tabs | Tab holes 3.4 (FDM 3.6). Not needed for the demo |

Pan-head (ISO 7045, head Ø5.0) M2.5 screws also work. Button heads (ISO 7380) are about Ø4.7. Both are slightly
bigger than the Ø4.5 checked: the margin to the cooler drops from 0.83 to about 0.6 mm.

**FDM differences:** same screws and counts. The holes are modelled 0.2 mm bigger for FDM. PLA strips easily, so
stop when the screw is snug.

## Tools

- **2.0 mm hex key** for M2.5 socket heads. Its thin shaft clears the cooler. A screwdriver wider than 6 mm does not
  reach the two board screws next to the cooler once the cooler is fitted (`RISK_CHECK.md` B5).
- Ruler (for the checks), flush cutters, a craft knife or fine sandpaper for support nubs.
- SLA: IPA wash + UV post-cure for Alpha Fast per Formlabs (Form Wash / Form Cure or equivalent).
- FDM: optional 2.2 mm drill bit, turned by hand, if a pilot hole is too tight.

## Step by step

1. **Finish the parts.** SLA: wash, cure, remove supports. Sand away support nubs on the lid's lip and underside so
   the lip drops into the base. FDM: pull the supports out of the microSD and GPIO slots. Clean the first-layer
   lip (elephant foot) off the lid's screw holes.
2. **Test the screw grip** (2 min): drive one M2.5 x 8 into one corner boss of the empty base, then back it out.
   If it is very hard to turn, the hole is too tight (open it a little). If it spins without biting, the hole is
   too loose (use x10 or a drop of glue).
3. **Cooler onto the Pi**, on the table, as Raspberry Pi's instructions say. Peel the thermal pad films, push both
   push pins into the two holes (near the USB-C corner and near the fan header) until they click. Plug the fan
   cable into the 4-pin fan header. **Take the microSD card out** for now: the card slot in the wall has a closed
   top, so a card sticking out would catch the wall while lowering.
4. **Board into the base.** Hold the Pi level and lower it straight down. The USB-A and Ethernet fronts slide down
   the open notches in the +x wall, and USB-C/HDMI drop into the notches in the −y wall. The four mounting holes
   land on the four standoffs.
5. **Board screws:** 4 x M2.5 x 6 through the mounting holes into the standoffs, with the 2.0 mm hex key. Turn them
   until snug, alternating diagonally. Do not crank: these are self-tapped plastic threads.
6. **microSD card** back in, through the slot in the −x wall (fingernail).
7. **Lid:** lip down, **vent slots over the round fan opening**. The vent is off-centre, so if the lid does not
   drop flat, turn it 180°. The lid underside sits 4.4 mm above the cooler and 0.8 mm above the USB-A stack.
8. **Lid screws:** 4 x M2.5 x 8 through the lid ears into the corner bosses, snug.
9. **Cables:** USB-C power last. Wide USB-A plugs go in the **lower pair** (next to Ethernet). See `RISK_CHECK.md` B1.

## Demo checklist (tick on the day)

| check | SLA | FDM |
|---|---|---|
| Board drops in straight, no forcing | ☐ yes ☐ no | ☐ yes ☐ no |
| Board screws grip (4/4) | ☐ __/4 | ☐ __/4 |
| USB-C power plug fits | ☐ yes ☐ no | ☐ yes ☐ no |
| HDMI 0 plug fits | ☐ yes ☐ no | ☐ yes ☐ no |
| HDMI 1 plug fits (with HDMI 0 in) | ☐ yes ☐ no | ☐ yes ☐ no |
| Ethernet plug fits, latch releases | ☐ yes ☐ no | ☐ yes ☐ no |
| USB-A lower pair: 2 plugs fit | ☐ yes ☐ no | ☐ yes ☐ no |
| USB-A upper pair: 2 plugs fit (**widest risk**) | ☐ yes ☐ no | ☐ yes ☐ no |
| microSD in/out through the slot | ☐ yes ☐ no | ☐ yes ☐ no |
| Power button pressable through its window (pen tip) | ☐ yes ☐ no | ☐ yes ☐ no |
| Status LEDs visible | ☐ yes ☐ no | ☐ yes ☐ no |
| Lid closes flat (no rocking, lip fully in) | ☐ yes ☐ no | ☐ yes ☐ no |
| Lid screws grip (4/4) | ☐ __/4 | ☐ __/4 |
| Fan clears the lid: spins, no rubbing noise, air through the vent | ☐ yes ☐ no | ☐ yes ☐ no |
| Case sits flat on the table (no screw tips under it) | ☐ yes ☐ no | ☐ yes ☐ no |

## PreForm print (SLA only; from `formlabs/enclosure_a.json`, real PreForm 3.64.2 results)

| | |
|---|---|
| printer / material / layer | Form Studio 1 / Alpha Fast V1 (FLAPFA01) / **0.15 mm**, print setting DEFAULT |
| platform | **one** build platform, base + lid |
| orientation | both "flat" (base open side up, lid as modelled). PreForm auto-orient gave 1 resin cup + 1 collision, so fixed orientations were chosen |
| PreForm warnings | **0** |
| print time | **23.0 min** (1381 s, PreForm estimate) |
| resin | **61.9 ml** incl. supports (parts 29.2 ml: base 18.06 + lid 11.10; supports ~33.8 ml) |

FDM: no slicer was run here, so there is no time or weight estimate. Solid volume is 29.3 cm3 (base 18.0 + lid
11.2), so at most about 36 g of PLA if printed 100 % solid. Your slicer gives the real number.
