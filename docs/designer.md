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
   the agent with the kernel's reason (up to 4 attempts).
2. **Test.** Each design is meshed (quadratic tetrahedra, element size tied to the thinnest wall)
   and solved by CalculiX. The checks are:
   - bend at the load;
   - peak von Mises stress (99.5th percentile, excluding the idealised fixed boundary) against
     46 MPa ÷ the safety factor;
   - mass (1.22 g/cm³);
   - minimum wall (1.0 mm design rule);
   - fit in a 200 × 125 × 210 mm build volume (PreForm checks the real fit).
3. **Adjust.** Each round:
   - mechanics (strength and stiffness) and fabrication (mass and printability) propose parameter
     changes;
   - measurement writes the physical test plan and flags bends too small to measure;
   - all three review each other;
   - the rig tests every valid proposal, and the evidence picks the next design: the lightest that
     passes, or the closest to passing. Peer support breaks ties.

   The loop stops when nothing beats the current design, after at most 6 rounds; *Let the agents
   keep improving it* runs more.
4. **View.** *Open in Fusion* asks the add-in to import the STEP into a new Fusion document
   (add-in 0.2.0: reload it under Utilities › Add-Ins). *Download STEP* / *Download STL* always work.
5. **Print.** *Prepare & check*: PreForm imports, orients, supports and validates the best passing
   design. Deterministic gates (PreForm grade, minimum wall, build volume) cannot be waived, and
   two of the three agents must approve. Only then can it go to the printer queue, with your
   review notes. Nothing prints until someone presses start.
6. **Real test.** Enter the measured bend at the load. Bend scales with 1/E, so the
   measured/predicted ratio calibrates the stiffness. The design is re-checked with the calibrated
   value, and later rounds use it.

## Modes

- **Live AI:** the agents from `.env`, Nemotron by default. Tested on "a hook that screws under a
  desk and holds a 4 kg backpack, max 2 mm bend":
  - two drafts were rejected (two separate solids), and the third built;
  - five rounds went from 56.5 g to 10.6 g with every wall at 1.5 mm;
  - about 6 minutes and about 30 AI calls.
- **Rule-based:** a built-in shelf-bracket template stands in for the drafting agent, and
  thickness scaling (bend ∝ 1/t³) stands in for the proposals. No API calls; about 30 s.

## Limits

- Shapes are unions and cuts of boxes and cylinders (no fillets, lofts or sweeps).
- Linear elasticity: no creep, large deflection, contact or print anisotropy.
- Peak stress converges more slowly than bend (12% between 3 mm and 1.5 mm elements on the
  bracket; bend 2%).
- These are simulations until the printed part is measured.
