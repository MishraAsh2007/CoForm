# FreeCAD: the agents' CAD tool

FreeCAD (free, open source) plays the role Onshape was meant to: a real parametric
CAD model the agents drive, with a desktop app people can open, inspect and edit.
No account or API key is needed. Tested with **FreeCAD 1.1.4** on Windows.

## How it works

1. **Master model, one per section family:** `data/freecad/master_<family>.FCStd`.
   A spreadsheet named **Params** holds `sc_length`, `sc_base_width`,
   `sc_wall_height` and `sc_thickness` (mm). The solids (`Part::Box` features fused or
   cut into **SupportArm**) take their sizes from those cells through expressions. Open
   it in FreeCAD, change a cell, press Recompute: the part rebuilds, exactly as the
   agents use it. The master is created on first use.
2. **Per candidate**, ScienceClaw runs `FreeCADCmd` headless with
   [`scienceclaw/freecad_worker.py`](../scienceclaw/freecad_worker.py): set the cells,
   recompute, require exactly one valid solid, then save the candidate snapshot
   `data/freecad/<candidate>.FCStd` and export STEP and STL. The master file is never
   overwritten. Each run takes about half a second.
3. **Physics check from the CAD kernel:** volume and second moment of area are read
   from the solid and must match the beam model's hand-derived section formulas within
   1%. On the three families they agree to every printed digit (e.g. U-channel 25 × 10 ×
   1.2 mm: 621.733 mm⁴ both ways). The exported STL also gets the closed-mesh, extents
   and volume checks.
4. The snapshot, STEP and master hashes are recorded in an immutable `cad_revision`.
   Downloads re-verify the hash, so a file edited after recording is refused.
5. The STL continues to PreForm, the specialist print review and the printer queue.

## Using it

- In the support-arm panel, **Build CAD model** builds the selected candidate.
  **Open in FreeCAD** opens its snapshot in the desktop app; **STEP** and **FreeCAD
  file** download it. Agent repairs build the new design's CAD automatically.
- **CAD source: FreeCAD parametric model** is the default for **Send to printer
  pipeline**.
- To edit a design by hand, open the snapshot, edit **Params** (or the solids), export
  STL, and use **Upload & check CAD**. The same checks say exactly what differs from
  the analyzed candidate.

API: `GET /api/freecad/status`, `POST /api/runs/{run}/candidates/{candidate}/freecad`,
`POST /api/runs/{run}/cad/{cad}/open`, `GET /api/runs/{run}/cad/{cad}/step|fcstd`.

ScienceClaw finds `FreeCADCmd.exe` under `%LOCALAPPDATA%\Programs\FreeCAD*`,
`Program Files\FreeCAD*` or `PATH`. Set `FREECAD_CMD` to override.
