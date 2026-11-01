# Part designer: prompt to printed part

Open **http://127.0.0.1:8000/designer** (or *Part designer* in the top bar). Describe the part in
plain words, and optionally give the load, maximum bend, maximum weight and safety factor. The
material is Formlabs Alpha Fast Resin.

## What happens

0. **Concept.** In live mode a fresh part starts with a concept, not coordinates (`concepts.py`).
   The mechanics agent picks a tested mount (`desk_edge` or `wall`) and a tested holder (`cup`,
   `cradle` or `hook`) and gives the numbers the request sets, for example "desk_edge + cup; desk
   25 mm, bottle 75 mm, 1.5 kg". That reply is about 300 tokens, against about 3,000 for a
   freehand draft. Fabrication and measurement review the choice before anything is built (can
   it print, can it be tested). A must-fix objection sends it back to mechanics once. Code then
   assembles the chosen parts at the same positions the tested assemblies use, so they fit
   (`tests/test_concepts.py` checks all six combinations at both ends of every number range).
   The result is attempt 1 below, and every later step is unchanged. Numbers outside a tested range
   go back to the agent as an objection from code. Numbers the request leaves out take the tested
   default, and the default is reported as an assumption. The user's own load and limits override the
   agent's numbers. If no combination fits, or two concept calls in a row are lost, the agent drafts
   freehand as described next. A resumed part keeps repairing its saved attempts and does not pick
   a new concept. The concept and its reviews are listed under **Saved drafting attempts**.

