# Sonnet vs Haiku: finished benchmark runs only

All numbers come from each run's `summary.json` / `board.sqlite` (regenerated with `benchmark.metrics`, 2026-11-01).
No new runs were made for this table. Same setup for both models: same tools, same naive starting spec,
budget 12, generic clearances, Alpha Fast V1 at 0.15 mm, and real PreForm on every final design. The in-loop PreForm
check is a local MOCK estimate.

Planned set per model: 2 briefs (a_pi5_standard, b_pi5_tight_robot) x 3 modes x 3 seeds = **18 runs**.
Finished: **Sonnet 4/18**, **Haiku 11/18**. Both sets are incomplete. With only 4 Sonnet runs, and no Sonnet
collective run at all, **no conclusion about modes or models can be drawn from Sonnet**.

## Brief a_pi5_standard (the only brief with runs from both models)

| mode | model | runs done | passed | failures left (mean) | quality (mean) | evals used (mean) | cost/run USD | print min | resin ml |
|---|---|---|---|---|---|---|---|---|---|
| one_shot   | Sonnet | 3/3 | 0/3 | 20 | 49.68 | 0  | 0.019 | 92.0 | 63.6 |
| one_shot   | Haiku  | 3/3 | 0/3 | 20 | 52.76 | 0  | 0.001 | 91.1 | 63.6 |
| generalist | Sonnet | **1/3** | 0/1 | **1** | 46.40 | 3  | 0.105 | 93.4 | 66.3 |
| generalist | Haiku  | 3/3 | 0/3 | 6  | 52.50 | 12 | 0.019 | 91.8 | 62.2 |
| collective | Sonnet | **0/3** | - | - | - | - | - | - | - |
| collective | Haiku  | 3/3 | 0/3 | 4  | 47.84 | 12 | 0.032 | 93.0 | 64.0 |

## Brief b_pi5_tight_robot

| mode | model | runs done | passed | failures left (mean) | quality (mean) | cost/run USD |
|---|---|---|---|---|---|---|
| one_shot   | Haiku  | 2/3 | 0/2 | 25 | 50.76 | 0.002 |
| all modes  | Sonnet | 0/9 | - | - | - | - |
| generalist, collective | Haiku | 0/6 | - | - | - | - |

How to read this:
- **Failures left** is the main result (lower is better). "Passed" means zero failures in assembly, printability,
  fit **and** the real final PreForm check. No run in either set passes. The runs that got closest (Sonnet generalist
  s0 and Haiku collective s0, with 1 failure left each) and all three Haiku collective runs carry a real PreForm
  "cup" warning (the base traps resin under PreForm's auto-orient). The print job fixes that separately by
  choosing the orientation (see `results/print/README.md`). The other runs fail on assembly checks.
- Sonnet generalist s0 shows evals_to_pass = 3 but passed = 0/1. That is because the in-loop PreForm check
  (MOCK) passed, but the real final PreForm found the cup.
- Quality only ranks designs with the same pass status and the same failures left, so it does not say which
  model is better here.

## Missing runs (named)

| model | run | state |
|---|---|---|
| Sonnet | a collective s0, s1 | started, stopped (low memory) before the first model call was logged ($0 logged) |
| Sonnet | a collective s2 | never started |
| Sonnet | a generalist s1, s2 | started, stopped part way (no summary.json; $0.081 and $0.033 logged) |
| Sonnet | b: all 9 (3 modes x 3 seeds) | never started |
| Haiku  | b collective s0, s1, s2 | never started |
| Haiku  | b generalist s0, s1 | started, stopped (no summary.json) |
| Haiku  | b generalist s2 | never started |
| Haiku  | b one_shot s2 | started, stopped (no summary.json) |

## Not in this table on purpose

The print candidate `results/runs/a_pi5_standard__collective__generic__s0__claude-sonnet-5-5` was a separate run
with **budget 16** (not 12). It used 11 evaluations, cost $0.825, and ended with assembly 0, printability 0, fit 0 and
1 real PreForm warning (the same cup), with quality 50.82. It is the only Sonnet collective run that finished. It is
not comparable to the budget-12 cells, so it is kept out of the table.

Charts (regenerated): `runs_sonnet_*.png`, `runs_haiku_v2_*.png`. Full tables are in `runs_sonnet_summary.md` and
`runs_haiku_v2_summary.md`.
