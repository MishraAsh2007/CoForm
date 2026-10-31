# Agents that design and physically validate small SLA assemblies

First test case: a two-piece Raspberry Pi 5 + Active Cooler enclosure (base + lid, M2.5 lid screws,
robot mounting tabs, port cutouts, cable access), printed on a Formlabs Form Studio 1 in Rigid 10K.

Setup (Windows PowerShell):

    py -3.13 -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    copy .env.example .env   # then put your ANTHROPIC_API_KEY in .env

## Phase 1: tolerance test print

    .venv\Scripts\python.exe -m cad.test_print

Writes `results/test_print/plate.stl` and `key.stl`. Test them by hand with
`results/test_print/HOW_TO_TEST.md` and fill in `data/fit_test_results.yaml`.
