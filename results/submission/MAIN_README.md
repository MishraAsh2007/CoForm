# ScienceClaw: agents that design print-ready parts

**MIT ScienceClaw hackathon 2026 · Main track.** Team: Mohamed Essadak Rakah (Launch Lead), Ashutosh Mishra,
Quang Tuong Van Nguyen, Jash Kotadiya.

A part that looks right in CAD often fails on the printer or in assembly. ScienceClaw gives each kind of expertise
to a specialist AI agent that owns one real tool and its own part of the design, with no boss. Fixes outside an
agent's part become requests to the owner, and plain code decides which edits are kept. Every decision is logged.

![Raspberry Pi 5 case designed by the agents](https://raw.githubusercontent.com/MishraAsh2007/ScienceClaw_Project/Mo_ver/results/submission/images/thumbnail_1200x800.png)

## Two part families, two branches

| | **Enclosures** (branch [`Mo_ver`](https://github.com/MishraAsh2007/ScienceClaw_Project/tree/Mo_ver)) | **Brackets + simulation** (branch [`Jash_ver`](https://github.com/MishraAsh2007/ScienceClaw_Project/tree/Jash_ver)) |
|---|---|---|
| What | 4 agents (Design, Assembly, Manufacturing, Fit) design a Raspberry Pi 5 + Active Cooler case | Mechanics, Fabrication and Measurement specialists design load-bearing brackets |
| Tools | CadQuery, assembly check, Formlabs PreForm (Local API), orientation tool, fit rules | FreeCAD / Fusion / Onshape, Gmsh + CalculiX finite elements, PreForm |
| Result | 20 → 0 local check failures; Formlabs .form with 0 PreForm warnings, 23.0 min, 61.9 ml | [Teammates: one line with your logged result] |
| Run | see below | see below |

### Run the enclosure agents (no API key needed)

```powershell
git clone -b Mo_ver https://github.com/MishraAsh2007/ScienceClaw_Project.git
cd ScienceClaw_Project
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run app.py
```
Mock mode (a rule-based stand-in model, labelled MOCK) is on by default; the best real run is preloaded. Real
PreForm checks need PreFormServer on port 44388 (see that branch's README).

### Run the bracket Design Lab

```powershell
git clone -b Jash_ver https://github.com/MishraAsh2007/ScienceClaw_Project.git
cd ScienceClaw_Project
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,formlabs]"
.\.venv\Scripts\python.exe -m scienceclaw.cli serve
```
Open http://127.0.0.1:8000 and use the offline demo (no keys needed). [Teammates: check these commands.]

## Submission material

- Slides: [slides.pdf](https://github.com/MishraAsh2007/ScienceClaw_Project/blob/Mo_ver/results/submission/slides.pdf)
- Demo video: [demo.mp4](https://github.com/MishraAsh2007/ScienceClaw_Project/blob/Mo_ver/results/submission/demo.mp4) (enclosures, 2:50)
- Print files: [`results/print/`](https://github.com/MishraAsh2007/ScienceClaw_Project/tree/Mo_ver/results/print) on `Mo_ver`

## Honest status

Validated: enclosure local checks pass; the PreForm print job has 0 warnings. Not validated: no benchmark run passed
every check; several part sizes are UNVERIFIED typical values; the tolerance plate is not printed yet.
[Teammates: add the bracket status, e.g. simulated vs physical pass.] [IF PRINTED / IF NOT PRINTED: one line.]

## Licence

MIT (see `LICENSE`). Third-party models keep their own licences.
