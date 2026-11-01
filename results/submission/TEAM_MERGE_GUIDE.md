# Team merge guide (everything except the app code)

For Ashutosh, Quang and Jash, from Mohamed's branch `Mo_ver`. The two apps stay on their own branches
(`Mo_ver` = enclosure agents, `Jash_ver` = brackets + CalculiX). We only merge the **submission material**:
slides, form, video, and a landing page on `main`. Nobody needs to merge code.

All my files are in `results/submission/` on `Mo_ver`:
`slides.pptx`, `slides.pdf`, `demo.mp4`, `demo.gif`, `SUBMISSION.md`, `MAIN_README.md`, `images/`.

## Who does what

| who | task | done by |
|---|---|---|
| Mohamed | Pick IF PRINTED / IF NOT PRINTED (slide 6 or 7, form "Additional info", demo script) | 10:00 |
| Mohamed | Send this guide to the team | 9:30 |
| Jash | Fill slide 11 (brackets + CalculiX) and slide 12 (multi-provider agents) with logged numbers | 11:30 |
| Jash | Confirm or deny: did the live Part Designer test use Nemotron? (decides the NVIDIA answer) | 11:00 |
| Ashutosh | Owner of `main`: paste `MAIN_README.md` as `README.md`, add `LICENSE`, add the final links | 12:00 |
| Quang | Join the videos (or decide who presents what) and upload | 12:00 |
| Mohamed (Launch Lead) | Final read of the form, submit | 12:45 |

Slides are due at **1 PM**.

## 1. Combine the slide decks

Start from `results/submission/slides.pptx` (editable, speaker notes on every slide) or copy its slides into your
deck. Slide map:

| # | slide | action |
|---|---|---|
| 1 | Title + team | keep; check your name and role |
| 2 | Problem | keep (enclosure example; add one bracket line if you want) |
| 3 | Idea: specialist agents, no boss | keep (it describes both families) |
| 4 | System diagram (enclosure agents) | keep; optionally add your Design Lab diagram next to it |
| 5 | Real exchange from the log | keep |
| 6 | Result, **IF PRINTED** | keep **one** of 6 / 7, delete the other |
| 7 | Result, **IF NOT PRINTED** | (see above) |
| 8 | Benchmark (Haiku) | keep |
| 9 | Trust: Why this design, charts from logs | keep |
| 10 | Formlabs relevance | keep; add one bracket + PreForm line if true |
| 11 | **TEAMMATES: replace this**: brackets + CalculiX | replace with your slide(s) |
| 12 | **TEAMMATES: replace this**: multi-provider agents | replace, or delete if there is no logged live round |
| 13 | Limitations and next steps | keep; add your own limitations |
| 14 | Links | add the `Jash_ver` and `main` links |

After editing, export the PDF from PowerPoint (File → Export → PDF) and put it at
`results/submission/slides.pdf` (or on `main`), then update the slide link in the form.
Rule for all slides: every number must come from a log or result file; keep the MOCK / UNVERIFIED / simulated labels.

## 2. The form (`SUBMISSION.md`)

Paste field by field. Fields **you** must check or fill:

| field | what to do |
|---|---|
| Title | pick one of the two (both ≤ 32 characters) |
| Description (≤ 150 words) | check the bracket sentence: "specialists design load-bearing supports and test them with CalculiX finite-element simulation before printing". Change it if that is not accurate |
| Project URLs | add `Jash_ver` and `main` links |
| NVIDIA Platform General | **only if** the live Part Designer test used Nemotron: fill the bracketed part with the role, model, rounds and logged result. Otherwise leave the field blank |
| Formlabs prize | add one bracket sentence if a bracket went through PreForm (word limit 250; currently about 230) |
| Additional info | keep one of IF PRINTED / IF NOT PRINTED; add the bracket status (simulated_pass vs physical_pass) |
| E14 | check eligibility with the organizers before ticking |

Re-run `python scripts/make_submission.py` after editing the text inside it to re-check the limits, or count by
hand.

## 3. The demo

Option A (simplest): one combined video. Mohamed's part is `results/submission/demo.mp4` (2:50, captions, no
audio). Join it with yours:

```
ffmpeg -i mo.mp4 -i jash.mp4 -filter_complex "[0:v]scale=1440:900,setsar=1[a];[1:v]scale=1440:900,setsar=1[b];[a][b]concat=n=2:v=1[v]" -map "[v]" -an demo_team.mp4
```
(`imageio-ffmpeg` ships an ffmpeg binary: `python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"`.)
If the total must stay under 3 minutes, trim with `-t 90` on each input.

Option B (live): 3 minutes split: 0:00-0:20 problem (anyone), 0:20-2:00 enclosures (Mohamed, script in `DEMO.md`),
2:00-2:50 brackets + simulation (Jash), 2:50-3:00 close.

## 4. GitHub `main` branch

`main` currently has only a placeholder `README.md`. Ashutosh (or whoever owns `main`):

1. `git checkout main && git pull`
2. Copy `results/submission/MAIN_README.md` from `Mo_ver` to `README.md`:
   `git show origin/Mo_ver:results/submission/MAIN_README.md > README.md`
3. Copy the licence: `git show origin/Mo_ver:LICENSE > LICENSE`
4. Fill the bracketed parts (your result line, run commands, final deck link, IF PRINTED / IF NOT PRINTED).
5. `git add README.md LICENSE && git commit -m "Landing page" && git push` (no force pushes; don't merge the code
   branches into `main`).

## Final submission checklist (Sunday Nov 1)

| time | item | who | ☐ |
|---|---|---|---|
| 9:30 | This guide sent to the team | Mohamed | ☐ |
| 10:00 | IF PRINTED / IF NOT PRINTED decided (slides, form, demo) | Mohamed | ☐ |
| 11:00 | Nemotron question answered (NVIDIA field: fill or blank) | Jash | ☐ |
| 11:30 | Slides 11-12 replaced; result slide 6 or 7 deleted | Jash, Mohamed | ☐ |
| 12:00 | `main` README + LICENSE pushed | Ashutosh | ☐ |
| 12:00 | Video joined / presenters decided; uploaded (YouTube unlisted optional) | Quang | ☐ |
| 12:30 | PDF exported, links checked in a private browser window | Mohamed | ☐ |
| 12:45 | Form submitted (paste from `SUBMISSION.md`) | Mohamed | ☐ |
| **13:00** | **Slides due** | | |
