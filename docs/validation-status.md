# Validation status

## Software and synthetic evidence

The offline robustness matrix completed **72 studies**: three section families
(U-channel, solid bar, rectangular tube), six synthetic conditions, two noise seeds,
and two policies (adaptive numerical and rule-based collective). It used **zero model
API calls**. All recorded protocol-budget and provenance invariants passed.

Acceptance counts: 24 simulated pass, 30 fail, 12 invalid, 6 uncertain.
Fit counts: 20 identifiable, 16 ill-conditioned, 24 insufficient-data, 12 inadequate.
Data and per-study outcomes: `data/robustness-v2/report.json` and `report.md`.

These stress tests cover material/mount changes, fixture motion, permanent set and
nonlinear response. They do not establish that printed prototypes work, that arbitrary
shapes are supported, or that LLM collectives outperform a single model.

Automated tests cover heterogeneous-provider routing with test doubles, revision and
disagreement across rounds, malformed votes, timeouts, shared evidence, geometry,
identifiability, measurement guard bands, persisted spending limits, and manufacturing
submission failures. Mock coordination is not real multi-model coordination evidence.

## Low-cost live model check

Read-only model metadata checks returned HTTP 200 for all three configured providers.
The first generation request (OpenAI) returned HTTP 400. Execution stopped; no Claude
or Gemini generation call followed. No successful three-provider round has been observed.
The provider error body was not retained, so the specific HTTP 400 cause is unknown.

The persistent ledger records one failed generation attempt with usage unknown and a
conservative reservation of approximately **$0.0007521**, not an invoice-confirmed cost.
The local guard caps the pilot at $0.05 estimated/reserved spend, six attempts total,
and two per provider. It is not a provider-side account spending cap. No new paid calls
were made for the Formlabs integration. The failed smoke report was recovered from
saved evidence, without repeating a request: `data/live-smoke-v2/report.json`.

## Manufacturing and physical evidence

See [Formlabs setup and live preparation](formlabs.md). A real PreForm preparation
completed and passed the requested checks. Queue submission is tested with an SDK
double only; no actual printer was discovered, queued or started. No physical
observations, completed prints, or validated material properties have been collected.

## Onshape → PreForm → specialist review pipeline (2026-10-31)

Implemented in `coform/cad.py`; see [onshape.md](onshape.md). The Onshape link
(variable writes, versioning, 307 export redirect, mass-properties cross-check, foreign
redirect refusal, missing-variable bootstrap) is tested against a **fake Onshape server**
only. No Onshape API keys are configured yet, and the FeatureScript support-arm feature
has not been run inside Onshape.

One real local pipeline run used the `local_fixture` CAD source with the reference
U-channel and **Form Studio 1 / Alpha Black V1 / 0.100 mm** (a software test profile,
not a Rigid 10K recommendation). Mesh checks passed (8,640 mm³, 160 × 25 × 11.2 mm,
closed). PreForm 3.65.0.1096 returned PASS on all seven checks, estimated 8,204 s and
24.93 mL including supports. The rule-based specialists approved it (3/3). Nothing was
queued or printed: PreForm still reports no connected printer. Evidence:
`data/pipeline-smoke/pipeline-result.json` and `data/pipeline-smoke/run/`.
Live-provider print reviews are tested with provider doubles only.

## Diagnosis and agent repair (2026-10-31)

Tested on simulated studies and on hand-entered physical records (not real measurements).
With only the shared pilot station, the simulated discrepancy (+79 sigma) is reported as
unresolved, and the agents decline to redesign (`diagnose_first`). After the diagnostic
station and refit, the reference is predicted at 106% of a 1.5 mm limit, 71% of it from
mount rotation. The rule-based specialists agreed (2 of 3, measurement objecting to the
97% margin) on 15 x 13 mm, which is predicted to pass with 9% less material. Those are
predictions from a fitted simulator model, not physical results. The station-pattern
explanation is unit-tested for a soft-material truth and a compliant-clamp truth.
Live-model repair uses the same metered provider path and is tested with doubles only.

## FreeCAD backend and design requirements (2026-10-31)

FreeCAD 1.1.4 was installed per-user with winget. Real headless builds of all three
section families passed every check. The kernel volume and second moment of area
matched the beam formulas to every printed digit (U-channel 621.733 mm⁴, tube
1,643.494 mm⁴, bar 496.517 mm⁴), at about 0.5 s per candidate. A reopened master
model rebuilt a second candidate with new dimensions, so the spreadsheet drives the
geometry. "Open in FreeCAD" was not exercised, to avoid opening windows unattended.
Requirements (material budget, allowable stress, ranges) are unit-tested through the
optimizer, diagnosis, repair and proposal validation. They are checked against model
predictions, not measurements; the allowable stress is the user's value, not a
qualified resin strength.
