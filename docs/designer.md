# Part designer: prompt to printed part

Open **http://127.0.0.1:8000/designer** (or *Part designer* in the top bar). Describe the part in
plain words, and optionally give the load, maximum bend, maximum weight and safety factor. The
material is Formlabs Alpha Fast Resin.

## What happens

1. **Draft.** A drafting agent turns the description into a *design spec* (`freeform.DesignSpec`),
   a small parametric CAD program:
   - boxes and cylinders, added or cut, optionally rotated;
   - every dimension a named parameter with an allowed range and a role (`thickness`, `size` or
     `position`);
   - **supports** (boxes on the held faces) and **loads** (boxes on the loaded faces, in newtons);
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
   review notes. Nothing prints until someone presses start.
6. **Real test.** Enter the measured bend at the load. Bend scales with 1/E, so the
   measured/predicted ratio calibrates the stiffness. The design is re-checked with the calibrated
   value, and later rounds use it.

## Retry limits

Defaults allow 12 drafting attempts, 12 optimization rounds, 8 review-requested revisions,
and 8 consecutive optimization rounds without improvement. Configure these in `.env` and
restart the server to apply them:

```dotenv
SCIENCECLAW_DESIGN_MAX_DRAFT_ATTEMPTS=12
SCIENCECLAW_DESIGN_MAX_ROUNDS=12
SCIENCECLAW_DESIGN_MAX_REVIEW_CYCLES=8
SCIENCECLAW_DESIGN_MAX_STALLED_ROUNDS=8
```

Each setting accepts 1–50. An explicit `rounds` value on the run API overrides the configured
optimization-round limit for that invocation. Existing token and spending guards still apply;
API failures and unresolved evidence conflicts can stop a run earlier. Increasing these limits
does not start a run automatically or waive a must-fix issue.

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
- These are simulations until the printed part is measured.
