# Main physical experiment: generic vs measured clearances

Question: does the collective's enclosure assemble better when clearances come from the measured fit test
than from generic Formlabs rules? Same brief, model, budget and seed; only `--clearances` changes.
All prints: Form Studio 1, Alpha Fast V1, the one layer thickness in `config.yaml` (`print:`).

1. Print `results/test_print/plate.form` + `key.form`. Post-cure the way you will post-cure the enclosure.
2. Test by hand (`results/test_print/HOW_TO_TEST.md`) and enter results in the app (Fit test page) or in
   `data/fit_test_results.yaml`. Check that `meta.resin` / `meta.layer_thickness_mm` match `config.yaml`.
3. Optional: print `results/fit_check/fit_check_frame.form` and run `results/fit_check/HOW_TO_CHECK.md`.
   Correct any port/height that is off in `components/rpi5.yaml` (with a note in `components/sources.md`).
4. Run both designs (real model):

       .venv\Scripts\python.exe run.py --mode collective --brief benchmark\briefs\a_pi5_standard.yaml --budget 12 --seed 0 --clearances generic --real-preform
       .venv\Scripts\python.exe run.py --mode collective --brief benchmark\briefs\a_pi5_standard.yaml --budget 12 --seed 0 --clearances measured --real-preform

   Before step 2 is done, "measured" falls back to generic for every value and says so in the log
   (`MEASURED MODE FALLBACK`). The two designs are then the same, so don't print them yet.
5. Make `.form` files for both final designs:

       .venv\Scripts\python.exe -m cad.prepare_forms results\runs\a_pi5_standard__collective__generic__s0 results\runs\a_pi5_standard__collective__measured__s0

6. Print both, in one print job if they fit. Assemble each the same way and record for each:
   board drops in (y/n), all 4 board screws go in without cracking (y/n), lid closes flush (y/n, gap),
   4 lid screws (y/n), each cable plugs fully (list), fan spins with the lid on (y/n), tabs bolt to a plate (y/n).
7. Report only what the prints show (step 6), next to what the checks predicted (each run's `log.md`).
