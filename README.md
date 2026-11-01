# CoForm Design Lab

An implementation for the MIT ScienceClaw hackathon: three specialists propose and review experiments, with **Onshape for production CAD and Formlabs PreForm for print preparation**. CoForm coordinates evidence and decisions.

**Current on-site target:** Formlabs Form Studio 1; Rigid 10K Resin and another resin whose exact product name is pending. PreForm Insider 3.65.0.1096 is connected locally. Its current Studio 1 catalog does not list Rigid 10K, and no actual printable device is connected yet. The integration reads catalog settings instead of guessing compatibility.

**Working now:** agent candidates drive an Onshape Part Studio (variables → immutable version → STL export), the exported mesh is checked against the analyzed section, PreForm prepares and checks it, and the three specialists must approve before a study candidate can be queued. Also: offline beam experiments for three section families and reviewed queue-upload code. One real PreForm preparation passed all seven requested checks. The Onshape link is tested against a fake server only; it needs your API keys and a one-time Part Studio setup ([Onshape pipeline](docs/onshape.md)). No physical print or successful live three-provider round has been validated.

Read [Autodesk Fusion add-in](docs/fusion.md), [FreeCAD CAD integration](docs/freecad.md), [Onshape → print pipeline](docs/onshape.md), [Formlabs setup and current hardware findings](docs/formlabs.md) and [validation status](docs/validation-status.md).

## Run locally

Python 3.11+ is required. A configured `.venv` already exists in this workspace.

```powershell
# Existing workspace:
.\.venv\Scripts\python.exe -m coform.cli serve
```

Open **http://127.0.0.1:8000**. Create a simulated study, then choose **Offline step** or **Complete offline demo**. No keys, printer or camera are needed for the offline demo.