1. **Draft.** A drafting agent turns the description into a *design spec* (`freeform.DesignSpec`),
   a small parametric CAD program:
   - boxes and cylinders, added or cut, optionally rotated;
   - every dimension a named parameter with an allowed range and a role (`thickness`, `size`,
     `position`, or `fixed` for dimensions the request sets, such as desk thickness or bottle diameter);
   - the **environment**: the desk, wall, bottle, strap or screws the part works with, at true size
     (see [Function before strength](#function-before-strength));
   - optionally, tested **components** (clamp, cup, cradle, wall plate, hook) built from those bodies;
   - requirements.

   Gmsh's OpenCASCADE kernel must build it into **exactly one solid**, or the draft goes back to
   the agent with the kernel's reason (up to 12 attempts by default). A draft with a sealed cavity is also sent
   back, with the cavity's location.

   The drafting prompt first asks for a general **design intent**: function, contact/mating
   interfaces, keep-clear space, assembly/tool access, load path and provisional assumptions.
   This applies to enclosures, clamps, hooks and other parts; it is not a wall-hook template.
   The brief travels with the part into reviews and subsequent repairs. It is an agent-authored
   interpretation, not an independently verified clearance check.

   After a failed draft, the agent receives the previous spec and up to eight saved attempts.
   It returns a bounded repair of existing parameters, features or support/load regions instead
   of replacing the whole design. Repairs preserve feature identities, applied forces and
   requirements. Functional pockets are not automatically deleted to eliminate resin traps.
   Repeated geometry is rejected without another solve.

   All three specialists must return usable reviews. Must-fix issues need evidence references
   and a concrete explanation. A claim contradicting a cited passing check, or missing evidence,
   gets one clarification request to that reviewer. An unresolved conflict stops the draft for
   attention; unresolved must-fix issues are never accepted just because the revision limit ran out.
2. **Test.** Each design is meshed (quadratic tetrahedra, element size tied to the thinnest wall)
   and solved by CalculiX. The checks are:
   - bend at the load;
   - peak von Mises stress (99.5th percentile, excluding the idealised fixed boundary) against
     46 MPa ÷ the safety factor;
   - mass (1.22 g/cm³);
   - minimum wall (1.0 mm design rule);
   - fit in a 200 × 125 × 210 mm build volume (PreForm checks the real fit);
   - no sealed internal cavities (a pocket that doesn't reach the outside traps liquid resin);
   - a closed, watertight surface.

   Every exported STL is repaired before use: Gmsh orients each surface patch on its own, and after
   booleans some patches came out inside-out, which slicers showed as broken. The repair makes the
   winding consistent, points the outer skin outward and any cavity inward, and recomputes the normals.
3. **Adjust.** Each round:
   - mechanics (strength and stiffness) and fabrication (mass and printability) propose parameter
     changes;
   - measurement writes the physical test plan and flags bends too small to measure;
   - the rig tests each new, valid proposal; all three then review its actual results;
   - the evidence picks the next design: the lightest that
     passes, or the closest to passing. Peer support breaks ties.

   Agents receive recent rejected changes and their failed checks. A repeated parameter
   combination at the same material calibration is not tested again. One unsuccessful round
   leads to another correction; eight consecutive unsuccessful rounds stop as **stalled**, not
   as proof of convergence. Each invocation has at most 12 optimization rounds by default.

   Draft attempts, review evidence, usage and progress are saved in SQLite. Every candidate
   gets its own artifact folder; resuming never overwrites earlier geometry. The UI shows
   **Saved drafting attempts**, and **Resume drafting from saved failures** works even when
   no initial draft was accepted. Interrupted work is labelled after a server restart and does
   not automatically spend more credits. Older runs cannot recover history they never saved.
   For an older design with outstanding must-fix issues, **Repair design review issues** first
   rechecks its geometry and asks the reviewers to resolve the existing concerns. Confirmed
   concerns then go through targeted repair. Printing cannot waive those issues, and tests or
   print approvals from a superseded design cannot be used for the repaired version.
4. **View.** *Open in Fusion* asks the add-in to import the STEP into a new Fusion document
   (add-in 0.3.0: stop and run it under Utilities › Add-Ins). *Download STEP* / *Download STL* always work.
5. **Print.** *Prepare & check*: PreForm imports, orients, supports and validates the best passing
   design. Deterministic gates (PreForm grade, minimum wall, build volume) cannot be waived, and
   two of the three agents must approve. Only then can it go to the printer queue, with your
   review notes. Nothing prints until someone presses start. The print card shows the verdict
   (PASSED or NOT APPROVED) and every reason it was blocked. Each specialist's vote shows whether
   it approves, objects or gave no usable reply, with its reason and up to three suggestions for
   printing or testing. A vote cut off at the token cap is asked once more before it counts.
6. **Real test.** Enter the measured bend at the load. Bend scales with 1/E, so the
   measured/predicted ratio calibrates the stiffness. The design is re-checked with the calibrated
   value, and later rounds use it.

## Retry limits

Defaults allow 12 drafting attempts, 12 optimization rounds, 8 review-requested revisions,
and 8 consecutive optimization rounds without improvement. Drafting stops after 3 attempts without
measured progress; reducing a failed check's value or removing failures permits further attempts.
Configure these in `.env` and
restart the server to apply them:

```dotenv
COFORM_DESIGN_MAX_DRAFT_ATTEMPTS=12
COFORM_DESIGN_MAX_ROUNDS=12
COFORM_DESIGN_MAX_REVIEW_CYCLES=8
COFORM_DESIGN_MAX_STALLED_ROUNDS=8
COFORM_DESIGN_MAX_STALLED_DRAFT_ATTEMPTS=3
```

Each setting accepts 1–50. An explicit `rounds` value on the run API overrides the configured
optimization-round limit for that invocation. Existing token and spending guards still apply;
API failures and unresolved evidence conflicts can stop a run earlier. Increasing these limits
does not start a run automatically or waive a must-fix issue.

## Repairs preserve evidence

A draft that passes geometry and functional gates becomes a checkpoint, even if a reviewer still
has an objection. Every proposed repair is tested as a separate candidate. Losing a passed hard check,
breaking a declared component relationship, or failing to build rolls the repair back. Failed candidates
and their diagnostics stay in the history. Every candidate that passes the gates is kept, but the
checkpoint is the *best* of them: a passing repair against which reviewers verify more must-fix issues
than the current checkpoint stays a candidate, and the next repair starts from the better one. Before any
checkpoint exists, a tested repair replaces the working draft only if it got at least as far (did not
build < built but does not work < works but fails a gate < passes; then fewer findings), so a worse
branch is rolled back even then. Resume selects the best tested checkpoint (fewest verified must-fix
issues, then the latest) ahead of later failed branches and rechecks its geometry and reviews; it does
not silently approve it. Historical runs with saved checks can recover a checkpoint even if they predate
the explicit checkpoint flag.

Repeated geometry is compared after evaluating expressions, so replacing a dimension with an equivalent
literal or renaming a feature does not buy another simulation. After three attempts without measured
progress (by default), the drafter is asked once for a different mechanism: a redesign, built from
components where one covers it, keeping the world and requirements. If that makes no progress either,
the search stops instead of spending the remaining attempts. Existing overall attempt, review and token
limits still apply.

Review citations and verified claims are distinct. A must-fix objection must cite a failed code check
or provide a structured claim checked by code: a numeric comparison using supplied `measure:` references,
an `axial_span` test of an intersecting cut through an axis-aligned primitive, or `check_failed`.
False or unsupported claims get one clarification. Remaining unknowns preserve the checkpoint for
review and block approval; a citation or a reviewer majority alone cannot verify geometry. Axial-span
proofs apply to the named primitive, not global drainage or arbitrary print orientations.

Missing contact includes a CSG diagnostic when a cut removed supporting material: the required body
face, plane, added feature, subtractive feature and sample counts. When the support simply stops short,
it names the nearest material beyond that face and how far away it is ("the nearest material under
'bottle' is 'cup floor', 3.0 mm away"), or says there is none in the body's footprint; when both apply,
the nearby material leads, since moving or extending reaches it. Sampling identifies the cause; the
mesh checks still decide pass/fail. These diagnostics work by geometry and operation order, not names
such as "cup" or "bottle".

Components also emit `relationships`, comparisons between named feature bounds (`min`/`max`, axis,
`le`/`eq`/`ge`, optional parametric offset). For example, an interior cut stays above its floor, a wall
meets that floor, and a through-hole spans its target plate. Parameter edits reevaluate the expressions;
repairs cannot remove the relationships. Changes that break them fail before CAD/FEA. Any custom part
can declare the same relationships, and a concept redesign may establish a new set while preserving
the world and requirements. These face constraints complement, rather than replace, functional checks.

## Function before strength

A strong, light part is worthless if it does not do its job. A live bottle holder once passed every
check at 110 g although its lower jaw filled the space where the desk goes: its "support" was a
30 × 75 × 25 mm volume glued in place, so the simulation assumed the clamp already worked. The
designer now establishes the mechanism first.

- **Reference bodies.** `environment` lists what the part works with, as axis-aligned boxes and
  cylinders: a `mount` (desk, wall), a `payload` (bottle, strap, with its force), `hardware` (screws,
  nuts) and `keep_clear` space. Their sizes may use only numbers and `fixed` parameters, so neither
  the optimiser nor a repair can change the world to make a design pass.
- **Functional contract and checks.** Each body states the faces the part must `touch` (a desk clamp
  needs the desk's `+z` and `-z`), the way it is `insert`ed (whose path must be clear: tool access,
  sliding onto the desk, dropping the bottle in) and the directions the part must `retain` it. A
  clamp screw `bears_on` the desk and then counts as touching it. On the part's mesh nodes, code
  checks **fit** (no material inside any body), **contact**, **insertion** and **retention**, and names
  the features and coordinates involved. Any failure stops the evaluation before the strength
  solve: strength is not computed for a part that does not work.
- **Supports and loads come from contact.** The simulation is held only where the part touches its
  mounts (and bearing screws) and loaded only where it touches its payloads.
- **Unverified is not a pass.** A design without reference bodies reports `function: unverified`,
  never passes, and a draft cannot reach optimisation like that.
- **Components** (`coform/components.py`): `desk_clamp` (jaws, spine, thumb screw in a slide-in nut
  seat), `cup`, `cradle`, `wall_plate` (with screws), `hook`, and `join` (a bridge code measures between
  two labelled pieces). Each is built from the body it serves, so it fits by construction: the same
  clamp call fits an 18 mm or a 40 mm desk. Their voids are cut after every added feature, and their
  screws and nuts arrive as hardware, so access and engagement are checked too. Each has tests that
  build it into a complete part that passes. Live drafts that hand-built clamps and cups failed 12 of
  12 attempts, so the drafter is told to use components, and its worked example
  (`templates/desk_bottle_holder.json`) is built from them, sized from fixed dimensions so it still
  fits a 65 or 90 mm bottle and an 18 or 40 mm desk.
- **What may move.** Sizes and forces come from the request. A desk, wall or keep-clear space never
  moves; a payload or screw stands where the design puts it, so its `at` may use any parameter and a
  repair may move it.
- **Code fixes what code can measure.** A draft that builds into separate pieces is joined at once
  by a bridge across the pieces' shared extent (inserted before every cut, so cups and holes stay
  open) instead of spending another AI call on coordinates. A repeated placement mistake (a piece
  through the wall, a piece shifted sideways) is still fixed in code on the second occurrence.
- **Adjust or replace.** A repair still changes at most six things (pieces moved together by one
  offset count once). When the evidence shows the concept is wrong, the drafter may return
  `{"redesign": SPEC}`: a new mechanism that keeps every mount and keep-clear body, every payload's
  size, force and contract, every fixed dimension and the requirements. The bottle may stand elsewhere
  and the screws may change. At most two redesigns per drafting run.
- **Slow or overloaded provider.** A call may take as long as writing its longest allowed reply at 20
  tokens/s (a full draft about 4 minutes); NVIDIA's shared endpoint has dropped to 29 tokens/s. After an
  HTTP 429/502/503/504 the drafter waits 20 s before asking again.
- **The assembled scene.** The 3D view draws the reference bodies as see-through shapes coloured by
  role, and the checks table lists, per body, what touches it, what holds it on each side and
  whether it can be put in place. Reviewers receive the same evidence as `world` and can cite
  `scene:<body>`. Visual review helps a person spot wrong concepts; the geometric checks are the
  gate. An image-based AI reviewer is not built: the configured agents receive JSON, not renders.

## Modes

- **Live AI:** the agents from `.env`, Nemotron by default. A previous version was tested on "a hook that screws under a
  desk and holds a 4 kg backpack, max 2 mm bend":
  - two drafts were rejected (two separate solids), and the third built;
  - five rounds went from 56.5 g to 10.6 g with every wall at 1.5 mm;
  - about 6 minutes and about 30 AI calls.
  The repaired workflow is covered by fake-agent and local CAD tests; these earlier live results
  do not establish the success rate of the new prompts.
- **Rule-based:** a built-in shelf-bracket template stands in for the drafting agent, and
  thickness scaling (bend ∝ 1/t³) stands in for the proposals. No API calls; about 30 s.

## Limits

- Shapes are unions and cuts of boxes and cylinders (no fillets, lofts or sweeps).
- Linear elasticity: no creep, large deflection, contact or print anisotropy.
- Peak stress converges more slowly than bend (12% between 3 mm and 1.5 mm elements on the
  bracket; bend 2%).
- Functional checks use the mesh nodes (corner nodes for collisions, 0.2 mm tolerances), so an
  overlap thinner than about one element can slip through. Contact is treated as bonded: friction,
  screw preload and sliding are not modelled. Reference bodies are axis-aligned boxes and cylinders.
- Retention means something is in the way somewhere along that direction; it does not yet measure
  how high a lip is relative to the payload's centre of mass.
- These are simulations until the printed part is measured.
