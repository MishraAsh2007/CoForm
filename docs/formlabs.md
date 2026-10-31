# Onshape and Formlabs workflow

ScienceClaw coordinates experiments and agent discussion. Onshape owns production CAD;
PreForm owns print preparation. ScienceClaw is not a replacement CAD editor or slicer.

## What is implemented

- Import an Onshape-exported ASCII or binary STL in millimeters, up to 25 MiB.
- Connect to an already running local PreForm Insider/Server, preserving its login.
- Read the real print-setting catalog and actual printable devices. Generic printer
  templates are not presented as connected hardware.
- Create a scene, import without silently repairing geometry, orient, support, layout,
  run seven printability checks, calculate print time, and save a `.form` project.
- Record the source hash, candidate link if applicable, settings, PreForm version,
  checks, resin usage, estimated time, and saved project hash in immutable records.
- Upload a reviewed saved project to a selected printer queue with `print_now=False`.
  Reopen the hashed file and rerun checks before uploading. Failed/incomplete checks
  block uploading; warnings remain visible and require review notes.
- Persist upload intent before sending. A failure or ambiguous timeout never triggers
  an automatic repeat. Inspect the printer queue before preparing another submission.
- Include candidate-linked PreForm reports in agent evidence and study exports.

Production Onshape creation/editing and automatic CAD export are **not connected yet**.
The current CAD handoff is manual STL export/import. The three local parametric section
families are simulation fixtures; they are not a new production modeling application.
An arbitrary imported STL does not acquire beam predictions or physical validation.

## Your account and hardware setup

1. Sign in to [Onshape](https://cad.onshape.com/signin) yourself. Create a document named
   **ScienceClaw - Physical Design** and send its document URL. Keep passwords out of chat.
   Do not create new API credentials yet: the exact automation access will be scoped
   after the document exists. Manual export works without API credentials.
2. Create/open the part in Onshape. Export the part as **STL**, units **millimeter**.
3. Open **PreForm Insider**. Choose **File > Start PreForm Server**, port **44388**, then
   **Start**. This was enabled in the current session; after restarting PreForm it may
   need starting again. Auto-start was not enabled.
4. If remote printers are needed, sign in to Formlabs yourself inside PreForm. Add/select
   the actual on-site printer using the team's normal network or Dashboard setup.
5. Open [ScienceClaw locally](http://127.0.0.1:8000), click **Connect PreForm**, and import
   the STL. Select a catalog preset matching the actual printer and exact resin product.
   Enter the minimum-wall threshold from the material/process guidance. No threshold
   is automatically qualified by ScienceClaw.
6. Click **Prepare in PreForm**. Inspect the result and saved `.form` file. Record the
   orientation, supports, settings and wash/cure plan in the review notes.
7. Select the actual printer and upload the reviewed project to its queue. Start the job
   at the printer once the operator has checked the hardware. ScienceClaw never sends
   `print_now=True`. Printability PASS and queue receipts are not physical test results.

## On-site hardware and live check, 2026-10-31

User reports **Form Studio 1**, **Rigid 10K Resin**, and another bottle described as
"photopolymer resin" (exact product/variant not yet identified).

The [Rigid 10K product page](https://formlabs.com/products/rigid-10k-resin/) lists Form
Studio 1 compatibility and the **Form Studio Resin Tank Pro**. However, the actually
installed **PreForm Insider 3.65.0.1096** catalog returned no Rigid 10K preset for
Form Studio 1. Do not substitute a Form 4 preset or assume Open Material Mode solves
this. Confirm the supported settings/resin variant with the on-site Formlabs team.

The real catalog identifies Form Studio 1 as `CLRK-1-2`. It currently lists Alpha
Black/Clear/Fast/Grey/Model/White, Flexible 80A V2, Open Material V1, Tough 1500 V2,
and True Cast V1. A read-only check returned **zero actual printable devices**.

One real local preparation was completed using an existing U-channel simulation fixture
and **Alpha Black V1 / 0.100 mm**, solely to test the software connection. Its 0.5 mm
wall-check threshold is a test parameter, not a Rigid 10K process recommendation.
All seven checks returned PASS. PreForm estimated **8,203 seconds** and **24.92 mL**
including supports. No job was queued or printed.

- Project: `data/formlabs/build_0a52d0a8252643ab.form`
- Preparation evidence: `data/formlabs-live-preparation.json`
- Catalog snapshot: `data/formlabs-catalog.json`
- Hardware notes: `data/site-hardware.json`

## Installation and API

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,formlabs]"
.\.venv\Scripts\python.exe -m scienceclaw.cli serve
```

The optional Formlabs dependency is pinned to the official SDK's
`v1.0.0-alpha.post2` commit. This alpha API may change; the integration was tested
against the installed PreForm version above. `FORMLABS_PREFORM_PORT` changes the local
port. Remote PreForm file mappings and Dashboard developer-token management are not
implemented. Local preparation does not require a new Formlabs developer secret.

API routes under `/api/formlabs`: `status`, `catalog`, `projects`, `assets`, `prepare`,
`projects/{id}/file`, `projects/{id}/queue`. Candidate fixtures can be selected through
`/api/runs/{run_id}/formlabs/assets`. API requests never invoke an LLM for preparation.

Sources: [setup](https://developer.formlabs.com/getting-started/install/),
[workflow](https://developer.formlabs.com/getting-started/quickstart/),
[checks](https://developer.formlabs.com/core-concepts/validate/),
[API specification](https://developer.formlabs.com/reference/openapi-v1.yml),
[SDK](https://github.com/Formlabs/formlabs-api-python).
Markdown docs keep the `.com` domain and replace a page's trailing `/` with `.md`.
