r"""Write results/submission/SUBMISSION.md (hackathon form answers) and check every length limit.
.venv\Scripts\python.exe scripts\make_submission.py      (prints the counts; exits 1 if a limit is broken)"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/MishraAsh2007/ScienceClaw_Project"
BR = f"{REPO}/tree/Mo_ver"
SLIDES = f"{REPO}/blob/Mo_ver/results/submission/slides.pdf"
VIDEO = f"{REPO}/blob/Mo_ver/results/submission/demo.mp4"

TITLE_1 = "ScienceClaw: Print-Ready Agents"
TITLE_2 = "Agent Team for Print-Ready Parts"
ONE_LINER = "Specialist AI agents with real CAD and PreForm tools design parts checked before they are printed."

DESCRIPTION = (
    "A part that looks right in CAD often fails on the printer or in assembly. One AI call can draw a box, but making "
    "it fit a real board, print cleanly and assemble needs several kinds of expertise. ScienceClaw gives each kind to "
    "a specialist agent that owns one real tool and its own part of the design, with no boss: fixes outside an "
    "agent's part become requests to the owner, and plain code keeps an edit only if no check gets worse. We built "
    "two part families. Enclosures: four agents (Design, Assembly, Manufacturing, Fit) took a naive Raspberry Pi 5 "
    "case from 20 local check failures to 0 and produced a Formlabs .form file with 0 PreForm warnings. Brackets: "
    "specialists design load-bearing supports and test them with CalculiX finite-element simulation before printing. "
    "Every decision is traceable in the log."
)

FORMLABS = (
    "We put Formlabs PreForm inside the design loop instead of at the end. Our Manufacturing agent calls the PreForm "
    "Local API (PreFormServer 3.64.2) and acts on real warnings. On our Raspberry Pi 5 case, PreForm's auto-orient "
    "found one resin-trapping cup in the base. The agent's orientation tool then compared six orientations in real "
    "PreForm (cups, supports, time) and chose flat for both parts: 0 warnings, base and lid on one Form Studio 1 "
    "platform, Alpha Fast V1 at 0.15 mm, 23.0 minutes and 61.9 ml of resin. The output is a .form file that prints "
    "as is, plus the same design as SLA STL, an FDM version and STEP for Onshape. "
    "The Fit agent is built for printer-specific tolerances: it reads clearances measured on a printed tolerance "
    "plate for one printer and resin, and labels every value that still comes from the design guide. Printer, "
    "material and layer height are one setting recorded in every output, so results never mix setups. "
    "What we have not shown yet: the tolerance plate is not printed, several part sizes are typical values marked "
    "UNVERIFIED, and the design run itself still ended with the cup warning because the orientation tool ran after "
    "the loop. Next we move it into the loop. Formlabs users get a design assistant that knows the printer before "
    "the first print, which saves failed prints, resin and time."
)

NVIDIA = (
    "ONLY IF teammates confirm the Part Designer live test used Nemotron. "
    "Our teammates' Design Lab runs specialist agents (mechanics, fabrication, measurement) on several model "
    "providers behind one small adapter layer, and NVIDIA-hosted Nemotron is one of the supported providers. In the "
    "Part Designer, the user describes a bracket in plain words; the agents draft parametric CAD, test it with 3D "
    "finite elements (Gmsh + CalculiX), adjust it until it meets the load and bend limits with the least resin, and "
    "send it through PreForm checks. [Teammates: state exactly which agent role ran on which Nemotron model, how many "
    "rounds, and what the logged result was. Do not submit this answer unless that live run happened and is logged.] "
    "Every model response is validated against a bounded JSON contract, cannot invent measurements or change frozen "
    "limits, and is recorded with its token usage."
)

ADDITIONAL = """Honest status (pick ONE line before submitting):
- IF PRINTED: The enclosure was printed on a Form Studio 1 in Alpha Fast V1: [fill in from the ASSEMBLY.md checklist: board drops in / screws grip / lid closes / ports fit / fan clears].
- IF NOT PRINTED: The enclosure has not been printed yet. Its fit was checked in geometry only (Onshape with the official Raspberry Pi 5 model; our own clearance checks).

