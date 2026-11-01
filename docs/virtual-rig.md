# Virtual test rig (3D finite elements)

Simulated studies can take their measurements from a finite-element model of the actual part,
instead of the beam formula with built-in material and clamp values. The agents' beam model then
has to match an independent, higher-fidelity model, so their discrepancies are real physics
rather than planted numbers.

## Pipeline

1. **Geometry.** The newest checked CAD revision's STEP (Fusion or FreeCAD) when the app has
   one; otherwise the identical section generated from the candidate's parameters (unattended
   comparisons). A STEP is turned about its length axis until it matches the analysed part
   (length X, width Y, height Z, channel opening up). Fusion's export arrives turned 90°
   because the add-in sketches on the YZ plane. A STEP of a different part is refused
   (`GeometryMismatch`) and the generated section is used, with a note on the observation.
2. **Mesh.** Gmsh, 2 mm quadratic tetrahedra, with conformal cuts at the clamp line and at
   every load station. On the default U-channel this is within 1.5% of a 0.8 mm mesh, below
   measurement noise.
3. **Solve.** CalculiX 2.22, linear elastic. A unit vertical load is spread over the station's
   cross-section; the reading is the mean vertical displacement there. All stations are solved
   in one run and cached per geometry, stiffness and fixture (about 5–10 s per new design).
4. **Measurement.** The deflection times the applied force, plus the scenario's sensor noise.

Gmsh and CalculiX ship with FreeCAD 1.x (`bin/gmsh.exe`, `bin/ccx.exe`). Override them with
`GMSH_EXE` and `CCX_EXE`.

## Materials (hidden from agents)

The project prints in **Formlabs Alpha Fast Resin** only. Each study draws one resin batch
somewhere between green and fully post-cured:

| Material | Tensile modulus | Source |
|---|---|---|
| Formlabs Alpha Fast Resin (default) | 1.67 GPa green · 2.23 GPa default cure · 2.54 GPa max-strength cure | Formlabs TDS FLAPFA01 V1, 2026-09-28, Form Studio 1, ASTM D638 |
| Standard photopolymer (Formlabs Clear/White/Grey/Black V4) | 1.6–2.8 GPa | Formlabs Standard Resins TDS |
| Formlabs Rigid 10K | 7.5–10 GPa | Formlabs Rigid 10K TDS |

The Poisson ratios are typical values, assumed because they are not on the data sheets.

## Clamp (fixture)

The clamp is modelled as bonded jaw pads over the clamped length with their outer faces held, so
mount rotation comes from the jaw material:

| Fixture | Jaws | Extra deflection over a rigid clamp (default part, 60 / 120 mm) |
|---|---|---|
| Steel vise | none; clamped length fully fixed | — |
| Printed jaws | 3 mm resin, 2.2 GPa | +31% / +16% |
| Rubber-lined | 1.5 mm neoprene, 5 MPa (typical) | +57% / +30% |

## Scenarios

`validation.RIG_SCENARIOS`, also listed under *Simulated test rig* in the New study form. All use
Alpha Fast, a dial indicator (5 µm noise), a 0.25 mm bend limit and a 0.02 mm measurement margin:

- `rig_alpha_fast_printed_jaws`
- `rig_alpha_fast_rubber_clamp`
- `rig_alpha_fast_steel_vise`

At 0.3 N the 60 mm station bends about 20 µm, so the model can be calibrated. With the default
2 mm limit the smallest allowed design passes (a trivial search); at 0.25 mm the lightest passing
design is 73–82% of the starting resin and depends on the hidden cure state. Offline results on
seeds 15 and 16: lighter designs passed after 3 tests, and held-out prediction errors were 4–13 µm.

An earlier version used 15 µm noise against a 0.05 mm margin. Even the optimizer could not
calibrate the model at 60 mm, and the measurement agent, whose signal-to-noise tool then used the
allowed uncertainty instead of the measured noise, vetoed every test. Both are fixed.

Example:

```
python -m coform.cli compare --seeds 17 18 --methods adaptive collective single_agent independent   --scenarios rig_alpha_fast_printed_jaws rig_alpha_fast_rubber_clamp --output data/rig-comparison
```

## Limits

Linear elasticity: no creep, no large deflection, no contact slip in the clamp. The stiffness is
uniform (no print-orientation anisotropy), and these are not printed measurements. Physical prints
remain the evidence for a real pass.
