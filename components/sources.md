# Sources for every outside dimension

Values marked **UNVERIFIED** are best documented or typical values. The fit-check frame
(`results/fit_check/`) and the tolerance print test them physically.

## Raspberry Pi 5 (`rpi5.yaml`)

| Key | Source | What it says |
|---|---|---|
| DRW | https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf | Board 85 x 56. Holes Ø2.7, 3.5 from edges, 58 and 49 apart. USB-C at 11.2, HDMI at 25.8 and 39.2 from the left edge. Ethernet centre 10.2, USB stacks 29.1 and 47 from the bottom edge. Ethernet sticks out 3 mm. Left-edge features at 18.4, 13.3 and 6. "All dimensions are approximate and for reference purposes only." |
| STEP | https://datasheets.raspberrypi.com/rpi5/RaspberryPi5-step.zip (`rpi-5b_no_graphics.step`, dated 2025-05-27) | Official 3D model. Bounding boxes of 2711 solids were read with `cad/inspect_step.py`, and PCB holes with a cylinder-face scan. Results match DRW (port centres within 0.1 mm). Also gives: PCB 1.3 thick; cooler push-pin holes Ø3.1 at (3.5, 9.5) and (61.5, 46.5); tallest part the USB-A stack, top at z 17.53; underside microSD socket to z -1.42. |
| UNV plug sizes | Typical cable overmolds, not from Raspberry Pi | USB-C plug overmold ≤ 12.35 x 6.5 (commonly quoted USB Type-C recommended maximum, **UNVERIFIED**); micro-HDMI 11 x 6.5, RJ45 + boot 14 x 13.5, USB-A 16 wide (**UNVERIFIED**, typical). These are what the plug clearance zones use. |
| UNV FFC/ribbon widths | Typical | PCIe 16-pin and MIPI 22-pin 0.5 mm FFC widths, 40-way IDC ribbon about 51 mm (**UNVERIFIED**). |

## Raspberry Pi Active Cooler (`active_cooler.yaml`)

| Key | Source | What it says |
|---|---|---|
| CDRW | https://datasheets.raspberrypi.com/cooling/raspberry-pi-active-cooler-mechanical-drawing.pdf | Footprint 63.50 x 42.50, fan 30 x 30, overall height 13.70 (side view: fan top to push-pin tips). |
| PAGE | https://www.raspberrypi.com/products/active-cooler/ | "Heatsink-mounted, temperature-controlled blower fan", "Maximum airflow: 1.09 CFM", "Spring-loaded push pins", "four-pin fan header on Raspberry Pi 5". |
| PIX | Measured from CDRW by pixel scaling (4.41 px/mm) | Pin positions relative to the footprint corners, fan opening centre and diameter, and the height of the base above the SoC (10.2). Each is ±1 mm and **UNVERIFIED**. The envelope top (z 14.0 in the board frame) is **UNVERIFIED**. |

## Fasteners

| Item | Source | What it says |
|---|---|---|
| M2.5 hex nut | ISO 4032 / DIN 934. Supplier listing: https://aspenfasteners.com/m2-5-0-45-din-934-iso-4032-metric-hex-finished-nuts-class-6-steel-zinc-plated | Across flats 5.00 max (4.82 min), height 2.00 max (1.75 min). |
| M2.5 clearance hole | ISO 273 medium series (**UNVERIFIED** here, commonly quoted) | 2.9 mm medium clearance for M2.5. |

## Printing (Formlabs)

| Item | Source | What it says |
|---|---|---|
| Formlabs Design Guide (Form 1+/Form 2, Clear resin, 100 µm) | https://pocc-techpark.media.uconn.edu/wp-content/uploads/sites/2880/2020/10/formlabs-design-guide.pdf (copy of Formlabs' guide) | Min supported wall 0.4. Min unsupported wall 0.6. Min clearance between parts 0.5 ("less than 0.5 mm may cause parts to fuse"). Min hole 0.5. Min engraved detail 0.4. Min embossed 0.1. Drain hole 3.5. Max overhang 1.0. Overhang angle ≥ 19°. The guide warns that other resins may vary. It does not cover the Form Studio 1 or Rigid 10K, so these are our **generic** rules. |
| Formlabs Engineering Fit | https://formlabs.com/global/blog/3D-printing-tolerances-for-engineering-fit/ | Recommends printing a test coupon for the exact resin and printer, which is what Phase 1 does. Gives no numeric values for Rigid resins. |
