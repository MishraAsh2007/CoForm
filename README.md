# CoForm: an agent collective that designs, tests and prints physical parts

> Built at the **ScienceClaw hackathon**, MIT Media Lab, October 30 – November 1, 2026.
> Challenge area **A·03 Manufacturing & Materials** (from candidate discovery to fabrication),
> with elements of **A·02 Physical Agency** (the agents drive CAD, a finite-element rig and a
> Formlabs printer's software).

Describe a part in plain words, for example *"build a water bottle holder which can attach to a desk
and hold it"*. Three specialist agents (mechanics, fabrication and measurement) agree on a concept.
Code builds it as parametric CAD and tests it against the desk, bottle and screws it must work with.
The agents then lighten it with 3D finite-element tests, and vote on whether it may go to the
printer queue. No agent directs the others, and no agent's claim counts until code has measured it.

<p align="center">
  <img src="docs/images/bottle-holder-in-use.png" width="49%" alt="The agents' desk-edge water bottle holder clamped on a desk, holding a bottle">
  <img src="docs/images/backpack-holder.png" width="49%" alt="The agents' wall-mounted backpack holder with its 100 N load at the hook tip">
</p>
<p align="center"><sub>Two parts the live agent team designed from one sentence each, shown in CoForm's 3D viewer
from the saved finite-element results (true scale). Left: desk-edge water bottle holder; the desk and
bottle are the reference bodies its fit was checked against. Right: wall-mounted backpack holder,
now being printed. Colour is stress relative to each part's own peak (lighter = higher).</sub></p>

This README follows the hackathon's brief: each project must show a **problem**, a
**functioning system**, a **result** and **validation**. Judging weights significance (20%),
impact (25%), decentralized agency (20%), collective capability (25%) and execution (10%).
Each section below answers one of those.

| Hackathon asks for | Where it is in this README |
|---|---|
| Problem; significance & complexity (20%) | [The problem](#the-problem) |
| Functioning system | [How it works](#how-it-works) |
| Decentralized agency (20%) | [Who decides what](#who-decides-what-decentralized-agency) |
| Result; scientific/technical impact (25%) | [Results: two real parts](#results-two-parts-the-agents-made) |
| Collective capability (25%) | [Does the collective add capability?](#does-the-collective-add-capability) |
| Execution & validation (10%) | [Validation and limits](#validation-and-limits) |

## The problem

Getting a small functional part from an idea to a printer still takes a person who can do CAD, judge
the strength and know the printing process. A language model can describe a bottle holder, but it
cannot be trusted to place hundreds of coordinates correctly. A "passing" simulation also proves
little if the model never checked that the part fits the desk, can be screwed on, or lets the
bottle drop in.

Three things make it hard:

- **The part must work, not just be strong.** It has to fit the body it attaches to, touch the
  right faces, let things be inserted and hold them in, and print without trapping resin.
- **No single discipline owns the answer.** Structure, printability and testability pull in
  different directions: lighter is weaker, and supports scar the faces that do the work.
- **Evidence has to beat confidence.** Agents confidently claim failures that are not there. In
  our runs a reviewer twice called a drain hole blind while the measured geometry showed it went
  through. Someone has to settle such disputes with measurements.

## How it works

```
 request ─► 1 CONCEPT ─► 2 BUILD & TEST ─► 3 REVIEW ─► 4 OPTIMISE ─► 5 PRINT ─► 6 MEASURE
            mechanics     code: CAD,         all three    proposals    PreForm +    real bend
            proposes;     fit / contact /    specialists  by each      hard gates + recalibrates
            fab + meas.   insertion /        (claims      specialist,  2 of 3       stiffness
            review        retention, FEA     measured)    FEA picks    agent votes
```

1. **Concept.** The mechanics agent picks tested parts and the numbers the request sets, for
   example *"desk_edge + cup; desk 25 mm, bottle Ø75 mm, 1 kg"*. That reply is about 300 tokens,
   where a freehand draft is about 3,000. Fabrication and measurement review the choice before
   anything is built. Code then assembles the parts, each built to fit the body it serves: a
   C-clamp with a thumb screw in a captive nut, a cup with a drain, a cradle, a wall plate with
   screws, a hook. If no tested part fits, the agent drafts the CAD freehand instead.
2. **Build and test.** The CAD program is built into one solid with the Gmsh/OpenCASCADE kernel.
   Code checks it against the world it works in (desk, wall, bottle, strap, screws):
   - **fit:** no material inside any body;
   - **contact:** it touches the faces it must;
   - **insertion:** every item and tool has a clear path into place;
   - **retention:** the item is blocked in every direction that matters.

   Only a part that works is tested for strength, with 3D finite elements (Gmsh + CalculiX)
   using Formlabs Alpha Fast Resin properties. Failures go back to the agents with the
   measured reason, up to 12 attempts.
3. **Review.** Each specialist reviews the built part from its own field. A blocking objection
   must be a claim code can check, such as "hole radius < 3.2 mm". Code measures each claim. A
   claim still contradicted by measurement after one clarification round is overruled and shown
   as a note.
4. **Optimise.** Each round, every specialist proposes dimension changes within its charter, and
   they review each other. The rig tests every valid proposal, and the evidence picks the
   winner: the lightest design that passes.
5. **Print.** Formlabs PreForm imports, orients, supports and checks the part. Hard gates
   (PreForm grade, part checks, minimum wall) cannot be waived. Then at least two of the three
   specialists must approve, each giving reasons and suggestions, before it can be queued.
6. **Measure.** The measured bend of the printed part recalibrates the material stiffness, and
   the design is rechecked with it.

Everything is saved as append-only, hash-chained records: every attempt, reply, review, test and
vote. Any decision can be traced to its evidence.

## Who decides what (decentralized agency)

- **Three specialists with separate charters, and no master agent.** Mechanics owns strength and
  stiffness, fabrication owns printability, mass and resin, and measurement owns the physical test.
  Each can run on a different model provider (OpenAI, Anthropic, Gemini or NVIDIA). In the runs
  below, all three ran on NVIDIA's Nemotron 3 Ultra.
- **Decisions come from proposals, peer review and evidence, not from a planner.** Mechanics
  proposes the concept, the other two review it, and code builds it. In optimisation, any
  specialist may propose, and the finite-element test picks the winner. Printing needs two
  independent votes.
- **Code is the referee, not a participant.** It never chooses the design. It measures claims,
  runs the checks and enforces the gates, so an agent cannot argue past a measurement. Equally, an
  objection that code verifies cannot be outvoted.
- **The agents adapt to failure.** Repairs start from the best checkpoint and failed branches are
  rolled back. When repairs stall, the agents are asked once for a different mechanism. Code fixes
  what it can measure exactly, such as joining pieces that do not touch.

## Results: two parts the agents made

Both parts were designed by the live agent team (Nemotron 3 Ultra) from one plain-language
request, with no human CAD. The holding and loading conditions came from the agents' own design.

### Wall-mounted backpack holder (being printed)

> *"Create a wall mounted backpack holder, make sure there is no extrusions through the wall
> mounted holder and also make sure there no [holes] which resin can get trapped in without
> draining properly"*

<p align="center"><img src="docs/images/backpack-holder.png" width="70%" alt="Wall-mounted backpack holder: back plate with four screw holes, arm, gusset and hook tip, with the 100 N load arrow"></p>

The agents designed a vertical back plate screwed to the wall, a horizontal arm with a hook tip,
and a triangular gusset at the joint. The render shows the stress peaking where the arm meets the
plate. Every screw hole and the drain at the tip goes all the way
through, so no resin is trapped. It carries a 100 N backpack load at the tip.

| | First draft | After optimisation (adopted) | Limit |
|---|---|---|---|
| Mass | 118.5 g | **99.1 g (−16%)** | — |
| Bend at the load | 1.30 mm | 1.81 mm | ≤ 2 mm |
| Peak stress | 8.4 MPa | 11.6 MPa | ≤ 23 MPa (strength ÷ safety factor 2) |

In round 1, fabrication's proposal (thinner back plate, gusset and arm) won on the finite-element
evidence over mechanics' lighter but over-bending one. Eight further rounds found nothing lighter
that still passed, so the run stopped. PreForm graded the part WARN (3.9% of the surface not fully
supported, no failures). The estimate is 1.5 h and 117 mL of resin with supports. All three
specialists voted to print. This part was drafted freehand, before the concept step existed,
with its wall mounting and load written by the agents as hold and load regions.

### Desk-edge water bottle holder

> *"build a water bottle holder which can attach to a desk and hold it"*

<p align="center">
  <img src="docs/images/bottle-holder.png" width="49%" alt="Desk-edge bottle holder: upper and lower clamp jaws with screw hole and nut slot, spine, web and cup">
  <img src="docs/images/bottle-holder-in-use.png" width="49%" alt="The bottle holder clamped on the desk edge with the bottle standing in the cup">
</p>
<p align="center"><sub>Left: upper and lower jaws (thumb-screw hole and slide-in nut slot), spine past the desk
edge, web and cup. Stress peaks where the web meets the cup. Right: clamped on the 25 mm desk with
the bottle standing in the cup.</sub></p>

Mechanics chose a concept: a desk-edge clamp plus a cup, for a 1 L bottle (Ø75 mm, 200 mm, 1 kg)
on a 25 mm desk. The request gave no sizes, so these were tested defaults, reported as
assumptions. Fabrication and measurement approved the concept. Code assembled it: a C-clamp with
an M6 thumb screw in a captive nut, a cup with a drain hole, and a web joining them. It passed
every check on the first try, including fit, contact, insertion and retention against the desk,
bottle, screw and nut.

| | First draft | After optimisation (adopted) | Limit |
|---|---|---|---|
| Mass | 158.1 g | **125.4 g (−21%)** | — |
| Bend at the load | 0.03 mm | 0.08 mm | ≤ 2 mm |
| Peak stress | 0.38 MPa | 0.76 MPa | ≤ 23 MPa |
| Size | 139 × 83 × 89 mm | 138 × 81 × 85 mm | 200 × 125 × 210 mm build volume |

Choosing and reviewing the concept took 3 model calls and 3,123 tokens. The three specialists
approved the built design. In round 1, mechanics' change won, and eight more rounds found
nothing lighter. PreForm graded the part WARN (0.5% of the surface unsupported). The estimate is
1.8 h and 157 mL of resin with supports, and the three specialists voted 3 of 3 to print.

## Does the collective add capability?

We measured this rather than assumed it. The answer depends on the task.

**On a narrow numeric task, no.** On the beam-study benchmark, the agents size a printed
support beam on a simulated test rig with equal budgets. Each method had 4 runs: 2 scenarios ×
2 seeds, Alpha Fast resin, all agents on Nemotron 3 Ultra.

| Method | Passed | Runs that saved material | Mean volume vs reference | Tokens per run | Calls per run |
|---|---|---|---|---|---|
| Adaptive numerical optimizer (no LLM) | 4/4 | 4/4 | **0.76** | 0 | 0 |
| Specialist collective | 4/4 | 1/4 | 0.93 | 161k | 48.5 |
| Single agent with the same tools | 4/4 | 2/4 | 0.88 | 31k | 7.8 |
| Specialists without evidence exchange | 4/4 | 2/4 | 0.87 | 52k | 21 |

In a second set (nominal and soft-material scenarios, agents on the smaller Nemotron 3.5
Lightning), the optimizer reached 0.69 and no LLM
method improved on the reference. For tuning a few numbers against a known model, a plain
optimizer wins, and the collective costs the most. Data: `data/comparison-live-alpha-fast/` and
`data/comparison-live-nemotron/`.

**On open-ended design, the collective's job is judgement, not arithmetic.** We moved the agents
to the decisions only they can make: which mechanism, whether it can print, and how it will be
tested. Code does the millimetres. On the bottle-holder request, the overnight freehand runs
(one model writing the whole CAD) never produced a design that passes today's checks. One passed
the older checks, but its clamp jaw filled the gap meant for the desk. It is kept as a
regression test (`tests/fixtures/live_bottle_holder.json`). With concepts, the first live run
built and passed every check, though review then stopped it on claims the measurements
contradicted. After we made measurement the referee, the next run went from request to approved
print on its own. This is a before/after on one request, not a controlled comparison: the code
and checks also changed between runs.

The reviewers also caught a real concern code did not: the clamp's M6 hole has no clearance for
the thread. It was raised as a should-fix and has not been fixed yet.

## Validation and limits

**Verified in software**
- 348 automated tests. They cover the component library, every concept combination at both ends
  of its tested size ranges, the function checks, repair and rollback, review arbitration, print
  gates and votes. Fake agents exercise the whole pipeline without spending credits.
- Finite-element results are checked against linear-elastic scaling (twice the stiffness gives
  half the bend). The CAD-kernel volumes and second moments match beam formulas.
- Real PreForm 3.65 preparations of both parts above, with PreForm's own checks: manifold, in
  bounds, no resin cups, no thin walls.

**Not yet established**
- The backpack holder is being printed. As of this commit, no printed part has been loaded and
  measured. The measurement loop (measured bend → recalibrated stiffness → recheck) is
  implemented and tested with simulated numbers.
- Strength and bend are finite-element predictions using datasheet resin properties. They are not
  certified ratings.
- The comparison is small (4 runs per method) and uses a simulated rig. It shows where the
  collective does not help. It does not prove the general case either way.
- The tested parts cover desk edges and walls holding cups, cradles and hooks. Other mechanisms
  fall back to freehand drafting, which is much less reliable.

## Run it

Requires Python 3.11+, and FreeCAD 1.1 for its bundled Gmsh and CalculiX finite-element tools.
Formlabs PreForm is needed only for print preparation.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,formlabs]"
.\.venv\Scripts\python.exe -m coform.cli serve
```

Open **http://127.0.0.1:8000/designer** and describe a part. Without API keys, a built-in
template and rule-based agents stand in, labelled as such. To use live agents, copy `.env.example`
to `.env` locally and set each role's provider, for example:

```
MECHANICS_PROVIDER=nvidia
FABRICATION_PROVIDER=nvidia
MEASUREMENT_PROVIDER=nvidia
NVIDIA_API_KEY=...            # never commit keys
NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b
```

Run the tests with `.\.venv\Scripts\python.exe -m pytest -q`.

## Repository map

| Path | What it does |
|---|---|
| `coform/designer.py` | The designer: concept, drafting, review, optimisation and print stages |
| `coform/concepts.py` | Concept catalogue and assembly: tested mounts × holders, numbers within tested ranges |
| `coform/components.py` | Tested parts built from the body they serve: clamp, cup, cradle, wall plate, hook, join |
| `coform/freeform.py` | Parametric CAD spec, solid build, meshing and finite-element analysis |
| `coform/scene.py` | Fit, contact, insertion and retention checks against the reference bodies |
| `coform/designer_repair.py` | Repairs, redesigns, and the machine-checkable review claims |
| `coform/agents.py`, `coform/providers.py` | Metered model calls; OpenAI, Anthropic, Gemini and NVIDIA adapters |
| `coform/formlabs.py` | Formlabs PreForm integration: preparation, checks, printer queue |
| `coform/fusion.py`, `fusion/` | Autodesk Fusion add-in: open any tested design in Fusion |
| `coform/static/` | The web interface |
| `docs/` | [Designer details](docs/designer.md), [beam-study lab](docs/study-lab.md), [Formlabs](docs/formlabs.md), [Fusion](docs/fusion.md), [validation status](docs/validation-status.md) |

The server binds to `127.0.0.1` and has no authentication. Keep it local.
