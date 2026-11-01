# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

"Bracket Collective": a multi-agent system for designing a lightweight ribbed L-bracket for SLA resin printing. Work follows an approved milestone plan (M0–M9) at `~/.claude/plans/what-are-next-steps-recursive-seahorse.md`. Done: M0 (config, schemas, store, filleted geometry), M1 (L1 analytical mechanics), M2 (PreForm client), M4 (L2 FEA), M5 (level selection + L1 calibration; `coordination.Budget`), M6 (optimization, selection, baselines; L1 feedback loop), M8 (Claude agents + `main.py`), M9 (`compare.py`; 6-seed batch done, see below). Not built: M3/M7 (measurement intake and L3 calibration; a first specimen is being printed).

Do not read or print `.env` (local credentials, e.g. `LLM_API_KEY`, `PREFORM_PORT`, consumed by `config.py`).

## Setup

Python 3.11+. The virtualenv lives one level up, outside the repo, at `../.venv` (`scienceClaw/.venv`, Python 3.12). From this directory:

```powershell
..\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Without activating, call `..\.venv\Scripts\python.exe` directly.

Tests (pytest, configured by `pytest.ini`; run from this directory):

```powershell
python -m pytest                                   # all tests
python -m pytest tests/test_analytical.py -k plate  # one test
python -m pytest -m "not freecad"                  # skip tests that launch FreeCAD
$env:RUN_PREFORM_TESTS=1; python -m pytest -m preform  # live PreForm test (~75 s, server must run)
$env:RUN_SLOW_TESTS=1; python -m pytest -m slow        # full L2 FEA convergence study (~2 min)
python bracket.py 3 20 4                           # build one bracket into runs/smoke/c0/
python preform_client.py                           # prepare runs/smoke/c0/bracket.stl -> bracket.form
python main.py --run-id demo --mode collective --rounds 3   # agents (Claude API, costs money)
python main.py --run-id base1 --mode optimizer               # baseline, no LLM
python main.py --run-id cmp1 --mode collective --calibration-start reference  # fair comparison start
python compare.py run --prefix m9 --seeds 3   # M9 batch: 4 methods x seeds, resumable (~1.5-2.5 h)
python compare.py report --prefix m9          # -> runs/m9-report.md / .json
```

`main.py` modes: `collective` (3 agents), `single` (one agent with every tool), `optimizer`, `random`. Budgets: `--budget-l1/--budget-l2-fea/--budget-preform-prep/--budget-llm-calls` (defaults in `config.BUDGETS`); compare modes only with identical budgets. PreForm is used if its server answers, unless `--no-preform`; without it nothing can be selected. Outputs: `runs/<run>/{selection.json, run.json, messages.jsonl, proposals.jsonl, turns.jsonl}`.

FreeCAD is not pip-installable: install FreeCAD separately and set `FREECAD_PYTHON` in `.env` to its bundled interpreter (default `C:\Program Files\FreeCAD 1.1\bin\python.exe`). Smoke test: `& $env:FREECAD_PYTHON -c "import FreeCAD, Part, MeshPart"` (after setting the variable in the shell).

PreForm must be running separately with its API server started (File → Start PreForm Server, default port 44388; tested with PreForm 3.65, API is alpha). `preform_client.connect()` attaches to it.

`preform_client.prepare_print(preform, stl, PrintSettings, form_path)` does the following, then deletes the scene:
1. Creates its own scene (printer/resin from `config.PRINTER_*`, currently Form Studio 1 + Alpha Black V1).
2. Imports the STL in mm.
3. Orients it (auto, or Euler angles from `PrintSettings`).
4. Auto-supports it (optional density and touchpoint size) and lays it out with rotation locked.
5. Runs the 7 checks, then records print time and resin volume and saves the `.form`.

Behaviour to keep:
- **Takes ~75 s per candidate**, so budget PreForm preparations accordingly.
- **Never touch other scenes:** the user may have their own scene open in PreForm.
- **Failures are recorded, not passed:** an SDK error or a non-`ANALYZED` thin-wall status makes that check `failed`. A preparation failure sets `error` and marks every check `not_evaluated`.
- **No print submission:** there is deliberately no call to `scene/print`.
- **SDK return values:** the SDK's typed facade methods return attrs objects, and the client works on their `.to_dict()` form. Look up exact field names in `formlabs_preform_api/models/*.py` in `../.venv` rather than guessing (e.g. an imported model's id is `model_id`).

## Architecture

Objective: minimize finished-part mass subject to structural and manufacturing constraints, and test whether three collaborating agents beat a single agent and a conventional parameter search under the same evaluation/model-call budgets.

Flow: agents post structured proposals to a shared record → `coordination.py` queues/deduplicates them and enforces budgets (start with round-robin, sequential execution) → `bracket.py` builds the solid and exports STL/STEP → `structural_evaluator.py` and the PreForm client evaluate it → results go to `store.py` and back to all agents → `selection.py` picks the lowest-mass feasible candidate.

Three Claude agents (Anthropic SDK, `config.CLAUDE_MODEL`), replacing the README's structural/weight/manufacturing split:
- **Mechanic:** structural evaluation at three levels: L1 analytical (`analytical.py`), L2 FEA with mesh-convergence checks (planned: FreeCAD FEM + bundled gmsh/CalculiX), L3 calibrated against measurements. Picks the level automatically by uncertainty, cost and required accuracy.
- **Fabrication / Design Optimization:** constrained optimization, Pareto, sensitivity, optional Bayesian optimization, uncertainty-aware selection, equal-budget method comparisons. Also **owns PreForm** (orientation, supports, checks, print time).
- **Measurement:** parameter estimation, uncertainty propagation, residuals, hypothesis comparison, discrepancy detection and held-out validation against real measurements (expected soon), kept separate from synthetic data and predictions.

Agents never edit CAD directly or run generated code; they call Pydantic-validated tools, and all numerics are deterministic Python. Prompts require citing real candidate/result ids. Adjustable geometry is only plate thickness, rib height and rib thickness (`config.PARAMETER_BOUNDS`). Envelope, fillet, load case, material, limits, L1 uncertainty and budgets live in `config.py`, all marked PLACEHOLDER until chosen.

Provenance is enforced in `schemas.py`, so don't work around it:
- **`Quantity`:** every number carries `source` (analytical/fea/calibrated/surrogate/measured/synthetic/preform) and `status`. A value exists only when the status is `completed`. `converged` is FEA-only and `validated` is calibrated-only.
- **Selection evidence:** `Quantity.counts_for_selection` is true only for converged FEA, validated calibrated results, or measurements. L1 and surrogate results are "promising", never selectable.
- **`CheckResult`:** a PreForm check can't pass unless it `completed`.
- **Candidate ids:** `GeometryParams.candidate_id()` is a hash of the parameters plus the fixed envelope, used for dedupe and caching.

L1 (`analytical.py`) treats the arm as a cantilever clamped at the wall's inner face. Its section is the plate plus a linearly tapering rib, and tip deflection comes from integrating M/EI numerically. It reports nominal bending stress with no stress concentrations, attaches relative uncertainty from config, and adds warnings outside its validity range (deep beam, large deflection, stress above strength). `volume_mm3()` matches FreeCAD's volume to within 0.005%; the rib-fillet corners are approximated.

L2 (`fea.py`) drives FreeCAD's bundled `gmsh.exe` and `ccx.exe` directly from `.venv`, not through FreeCAD's FEM module:
1. gmsh meshes the STEP into straight-sided C3D10 tetrahedra. Curved quadratic elements invert on the 1 mm fillets, so they're avoided.
2. The code writes the CalculiX deck itself: the Y=0 face is fixed, a consistent nodal load represents uniform downward traction on the Y=ARM_LENGTH face, and the solver is `ITERATIVE CHOLESKY` (the default direct solver needed 8+ GB on big meshes).
3. It parses `job.dat`.

`evaluate_l2` solves 3 meshes. Element size shrinks by `FEA_REFINEMENT_RATIO`, and fillet refinement via `Mesh.MeshSizeFromCurvature` scales with it. It then applies the Celik (2008) GCI. A quantity is `converged` only if it is monotonic and its GCI is under `config.FEA_GCI_MAX_*`; reported values are from the finest mesh.

Non-monotonic (oscillatory) sequences are accepted with uncertainty = `FEA_OSCILLATORY_FACTOR` (3) × the spread of the three values, relative to the finest (Eça & Hoekstra), against the same thresholds (user decision 2026-11-01). Thin-plate designs agreeing within 0.5% had been rejected under the strict rule. After changing convergence rules, run `python structural_evaluator.py rescore`, which re-judges the stored calibration rows from their saved mesh levels without re-running FEA. The observed order is capped at `FEA_FORMAL_ORDER` (2) for the GCI, and every FEA uncertainty has a floor of `FEA_MIN_REL_UNCERTAINTY` (0.5%). Without these, coincidentally agreeing meshes produced an observed order of 16.5 and a deflection uncertainty of 3e-8 mm.

History: in the first end-to-end run (3.7/49.1/3.2), the old depth-only calibration was 6% low on deflection and 24% high on stress. The optimizer therefore stopped at a design with ~35% unused stress margin. That prompted the feedback loop and feature selection described below; after them, the same design is predicted within −4% (deflection) and −2% (stress).

Peak von Mises stress excludes a band near the fixed and loaded faces. The band is `max(FEA_SINGULARITY_EXCLUSION_MM, coarsest element size)`, the same on all meshes so each mesh measures the same region. The rib-to-plate edges carry `config.RIB_FILLET_RADIUS` fillets (in `freecad_bracket.py`) specifically so their stresses are finite. Without them, peak stress never converges.

With `FEA_HALF_MODEL` (default), gmsh cuts the solid at X = BRACKET_WIDTH/2 with an OpenCASCADE boolean. The model carries half the load, and the symmetry plane is restrained in X. This matches the full model within 0.5% at about 4× less cost. Levels are solved finest first, so an oversized mesh fails in seconds. That matters because the optimizer's preferred designs (thin walls, ~49 mm ribs) exceeded 200k elements as full models.

Measured on the default 4/30/5 bracket:
- With the half model, each study takes about 30 s (37.9k elements on the finest mesh). The full-model numbers that follow were taken before the switch: about 2 min (6 + 20 + 101 s; 14k / 40k / 116k elements).
- Deflection converges (~1.213 mm, GCI ~0.1%).
- The rib-toe fillet stress (~34 MPa) has a GCI of ~10%. The user accepted a 12% stress threshold rather than 5–8 min studies, so selection must compare stress value + uncertainty against the limit.
- L1 underpredicts that deflection by ~21%. L1's nominal stress (17.8 MPa) excludes the ~1.9× fillet stress concentration, so it is non-conservative.

`gmsh` itself takes seconds; CalculiX solve time dominates. `FEA_MAX_ELEMENTS` (200k) makes runaway meshes fail fast.

Level selection (`structural_evaluator.evaluate(params, step_path, accuracy, budget, request_level)`):
- **L1 always runs**, corrected by `data/l1_calibration.json` when that file is valid. The file records a geometry/Poisson fingerprint and is ignored if those change; load and E cancel out of the L2/L1 ratios.
- **L1 verdict:** feasible, infeasible or ambiguous. A limit counts as decided only beyond `L1_DECISION_SIGMAS`. Uncalibrated L1 stress is a lower bound: it can prove a violation but never feasibility.
- **When L2 runs:** for an explicit `request_level=2`, for `accuracy="selection"` (unless L1 already proves infeasible), or when the verdict is ambiguous. In each case only if a STEP file is given and `budget.can_spend("l2_fea")`.
- **Every choice is explained:** each run or skip is recorded in `Evaluation.decisions`.
- **L3 is not built yet** (needs measurements, M7).

`coordination.Budget` counts spend per `config.BUDGETS` key. Baselines and agents must share the same budget accounting.

L1 calibration (the Mechanic's feedback loop):
- **What's stored:** `data/l1_calibration.json` holds raw FEA rows (params, L2 quantities, mesh levels; source `reference` or `experiment`), not fitted numbers. The fits are recomputed on load, using the current `analytical.py` and `config.L1_CALIBRATION_FEATURES`, and cached by file mtime, features and geometry fingerprint. So improving L1 or changing features never needs new FEA.
- **The model:** `fit_quantity` fits `log(L2/L1) = β·[1, features]` over the rows where that quantity converged. Candidate features are in `structural_evaluator.FEATURES`.
- **Uncertainty:** `correction` returns a regression prediction interval, √(s²(1+leverage) + numerical²), where numerical is the RMS of the FEA GCIs. The interval grows for designs far from the data and notes any extrapolation.
- **Feeding it:** `record_fea(params, l2)` adds or replaces a converged row and refits. `coordination.run_experiment` calls it after every FEA, so every agent or baseline FEA improves L1.
- **Building it:** `python structural_evaluator.py calibrate` FEA-solves any `L1_REFERENCE_DESIGNS` that are missing (~30–60 s each with the half model).
- **Choosing features:** automatic (`config.L1_CALIBRATION_FEATURES = "auto"`). On every refit, `select_features` picks the simplest set (at most 2 features) whose leave-one-out RMSE is within 10% of the best, considering only sets with at least `L1_MIN_ROWS_PER_COEFFICIENT` (3) converged rows per coefficient. A dict overrides this per quantity. The choice therefore depends on a run's data: on all rows it is depth ratio (deflection) and depth ratio + rib slenderness (stress); on reference rows only it is depth ratio + rib-to-plate (deflection) and a constant (stress).
- **Per-run isolation:** `main.py` calls `structural_evaluator.use_run_calibration(run_id, sources)`, which copies the shared file to `runs/<run>/calibration.json`. From then on, all calibration reads and `record_fea` writes in that process use the copy.
  - `--calibration-start reference` keeps only `reference` rows, hiding earlier runs' experiment rows (which agents could otherwise read). Use it for method comparisons.
  - `--promote-calibration` merges the run's new rows back into the shared file afterwards (`promote_run_calibration`).
  - Code run outside `main.py` (tests, scratch scripts) uses the shared file unless it calls `use_run_calibration`. Tests must call `use_shared_calibration()` afterwards.

Current fit (2026-10-31; 11 reference designs + 1 experiment, half model):
- **Deflection:** depth ratio only, scatter 4.4%, LOO 5.1%. Depth + rib-to-plate looked better (LOO 3.1%) until the first experiment row arrived.
- **That experiment row:** the selected 3.1/49.8/3.2 was 11% stiffer in L1 than FEA, and no feature explains it. The suspected missing physics is transverse "wing" bending of thin plates beside deep ribs. Adding that to `analytical.py` is the natural next L1 improvement.
- **Stress:** automatic selection on all rows gives depth ratio + rib slenderness (LOO 5.0%, 10 converged rows). Near the optimum, L1 stress is within −3% to +4% of FEA.
- **Best verified design so far:** 3.1/49.8/3.2 at 21.87 g (FEA deflection 0.362 mm and stress 22.1 MPa against limits of 0.5 mm and 25 MPa; all print checks pass). It is in `runs/m6-loop/`.
- **Excluded rows:** 3 reference designs have unconverged stress and 1 has unconverged deflection; each is left out of that quantity's fit.
- **Screening split** (measured on the older depth-only fit): over 2000 random valid designs, L1 found ~63% infeasible, ~33% feasible and ~4% ambiguous. Only the ambiguous ones (plus shortlisted candidates) escalate to FEA.

Experiments and selection:
- **`coordination.run_experiment(params, run_id, budget, accuracy)`** is the one pipeline for evaluating a candidate, used by agents and baselines alike. It builds geometry (CAD volume → `record.mass`), runs `structural_evaluator.evaluate`, and saves the `CandidateRecord` with its decisions. It skips re-evaluation if selection-grade results already exist.
- **`run_print_prep(record, run_id, budget, preform)`** adds a PreForm preparation and saves `runs/<run>/<cid>/print<n>.form`.
- **`selection.select(records)`** uses only selection-grade evidence, preferring measured, then calibrated, then FEA. It requires P(all limits met) ≥ `SELECTION_MIN_PROBABILITY` (0.95; per-limit normal probabilities multiplied) and at least one preparation whose checks all passed. It picks the lowest mass. Every other candidate gets a status (`unverified`, `uncertain`, `infeasible`, `not_printable`, `print_not_evaluated`) with reasons, and "no feasible design" is reported explicitly.

`optimization.py` holds the Fabrication agent's tools. They only propose designs; nothing they find is selectable without verification.
- **`Model`:** calibrated L1, cached and charged to the `l1` budget. "Feasible" here means value + `OPT_CONSTRAINT_SIGMAS`·σ is within every limit.
- **`constrained_optimum`:** scipy differential evolution with a nonlinear constraint. Parameters are snapped to `PARAMETER_RESOLUTION_MM`, and it stops cleanly on `BudgetExceeded`.
- **Analysis helpers:** `pareto_front` / `pareto_table` (all objectives minimized), `local_sensitivity` (log elasticities) and `morris_screening`.
- **`bayes_opt(evaluate)`:** scikit-learn GP models of ln(deflection) and ln(stress) for an expensive evaluator. Mass is computed exactly, so the acquisition is (best feasible mass − mass) × P(feasible). Surrogate outputs are `source="surrogate"`.

`baselines.py` provides `random_search` and `optimizer_baseline`. Both screen with L1, then `finalize()` verifies the lightest L1-feasible designs through `run_experiment(accuracy="selection")` plus PreForm until one is selected or the budget runs out. On calibrated L1, the mass optimum is about 3.4/50/3.5 (24 g). It is stress-limited, with rib height at its upper bound.

Geometry split: `bracket.build_bracket(...)` runs in the project `.venv`, validates parameters, and launches `freecad_bracket.py` as a subprocess under FreeCAD's own Python (JSON request on stdin, JSON result as the last stdout line). FreeCAD's compiled modules are tied to its bundled Python version, so never `import FreeCAD` from `.venv` code, and keep `freecad_bracket.py` free of project imports. Outputs are written as `*.partial.<ext>` then renamed, so watchers never see half-written files.

Visualization: `fusion_addin/BracketViewer/` is an Autodesk Fusion add-in (third interpreter: Fusion's Python, `adsk` API only exists there; no project imports). Install via Fusion → Utilities → Add-Ins → Scripts and Add-Ins → **+** → select that folder, then Run. It polls `runs/*/*/*.step` (override with `BRACKET_RUNS_DIR`), ignores files that existed at startup, and imports each new candidate as a component in one direct-modeling document per run, hiding earlier candidates. Its background thread hands paths to Fusion's main thread via a custom event, since the Fusion API is not thread-safe. Errors go to Fusion's Text Commands log. The `runs/<run_id>/<candidate_id>/` layout from `store.geometry_paths` is its contract with the pipeline.

Key design rules from the spec:
- **Geometry and print settings are separate.** A geometry change requires a new solid; an orientation/support change reuses the solid with new preparation.
- **Every analysis has a status** (completed / failed / not evaluated). A failed analysis never counts as a pass — notably a failed PreForm thin-wall analysis can return `has_thin_walls: false`. Missing structural analysis means `not evaluated`, never "feasible".
- **PreForm printability ≠ structural validation.** Mock structural results must be labeled synthetic. Keep predicted, software-checked, and physically measured values distinguishable.
- Units are millimeters; record units for every mass/force/stress/deflection value and preserve scale on STL export/import.
- Cache keys must include geometry, load/material settings, tool versions, and print settings. A changed load is a new experiment.
- Save prepared PreForm scenes as `.form` files (scene IDs are tied to the live server session). Job submission is a separate, explicit action — never submit a print as part of evaluation.
- Pick printer/material IDs from the running PreForm installation, not example values.

First integration milestone: generate a bracket → export STL → prepare in PreForm → save `.form`.

## Naming drift (README vs. files)

The README still describes CadQuery as the generator, the old structural/weight/manufacturing agent roles, and no Fusion add-in. Treat this file and the plan as current.

## Agents (M8)

The LLM layer:
- **`llm_client.run_turn(system, prompt, tools, budget)`** is a manual tool-use loop against `client.beta.messages.create`, using `config.CLAUDE_MODEL` (default `claude-opus-5-5`) and `CLAUDE_EFFORT`. It sends `fallbacks="default"` with beta `server-side-fallback-2026-07-01`.
- **Budget:** every API request costs one `llm_calls` unit.
- **Tool calls:** each tool input is validated by its Pydantic `args` model before the function runs. Failures go back as `is_error` tool results, all results are sent in one user message, and a turn is capped at `AGENT_MAX_API_CALLS_PER_TURN`.
- **Message history:** the conversation is append-only (the full `response.content`), as Opus 5.5's thinking rules require. A refusal (`stop_reason == "refusal"`) ends the turn.
- **Forced tool choice:** Opus 5.5 rejects forced `tool_choice`, so don't add it.

`agents.py` defines the three roles plus a `single` baseline:
- **Fabrication** (optimize, propose, Pareto, sensitivity, PreForm) → **Mechanic** (evaluate designs at a chosen level, quick L1 predictions, calibration report) → **Measurement** (calibration residuals and z-scores, compare correction models, measurement status, specimen requests, diagnostic proposals).
- **Tools** wrap the existing pipeline (`run_experiment`, `run_print_prep`, `optimization`, `structural_evaluator`), so agents and baselines share the same experiments and budget.
- **Digest:** each turn starts from `agents.digest()`: budget, selection status for every candidate, pending proposals and recent messages.
- **Board:** the agent's final text is posted to the board, with candidate ids (pattern `g` + 10 hex chars) extracted as cites.

`coordination.run_agents` runs rounds and stops on the round limit, an exhausted `llm_calls` budget, or a round with no successful tool call. The Measurement agent has no real data yet; its prompt forbids fabricating measurements.

`tests/test_agents.py` drives everything with a scripted fake client (`FakeClient`), so tests make no API calls.

First agent results:
- **`agents-smoke1`** (1 round, $0.32): the round ran end to end, but the budget ran out before PreForm.
- **`collective-r2`** (2 rounds, reference-only start, 21 API calls, 3 FEA, 1 PreForm, about $0.55): selected **3.3/49.9/2.4 at 21.79 g**.
  - The agents disagreed and changed positions on evidence. For example, the Mechanic refused to re-run a thin-plate FEA, and Measurement switched to proposing a physical test instead.
  - They exposed the over-strict FEA convergence rule that was blocking the ~20.7 g designs. That rule has since been fixed. The run's records still carry their original scoring.

## Method comparison (M9)

`compare.py` runs `collective`, `single`, `optimizer` and `random` through `main.py`, one subprocess per run.
- **Run ids:** `<prefix>-<method>-s<seed>`, ordered by seed.
- **Identical settings:** every run uses the same `compare.BUDGET` (l1 3000, l2_fea 4, preform_prep 2, llm_calls 24), agent rounds capped at 3, and `--calibration-start reference`.
- **Resuming:** runs that finished (they have `run.json`) are skipped, and a partially written run is moved aside as `-incompleteN`.
- **What `main.py` records:** `run.json` holds the seed, rounds, calibration start, budget used and `elapsed_s`.

`compare.report` reads each run's records, selection, turns and messages, and writes `runs/<prefix>-report.{md,json}`:
- **Per method:** how many runs selected a design, the best and mean selected mass, mean FEA used, median time (robust to machine stalls) and estimated cost.
- **Per run:** the selected design and the lightest FEA-qualified design.
- **Position changes:** keyword-matched excerpts where an agent challenged or changed a position. Read them in context before drawing conclusions.

LLM runs are not reproducible: temperature cannot be set on current models, and "seed" only labels the run.

**Batch `m9` (6 seeds, 2026-11-01).** The interpretation is in `runs/m9-report.md`.

| Method | Selected | Mean mass | Range | Mean cost |
|---|---|---|---|---|
| collective | 6/6 | 21.24 g | 20.88–21.42 g | ~$0.60 |
| single | 6/6 | 21.48 g | 20.88–22.34 g | ~$0.43 |
| optimizer | 6/6 | 21.66 g | 21.66–21.68 g | - |
| random | 5/6 | 23.71 g | - | - |

- **Collective vs optimizer:** the collective was lighter in all 6 seeds (sign test p ≈ 0.03), but mostly by only 0.24 g.
- **Collective vs single:** 2 wins, 4 ties and no losses, so they are not separable.
- **Where the gain comes from:** spending FEA on designs that L1 rejects at 2σ. The optimizer leaves FEA budget unused.
- **random-s4:** selected nothing. Every design's stress failed the convergence rule.

**Unattended runs on Windows.** `fea._run` exempts gmsh/ccx (and the calling process) from Windows power throttling (EcoQoS) and holds `ES_SYSTEM_REQUIRED` while solving. This was added after overnight batches showed ccx alive but nearly idle for 30–80 min on the Balanced plan. It affects timing only. In the `m9` batch it applies from `m9-optimizer-s5` onward.
