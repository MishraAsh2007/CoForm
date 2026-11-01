# Onshape fit check: enclosure + Raspberry Pi 5 + Active Cooler

All files share **one coordinate frame**: the Raspberry Pi 5 board frame from the official drawing and STEP.
The origin is the PCB's bottom-left corner (the corner nearest the USB-C and power button). +x runs along the
85 mm edge toward Ethernet/USB, +y along the 56 mm edge toward the GPIO header, and z = 0 is the PCB bottom. Units
are mm. Import everything at the origin, with no moves, and the parts appear assembled.

| file | what | accuracy |
|---|---|---|
| `enclosure_base.step`, `enclosure_lid.step` | the print candidate. Exactly the geometry in the `.form` and in `../stl_sla/` (volumes 18.06 / 11.10 cm3 match PreForm) | exact |
| `rpi5_board_official.step` | Raspberry Pi Ltd's official model (`rpi-5b_no_graphics.step`, 2025-05-27, 2711 bodies), copied unchanged | official (licence below) |
| `active_cooler_keepout_APPROX.step` | **APPROXIMATE** keep-out of the Active Cooler: one box 63.5 x 42.5 footprint, z 3.65 to 14.0, plus two push-pin tip boxes under the PCB. Heights come from scaling the official drawing (about ±1 mm) | **approximate** |
| `plug_zones_APPROX.step`, `plug_zones/plug_<port>_APPROX.step` | the room each cable plug needs outside its port, using **typical plug sizes (UNVERIFIED)** | **approximate** |
| `rpi5_board_simplified.step` | PCB + connector bounding boxes (light stand-in for the official model) | boxes from the official STEP |
| `assembly_enclosure_pi5.step` | **one combined assembly**: base, lid, cooler keep-out, simplified board, plug zones | see note |

**About the combined assembly:** it uses the *simplified* board, not the official one. Merging the 77 MB official
model into one file needs more RAM than this PC has free. Import `rpi5_board_official.step` next to it. It lands
in the same place.

**Licence of the official Pi STEP:** MIT licence, Raspberry Pi Ltd (copy in
`rpi5_board_official_LICENSE.txt`), so you may share it as long as the notice is kept. It is **not committed to git**
because of its size (77 MB). Run `python -m cad.print_package step` to recreate it from `results/tmp/dl/step/`.
The licence also says the model is "for guidance only" and that you should measure the real board. See
`../RISK_CHECK.md`.

## 1. Import

1. In Onshape: **Create → Document**, name it `Pi5 enclosure fit check`.
2. **Import** (the upload icon at the bottom-left "+" menu, or drag the files onto the tab bar). Import
   `enclosure_base.step`, `enclosure_lid.step`, `active_cooler_keepout_APPROX.step`, `plug_zones_APPROX.step`
   and `rpi5_board_official.step` (or unzip `onshape_step.zip` and pick the files).
   - If the import dialog asks about the up-axis, keep **Z up**. Do **not** tick "Y axis is up".
   - If it offers to **flatten the assembly into a single Part Studio**, accept. The official Pi model and the
     combined assembly import as assemblies otherwise.
   - The official Pi model is large (2711 bodies). Give the import a minute or two.
3. Each STEP becomes its own tab. To put everything in **one Part Studio**: create a new Part Studio, then use
   **Derived** (toolbar) once per imported tab and select all parts. Derived parts keep their position, so they
   appear assembled. Check this: the board sits on the four standoffs and the USB-A/Ethernet fronts poke through
   the +x wall.

## 2. Show there is no overlap (Boolean intersect, keep tools)

Do this once per pair below. Afterwards **delete or roll back the feature**, so the Part Studio stays clean.

1. **Boolean** → **Intersect**. Tools: the two bodies to test (for example `enclosure_lid` and the cooler keep-out).
   Tick **Keep tools**.
2. **Pass:** Onshape reports that the result is empty (the feature turns red / "no intersection"). That error **is
   the pass**. It means the two solids share no volume.
   **Fail:** a new small body appears. Measure it: that is the interference.

| pair | expected |
|---|---|
| `enclosure_base` × every body of the official Pi board | empty |
| `enclosure_lid` × every body of the official Pi board | empty |
| `enclosure_lid` × cooler keep-out | empty |
| `enclosure_base` × cooler keep-out (incl. pin tips) | empty |
| `enclosure_base` × `enclosure_lid` | empty (lid closes) |
| `enclosure_base` / `enclosure_lid` × each `plug_<port>` | empty |

With the official model, select its bodies by box-selecting in the parts list. If Onshape is slow, test only the
connector bodies near each wall.

## 3. Measure the key gaps

Use the **Measure** tool: select two faces (or two parts), and the bottom-right panel shows the **minimum
distance**. Expected values come from our own exact distance check (`../risk/margins_sla.json`). If Onshape shows
less than these, something moved.

| gap | how to pick it | expected (SLA print) |
|---|---|---|
| **Lid over the fan** | underside face of the lid plate over the vent ↔ top face of the cooler keep-out | **4.4 mm** (lid underside z 18.4, cooler top z 14.0, approx) |
| Lid lip over the cooler edge | lid (whole part) ↔ cooler keep-out | **1.4 mm** |
| Lid over the tallest part (USB-A stack) | lid ↔ USB-A metal shell of the official model | 0.8 to 0.9 mm (box 17.6 / STEP 17.53 top) |
| Board edge to wall | PCB edge face ↔ base inner wall face, each side | 0.5 mm |
| **USB-C** opening around plug zone | base ↔ `plug_usb_c_power` | 0.60 mm |
| **HDMI 0 / HDMI 1** | base ↔ `plug_hdmi0`, `plug_hdmi1` | 0.60 / 0.60 mm |
| **Ethernet** | base ↔ `plug_ethernet` | 1.44 mm |
| **USB-A lower pair** (next to Ethernet) | base ↔ `plug_usb_a_lower` | 1.30 mm |
| **USB-A upper pair** (next to GPIO, at the corner) | base ↔ `plug_usb_a_upper` | **0.07 mm**: the corner lid-screw boss. See RISK_CHECK.md |
| power button access | base ↔ `plug_power_button` | 0.60 mm |
| microSD finger access | base ↔ `plug_microsd` | 0.70 mm |

The plug zones are boxes of *typical* plug sizes (UNVERIFIED). A gap here says how much bigger than "typical" your
real plug can be, per side. The FDM variant (`../fdm/`) is not in these STEP files. Its gaps are listed in
`../RISK_CHECK.md`.
