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

## Arduino UNO R3 (`arduino_uno.yaml`), software-only benchmark board

| Key | Source | What it says |
|---|---|---|
| ADS | https://docs.arduino.cc/resources/datasheets/A000066-datasheet.pdf, section 5.4 | "4 x Ø3.20mm" mounting holes. Holes 15.24 / 2.54 from the top-left corner, 66.04 from the left edge to the right-hand holes, 17.78 and 45.72 from the top edge, 50.80 from the top edge to the bottom-left hole. |
| APIX | Same drawing, pixel scaling | Board outline 68.6 x 53.3; USB-B and DC jack positions and overhangs (**UNVERIFIED**). |
| UNV | Typical | Heights of USB-B, jack and headers, PCB 1.6, plug sizes (**UNVERIFIED**). |

## Raspberry Pi Pico (`rpi_pico.yaml`), software-only benchmark board

| Key | Source | What it says |
|---|---|---|
| PDS | https://datasheets.raspberrypi.com/pico/pico-datasheet.pdf, section 2, Figure 3 | "single sided 51×21 mm 1 mm thick PCB with a micro-USB port overhanging the top edge", "4× 2.1 mm (± 0.05 mm) drilled mounting holes", holes 11.4 apart across the width, 4.8 from the side, 2 from the ends, USB overhang 1.3 (typ), USB 8 wide. |
| UNV | Typical | Component heights, micro-USB height and plug size (**UNVERIFIED**). |
| Why not ESP32 | Espressif ESP32-DevKitC boards have no mounting holes | The Pico replaces it as the second "other common board". |

## Fasteners

| Item | Source | What it says |
|---|---|---|
| M2.5 hex nut | ISO 4032 / DIN 934. Supplier listing: https://aspenfasteners.com/m2-5-0-45-din-934-iso-4032-metric-hex-finished-nuts-class-6-steel-zinc-plated | Across flats 5.00 max (4.82 min), height 2.00 max (1.75 min). |
| M2.5 clearance hole | ISO 273 medium series (**UNVERIFIED** here, commonly quoted) | 2.9 mm medium clearance for M2.5. |

## Material: Formlabs Alpha Fast (`config.yaml` print / material_properties)

| Item | Source | What it says |
|---|---|---|
| Alpha Fast product page | https://formlabs.com/products/alpha-fast-resin/ (TDS: https://formlabs.com/materials/tds/alpha-fast-resin/) | Ultimate tensile strength 46 MPa, tensile modulus 2230 MPa, flexural strength 78 MPa, flexural modulus 2110 MPa, notched Izod 37 J/m, elongation at break 34 %, HDT @ 0.45 MPa 59 °C (post-cured at room temperature for 5 min, Form Studio Wash+Cure). Primary layer height 150 µm. "Alpha Fast Resin is only supported on the Form Studio 1." Post-cure "optional, but recommended". No accuracy, min wall or clearance values given (**UNVERIFIED** for design rules). |
| PreFormServer 3.64.2 on this PC | `/v1/print-setting/list/` (saved: results/tmp/preform_materials.json, not committed) | Form Studio 1 = machine type CLRK-1-2. Alpha Fast V1 = material code FLAPFA01, layer settings 0.150 mm and 0.250 mm, both "Default settings". |

## Printing (Formlabs)

| Item | Source | What it says |
|---|---|---|
| Formlabs Design Guide (Form 1+/Form 2, Clear resin, 100 µm) | https://pocc-techpark.media.uconn.edu/wp-content/uploads/sites/2880/2020/10/formlabs-design-guide.pdf (copy of Formlabs' guide) | Min supported wall 0.4. Min unsupported wall 0.6. Min clearance between parts 0.5 ("less than 0.5 mm may cause parts to fuse"). Min hole 0.5. Min engraved detail 0.4. Min embossed 0.1. Drain hole 3.5. Max overhang 1.0. Overhang angle ≥ 19°. The guide warns that other resins may vary. It does not cover the Form Studio 1, Alpha Fast or 150 µm layers, so these are our **generic** rules (**UNVERIFIED** for Alpha Fast). |
| Formlabs Engineering Fit | https://formlabs.com/global/blog/3D-printing-tolerances-for-engineering-fit/ | Recommends printing a test coupon for the exact resin and printer, which is what Phase 1 does. Gives no numeric values for Alpha resins. |