For a clean installation:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,formlabs]"
.\.venv\Scripts\python.exe -m coform.cli serve
```

## Enable all three models

Copy `.env.example` to `.env` and fill it in **locally**. Do not paste keys into chat or commit them. Configure the exact model identifiers your API accounts can access.

| Specialist | Default provider | Environment variables |
|---|---|---|
| Mechanics | OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` |
| Fabrication | Anthropic Claude | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| Measurement | Google Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL` |

Restart the server after changing environment variables. **Run live agent round** calls all three configured providers to propose actions and independently review their peers. It sends numerical evidence and bounded tool outputs; keys stay server-side. Each run freezes an aggregate token budget and a maximum discussion-round count. Responses and usage are recorded; invalid responses or failed calls do not silently switch to offline mode. See [provider details](docs/providers.md).

The creation form lets you set the aggregate token budget. Its 12,000-token default is a small integration pilot and may cover only one decision round with some models. Increase it before creating a study if you want several live rounds. Each live round still requires a button click; the complete-demo button always runs offline.

**Model execution and evidence source are separate.** Live agents can work with simulated observations. That still produces simulated results. The offline collective uses explicit rule-based policies to test the infrastructure; it does not demonstrate LLM collective capability.

## What works

- Autodesk Fusion add-in ([docs](docs/fusion.md)): agents set Fusion user parameters and Fusion rebuilds while keeping your own features. **Send to CoForm** turns your edits into checked candidates, with the requirements verdict shown in Fusion.
- Baselines for the rubric's "does the collective add capability?" question, with the same tools, evidence, checks and token budget: a **single agent** that owns every responsibility (it retries with validator feedback), and **specialists without evidence exchange** (independent proposals, no review or revision; the plurality valid action wins). `compare --live` runs them against the collective and the adaptive optimizer on identical simulated tasks.
- NVIDIA-hosted models (Nemotron) as an agent provider; see [providers](docs/providers.md).
- Design requirements, frozen per study: design load and position, maximum deflection, base-width and wall-height ranges, and optional material budget and allowable root stress. One function (`mechanics.requirement_checks`) judges every design for the optimizer, diagnosis, agent proposals, repairs and print review. When the requirements conflict, the diagnosis names the binding one.
- FreeCAD as the agents' CAD tool ([docs](docs/freecad.md)): a parametric master model per section family, a headless rebuild per candidate, `.FCStd`/STEP/STL snapshots, and CAD-kernel volume and second moment checked against the beam formulas.
- Part designer ([docs](docs/designer.md)), at `/designer`: describe a part in plain words and the agents draft it as parametric CAD, test it with 3D finite elements, adjust it until it passes with the least resin, open it in Fusion or export STEP/STL, and send it through PreForm checks and an agent print review to the printer queue. A measured bend then recalibrates the stiffness.
- Virtual test rig ([docs](docs/virtual-rig.md)): simulated measurements from a 3D finite-element model (Gmsh + CalculiX, bundled with FreeCAD) of the actual Fusion/FreeCAD geometry, with resin stiffness drawn from the Formlabs Alpha Fast Resin datasheet range and clamp looseness from the jaw material instead of built-in values.
- Diagnosis per candidate (`GET /api/runs/{run}/candidates/{candidate}/diagnosis`): each problem with its owning specialist, numeric evidence and concrete fixes. The physics shows the bending vs mount-rotation split, margin to the limit, root stress, and how much one grid step of width or wall height buys. When measurements disagree with the model, the station pattern says whether lower stiffness or mount rotation explains it better. CAD mismatches and PreForm issues also appear.
- Agent repair (`POST .../repair`, offline rules or live models): the specialists each propose a fix with their own priority (least material, smallest change, most margin), review each other, and with two supporters create a new candidate linked to the flawed one. They refuse to redesign from a model that the measurements contradict and ask for the diagnostic test instead.
- Onshape link: candidate dimensions → `sc_*` Part Studio variables → one immutable Onshape version per candidate → millimeter STL export. Mesh volume/extents/closure must match the analyzed section before PreForm preparation.
- Specialist print review: mechanics, fabrication and measurement review CAD and PreForm evidence (offline rules or the three live providers); two supporters plus non-waivable gates are required before a study candidate can be queued.
- U-channel, solid-bar and rectangular-tube simulation fixtures with width/height search and frozen family, length, thickness and material per study.
- Watertight millimeter STL fixtures, content hashes and immutable parent-linked designs. Legacy SCAD/SVG artifacts remain in evidence exports. Production geometry belongs in Onshape; the local 3D editor-style preview has been removed.
- Official Formlabs SDK integration for catalog lookup, preparation, checks, saved .form projects and explicit queue upload with duplicate-attempt protection.
- Correct second moment of area using the shifted neutral axis and parallel-axis theorem.
- Bending plus rotational mount compliance: `delta/F = a^3/(3*E*I) + C_theta*a^2`.
- Nonnegative weighted parameter fitting, conditioning, residuals and uncertainty. One station does not identify two parameters. Poor fits remain explicit.
- Independent agent proposals and reviews, evidence references, at least two supporters, deterministic validity checks, bounded discussion and a reproducible tie-break.
- SQLite append-only records, an event hash chain, per-agent local state, metered decisions, physical/design budgets and a single pending fixture reservation per study.
- Seeded simulation; manual physical measurements with fabrication records and actual force/station metadata; calibrated marker CSV reduction through `/api/trace/analyze`.
- A prediction committed before the reserved observation; reserved and acceptance observations excluded from fitting and agent search context.
- Frozen final nomination, three independent acceptance-copy records, uncertainty guard bands and distinct `simulated_pass`/`physical_pass` statuses.
- Adaptive numerical baseline, paired offline comparison, and ZIP exports with evidence and print artifacts.

## Print and physical-test workflow

1. Create a **physical** study after selecting pilot settings. The protocol freezes at creation; changed settings require a new study.
2. For each candidate to print, choose **Send to printer pipeline** (Onshape → geometry check → PreForm → specialist review), then upload the approved project to the printer queue. Manual Onshape STL import still works for parts outside a study. Candidate fixture STLs are available for software testing. Defaults are software bounds, not manufacturer-qualified minimum walls or material properties.
3. Choose **Offline step** to queue the shared pilot cycles. Register the requested specimen ID, actual dimensions, fabrication time and process notes. Keep the reference mounting condition unchanged during diagnosis.
4. Record measured force including the load holder, load station, displacement, uncertainty, residual displacement, fixture motion, dwell/recovery times and quality flags. An observation is immutable. Manual measurement notes are required; CSV evidence can be attached through the API.
5. After four shared cycles, use live agent rounds or the adaptive baseline. New geometry creates new files and a fabrication record. The system waits for human printing, mounting and loading.
6. Run a reserved prediction check after an identifiable fit. Freeze a final design before recording independent-copy acceptance cycles.

See [measurement protocol and marker CSV format](docs/measurement.md). Automatic webcam capture/marker detection is not implemented yet. The CSV reducer is ready to receive tracked points. No physical performance or resin calibration has been established.

## Comparisons and exports

```powershell
# One offline simulated run:
.\.venv\Scripts\python.exe -m coform.cli demo --method collective --seed 7

