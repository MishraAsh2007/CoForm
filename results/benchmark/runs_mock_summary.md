# Benchmark summary (runs_mock)

- model: `claude-haiku-5-5` **MOCK (rule-based fake model, no API calls)**
- print: Alpha Fast V1, 0.15 mm layers; clearances generic
- PreForm inside the loop: **MOCK** (time budget). PreForm warnings column: final design, **MOCK or partly MOCK**
- 35 runs. Means over seeds; failures are counted by our own checks (claims are limited to them).

| brief | mode | passed | req. failures | all failures (min-max) | edits prop./acc. | evals used | evals to pass | PreForm warn | time s | cost $ |
|---|---|---|---|---|---|---|---|---|---|---|
| a_pi5_standard | one_shot | 0/3 | 18 | 18 (18-18) | 1/1 | 0 | never | None | 15.2 | 0.0 |
| a_pi5_standard | generalist | 0/3 | 13 | 13 (13-13) | 11/10 | 12 | never | None | 157.9 | 0.0 |
| a_pi5_standard | collective | 0/3 | 10 | 10 (10-10) | 11/11 | 12 | never | None | 15.57 | 0.0 |
| b_pi5_tight_robot | one_shot | 0/3 | 21 | 21 (21-21) | 1/1 | 0 | never | None | 6.3 | 0.0 |
| b_pi5_tight_robot | generalist | 0/3 | 20 | 20 (20-20) | 11/9 | 12 | never | None | 157.2 | 0.0 |
| b_pi5_tight_robot | collective | 0/3 | 20 | 20 (20-20) | 11/10 | 12 | never | None | 111.0 | 0.0 |
| c_arduino_uno | one_shot | 0/3 | 20 | 20 (20-20) | 1/1 | 0 | never | None | 8.57 | 0.0 |
| c_arduino_uno | generalist | 0/2 | 16 | 16 (16-16) | 11/10 | 12 | never | None | 126.8 | 0.0 |
| c_arduino_uno | collective | 0/3 | 16 | 16 (16-16) | 11/10 | 12 | never | None | 31.37 | 0.0 |
| d_rpi_pico | one_shot | 0/3 | 19 | 19 (19-19) | 1/1 | 0 | never | None | 0.4 | 0.0 |
| d_rpi_pico | generalist | 0/3 | 14 | 14 (14-14) | 11/9 | 12 | never | None | 101.8 | 0.0 |
| d_rpi_pico | collective | 0/3 | 14 | 14 (14-14) | 11/10 | 12 | never | None | 10.8 | 0.0 |
