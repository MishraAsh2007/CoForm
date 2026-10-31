# Agents that design and physically validate small SLA assemblies

First test case: a two-piece Raspberry Pi 5 + Active Cooler enclosure (base + lid, M2.5 lid screws,
robot mounting tabs, port cutouts, cable access), printed on a **Formlabs Form Studio 1 in Alpha Fast resin**.
Printer, material and layer thickness are ONE setting (`print:` in `config.yaml`) used for every print and
recorded in every output (`PRINT_SETTINGS.json`) and run log. The fit-test results only apply to that combination.

## Setup (Windows PowerShell)

    py -3.13 -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    copy .env.example .env      # put ANTHROPIC_API_KEY in .env (never committed)

PreFormServer (3.64.2) on `http://127.0.0.1:44388` is used for real print checks. If it is down, checks fall
back to a MOCK that is labelled everywhere.

## What is where

| Folder | What |
|---|---|
| `components/` | `rpi5.yaml`, `active_cooler.yaml`, `arduino_uno.yaml`, `rpi_pico.yaml`, `sources.md` (a source for every number; UNVERIFIED where marked) |
| `data/fit_test_results.yaml` | hand-filled tolerance test results (template) |
| `cad/` | `test_print.py` (Phase 1), `fit_check.py` (Phase 2 frame), `spec.py` (JSON feature spec + validator), `enclosure.py` (generator), `render.py`, `prepare_forms.py` (.form files) |
| `checks/` | `assembly.py`, `printability.py`, `preform_check.py` (+ teammate's `preform_client.py`), `fit_rules.py`; tests in `checks/tests` |
| `core/` | `board.py` (SQLite shared board), `protocol.py` (ALL coordination rules), `llm.py`, `evaluate.py`, `report.py` |
| `agents/` | `design`, `assembly`, `manufacturing`, `fit`, `generalist`, plus `mock_policy.py` (rule-based fake model) |
| `benchmark/` | `briefs/` (a-d), `run_benchmark.py`, `metrics.py` |
| `run.py`, `app.py` | one design run; web UI |

## Commands

    .venv\Scripts\python.exe -m cad.test_print                 # Phase 1 plate + key
    .venv\Scripts\python.exe -m cad.fit_check                  # Phase 2 fit-check frame
    .venv\Scripts\python.exe -m cad.enclosure generic          # Phase 3 v0 enclosure (or: measured)
    .venv\Scripts\python.exe -m cad.prepare_forms results\test_print   # real PreForm .form files
    .venv\Scripts\python.exe run.py --mode collective --brief benchmark\briefs\a_pi5_standard.yaml --budget 12 --seed 0 [--mock] [--clearances measured] [--real-preform]
    .venv\Scripts\python.exe -m benchmark.run_benchmark [--mock]
    .venv\Scripts\python.exe -m streamlit run app.py
    .venv\Scripts\python.exe -m pytest checks core

## How the agents coordinate (core/protocol.py)

No boss. Agents post on a shared SQLite board. Each owns checks and data and may only patch its own top-level
spec keys (enforced in code); otherwise it posts a request (`note` with `suggest_patch`) for the owner.
An edit is accepted if no check gets worse. Otherwise owners of the worsened checks object with numbers, the
others vote, and the edit passes only if total failures go down and supporters outnumber objectors.
A budget of full evaluations ends each run. Agent runs start from a deliberately plain "naive" v0 spec.
