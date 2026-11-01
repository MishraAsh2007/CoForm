# Autodesk Fusion: agents and people editing the same design

A small add-in ([`fusion/ScienceClaw`](../fusion/ScienceClaw)) connects Fusion on this
laptop to the local ScienceClaw server. No API key, cloud service or Flex tokens are
used; it works with a Fusion student license. The add-in has not yet been run inside
Fusion from this workspace. ScienceClaw's side is tested against a simulated add-in.

## What it does

- **Agents → Fusion.** **Build in Fusion** (or CAD source *Autodesk Fusion* in the
  printer pipeline) sets four Fusion user parameters, `sc_length`, `sc_base_width`,
  `sc_wall_height` and `sc_thickness`. Fusion recomputes the parametric timeline,
  so **features you added yourself (fillets, holes, tabs) are kept**. It then exports
  STL and STEP. ScienceClaw checks the export against the section the physics
  analyzed: closed mesh, extents, volume, and Fusion's own physical-properties volume.
- **You → ScienceClaw.** Edit the design in Fusion, then press **Send to ScienceClaw**
  (Utilities tab, Add-Ins panel). ScienceClaw:
  - turns changed `sc_base_width` / `sc_wall_height` values into a **new candidate**
    marked `human:fusion`, linked to the design it came from;
  - runs the diagnosis and **shows the requirements verdict in a Fusion message**
    (deflection, material, stress, ranges), plus any problems found;
  - reports edits the beam model cannot represent. A fillet or hole changes the solid,
    so the CAD check fails and the diagnosis says what differs, instead of trusting a
    prediction for a different part. Frozen values (`sc_length`, `sc_thickness`) and
    off-grid sizes are refused with the reason.
  - The new candidate appears on the ScienceClaw page within about 8 seconds. From
    there the agents can diagnose it, **Let the agents fix it**, or send it to the
    printer pipeline.

## Install (once, about 5 minutes)

For **Part designer → Open in Fusion**, use add-in **0.3.0** from this repository.
If Fusion is already running an older copy: **Utilities → Scripts and Add-Ins → Add-Ins →
ScienceClaw → Stop**, then **Run**. The page now identifies incompatible versions before
queueing a job. This action imports the selected generated STEP into a new document; it does
not use the predefined study template or require **ScienceClaw: set up**.

The page waits for Fusion's actual completion result and displays import errors. Repeated
clicks reuse a pending job, and completed jobs are not replayed after a server restart.
The import helper requires an actual Document return value, as documented by Autodesk:
[ImportManager.importToNewDocument](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_ImportManager_importToNewDocument.htm).

1. Start ScienceClaw: `.\.venv\Scripts\python.exe -m scienceclaw.cli serve`.
2. In Fusion: **Utilities → Add-Ins → Scripts and Add-Ins** (Shift+S) → **Add-Ins**
   tab → **+** (or the green plus) → select the folder `ScienceClaw\fusion\ScienceClaw`
   in this repository → select **ScienceClaw** → **Run**. Optionally tick *Run on
   Startup*.
3. Open or create a design. Press **ScienceClaw: set up** (Utilities → Add-Ins panel)
   and enter the study's section family (`u_channel`, `solid_bar`,
   `rectangular_tube`). This creates the four user parameters and, if the design is
   empty, a sketch on the YZ plane with dimensions bound to them, extruded by
   `sc_length`. Check it under **Modify → Change Parameters**.
4. In ScienceClaw, click **Check Fusion**. It should say *connected*. Then **Build in
   Fusion** for any candidate.

If you model the part yourself instead, dimension the sketch with the parameter names
(e.g. type `sc_base_width` into a dimension). The geometry check verifies the result
either way.

## Notes

- The add-in polls `http://127.0.0.1:8000` every 2 seconds; set `SCIENCECLAW_URL` before
  starting Fusion to change it. Fusion API calls run on Fusion's main thread.
- STL exports from Fusion may be in centimeters. ScienceClaw rescales by 10 only when
  centimeters exactly explain the expected extents, and records that it did.
- Each Fusion revision stores the STL (print asset), the STEP (hash-checked download),
  the parameter expressions, the Fusion document name and version, and whether it came
  from an agent build or a user edit.
- Fusion must be open with the design active for agent builds. If it is closed, ScienceClaw
  says so rather than waiting.