# Same initial evidence, protocol, geometry and budgets for each paired seed:
.\.venv\Scripts\python.exe -m coform.cli compare --seeds 7 8 9 --output data/comparison

# Live agents (uses model credits): four methods, two scenarios, two seeds, equal budgets:
.\.venv\Scripts\python.exe -m coform.cli compare --live --seeds 7 8 --methods adaptive collective single_agent independent --scenarios nominal stiff_material_loose_mount --token-budget 500000 --output data/comparison-live

# Export a run (substitute the actual run ID):
.\.venv\Scripts\python.exe -m coform.cli export RUN_ID --output data/exports

# Tests:
.\.venv\Scripts\python.exe -m pytest -q
```

The comparison writes JSON, CSV and complete run exports. It reports nominal **geometry volume**, not measured mass, plus individual acceptance observations, reserved prediction error, tests, designs, numerical evaluations, tokens, calls and human effort. Offline comparisons measure the numerical workflow and rule-based policy only. Identical results are retained honestly. The expanded matrix completed 72 simulated runs across three families and six synthetic scenarios; see docs/validation-status.md. These are software checks, not research findings.

The simulator uses a beam model with configurable hidden stiffness/mount parameters, measurement noise, fixture motion, residual set and synthetic nonlinearity. This favors model-based policies and is not a broad benchmark. Printer variability, supports, post-curing, creep, damage and nonlinear behavior require real tests or additional simulator families. The initial implementation does not include the full single-agent/no-exchange ablations or independent final reference-copy comparison from the proposal.

## Layout

| File | Responsibility |
|---|---|
| `domain.py` | Frozen SI-unit contracts and protocol |
| `mechanics.py` | Geometry, STL/SCAD/SVG, prediction, fitting, optimization |
| `measurement.py` | Trace reduction and deterministic acceptance |
| `agents.py` | Proposals, peer reviews, consensus, specialist state |
| `providers.py` | OpenAI, Anthropic and Gemini REST adapters |
| `cad.py` | FreeCAD and Onshape backends, manual-export upload, mesh-vs-section checks, print pipeline |
| `freecad_worker.py` | Runs inside FreeCAD: parametric master model, rebuild, exports, kernel properties |
| `diagnosis.py` | Problems, physics breakdown and fix options per candidate |
| `formlabs.py` | Official PreForm adapter, model assets, preparation and queue receipts |
| `budget.py`, `validation.py` | Persisted live-spend guard and robustness matrix |
| `store.py` | Immutable SQLite records and hashed artifacts |
| `service.py` | Experiment lifecycle and budgets |
| `app.py`, `static/` | Local API and review interface |
| `comparison.py`, `cli.py` | Offline comparisons, export and launch commands |

All calculations and JSON quantities use SI units; display labels convert to mm/GPa/cm³. Geometry volume is `section area × total length`, excluding print supports and attachments. The U-section base spans the full outside width; side walls rise above the base. Effective stiffness is local to the tested geometry/process family, not a certified intrinsic resin property.

The server binds to `127.0.0.1` and has no multi-user authentication. Keep it local. The event chain detects accidental alteration; it is not external attestation of experimental authenticity. API routes are documented at `/docs` and `/openapi.json`.