Validated: local assembly, printability and fit checks pass on the final design; real PreForm print job: 0 warnings, 23.0 min, 61.9 ml on one platform; benchmark (Claude Haiku, brief a, 3 seeds per mode): mean failures left team 4, generalist 6, one shot 20.
Not validated: no benchmark run passed every check (0/3 per mode), and the team costs more per run; the Sonnet benchmark is incomplete (4 of 18 runs); several sizes (plugs, cooler) are UNVERIFIED typical values; the tolerance plate is not printed, so measured clearances fall back to design-guide values; the FDM version was not checked by PreForm; the free-form part family is EXPERIMENTAL.
Try it without API keys: pip install -r requirements.txt, then streamlit run app.py. Mock mode (a rule-based stand-in model, labelled MOCK everywhere) is on by default; the best real run is preloaded."""

TECH = ["Python", "Claude (Anthropic API)", "Multi-agent systems", "CadQuery", "Formlabs PreForm (Local API / PreFormServer)",
        "Streamlit", "SQLite", "trimesh", "PyVista", "WebGL", "Playwright", "Onshape (STEP)",
        "CalculiX / Gmsh (teammates)", "FreeCAD / Fusion (teammates)"]
DOMAIN = ["Mechanical engineering", "Additive manufacturing / 3D printing", "CAD / design automation",
          "AI agents", "Manufacturing"]


def words(s):
    return len(re.findall(r"\S+", s))


CHECKS = [("Title option 1", len(TITLE_1), 32, "chars"), ("Title option 2", len(TITLE_2), 32, "chars"),
          ("One-sentence description", len(ONE_LINER), 100, "chars"),
          ("Project description", words(DESCRIPTION), 150, "words"),
          ("Formlabs answer", words(FORMLABS), 250, "words"), ("NVIDIA answer", words(NVIDIA), 250, "words")]


def main():
    bad = False
    for name, n, lim, unit in CHECKS:
        ok = n <= lim
        bad |= not ok
        print(f"{'OK  ' if ok else 'OVER'} {name}: {n}/{lim} {unit}")
    counts = "\n".join(f"| {n} | {c} / {lim} {u} | {'OK' if c <= lim else 'OVER'} |" for n, c, lim, u in CHECKS)
    md = f"""# Hackathon submission form: ready to copy-paste

Generated by `scripts/make_submission.py`, which also checks every limit (table at the end). Numbers come from the
run logs and result files (`DEMO.md`, "Numbers to say").

## Title (max 32 characters, pick one)
- `{TITLE_1}`
- `{TITLE_2}`

## Tech tags
{", ".join(TECH)}

## Domain tags
{", ".join(DOMAIN)}

## Project URLs
- Code (enclosure agents, this branch): {BR}
- Slides (PDF): {SLIDES}
- Demo video (MP4): {VIDEO}
- [Teammates: add the Jash_ver / main links once main is set up, see TEAM_MERGE_GUIDE.md]

## One-sentence description (max 100 characters)
{ONE_LINER}

## Start date
Oct 30, 2026

## Track
Main track

## Description of the project and the problem (max 150 words)
{DESCRIPTION}

## Public GitHub repo URL
{REPO} (enclosure agents on branch `Mo_ver`: {BR})

## Slide deck link
{SLIDES}
(Before submitting: delete either slide 6 "IF PRINTED" or slide 7 "IF NOT PRINTED", and let teammates replace
slides 11-12, then re-export. If the final deck lives on `main`, change the link.)

## Demo video link
{VIDEO}
(Optional: upload `results/submission/demo.mp4` to YouTube as unlisted and paste that link instead; GitHub plays
MP4 files under 100 MB in the browser.)

## Formlabs prize (max 250 words)
{FORMLABS}

## NVIDIA Platform General (max 250 words)
**ONLY IF teammates confirm the Part Designer live test used Nemotron. Otherwise leave this blank.**

{NVIDIA}

## Leave blank
- **Alphaxiv**: blank. We did not build on Alphaxiv papers or tools.
- **Nebius**: blank. We did not run on Nebius infrastructure.
- **ALCHEMI**: blank. No materials-chemistry simulation with NVIDIA ALCHEMI.
- **BioNeMo**: blank. No biology models.
- **E14**: note only: check the eligibility rules for this prize with the organizers before ticking anything; we
  have not confirmed that our project fits it.

## Additional info
{ADDITIONAL}

## Limits checked
| field | count | status |
|---|---|---|
{counts}
"""
    out = ROOT / "results" / "submission" / "SUBMISSION.md"
    out.write_text(md, encoding="utf-8")
    print("wrote", out)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
