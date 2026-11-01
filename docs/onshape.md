# Onshape → checks → PreForm → printer

## Without Onshape API keys

Onshape may refuse to create API keys for some accounts ("Your account is not currently
eligible to create API keys"). The API is optional. Two routes need no keys:

- **CoForm parametric CAD** (`cad_source: "local_fixture"`, the default): the
  STL is generated from the candidate's dimensions. For these prismatic sections it is
  exactly the analyzed part.
- **Manual Onshape export** (`cad_source: "uploaded"`): set the part's dimensions in
  Onshape to the candidate's values, export STL in millimeters, and use **Upload & check
  CAD** (`POST /api/runs/{run}/candidates/{candidate}/cad-upload?filename=part.stl`).
  The same mesh checks run. A mismatch is explained dimension by dimension in the
  diagnosis, e.g. "25 mm expected, 30 mm exported", together with the values to re-enter.

Both continue through PreForm and the specialist review exactly as below.

## With Onshape API keys

The agents' designs reach the printer through one parametric Onshape Part Studio:

1. A candidate's dimensions are written to four Onshape **Length variables**
   (`sc_length`, `sc_base_width`, `sc_wall_height`, `sc_thickness`). Nothing else in
   the document is edited. Unchanged variables are not rewritten.
2. CoForm confirms every feature regenerated, then creates an Onshape **version**
   named `CoForm <candidate_id>`. Versions are immutable, so each candidate has a
   permanent CAD snapshot.
3. A millimeter binary STL is exported **from that version**. CoForm checks the
   exact bytes that will be printed: closed mesh, extents and volume within 0.05 mm / 1%
   of the section the mechanics model analyzed. Onshape mass properties are an extra
   volume cross-check. A part that differs (fillets, holes, wrong family) is stopped here.
4. PreForm orients, supports, lays out and runs its seven printability checks, then saves
   a `.form` project (see [formlabs.md](formlabs.md)).
5. The **mechanics, fabrication and measurement specialists each review** the CAD check
   and PreForm report in their own domain. Deterministic gates (CAD match, no PreForm
   FAIL, thickness ≥ selected minimum wall, project built from the checked export) cannot
   be waived. At least two supporters are required.
6. Only an approved project of a study candidate can be uploaded to a printer queue.
   Uploading still requires a person to pick the printer and write review notes, and it
   always sends `print_now=False`: the operator starts the job at the printer.

Every step is an immutable record (`cad_revision`, `print_asset`, `print_project`,
`print_review`, `print_submission`, `print_receipt`) in the study's evidence export.

## One-time setup (about 10 minutes)

1. **API keys.** Sign in at <https://dev-portal.onshape.com/keys>, create an API key with
   *read* and *write* permission (OAuth is not needed). Put them in `.env` yourself; do
   not paste them into chat:

   ```dotenv
   ONSHAPE_ACCESS_KEY=...
   ONSHAPE_SECRET_KEY=...
   ONSHAPE_PART_STUDIO_URL=https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>
   ```

   The URL is the browser address of the **Part Studio tab** in the
   *CoForm - Physical Design* document. Enterprise stacks also set
   `ONSHAPE_BASE_URL=https://<company>.onshape.com`. Restart `coform serve`.
2. **Variables.** In the CoForm page, click **Create Onshape variables** (or
   `POST /api/onshape/bootstrap`). This adds any missing `sc_*` Variable features at the
   current study's reference dimensions. You can also create them by hand in Onshape
   (Variable feature, type Length); they must come **before** the geometry feature.
3. **Geometry feature.** In the Onshape document, create a **Feature Studio**, keep the two
   header lines Onshape generates, paste the body of
   [`onshape/coform_support_arm.fs`](onshape/coform_support_arm.fs), and commit.
   In the Part Studio, add the **CoForm support arm** feature (custom features menu),
   choose the section family that matches your study, and type the expressions
   `#sc_length`, `#sc_base_width`, `#sc_wall_height`, `#sc_thickness` into its fields.
   A teammate may instead model the part by hand, provided its dimensions reference
   the same four variables. Then the geometry check verifies that hand-built model too.
4. Click **Check Onshape**. It should report all four variables, at least one geometry
   feature, and no regeneration errors.

The custom feature has not yet been run inside Onshape from this workspace. If it fails
to regenerate, Onshape's feature error explains why; the hand-modeled option above
remains available.

## API

| Route | Effect |
|---|---|
| `GET /api/onshape/status` | Reads features; reports variables, geometry, regeneration errors |
| `POST /api/onshape/bootstrap` | Adds missing `sc_*` variables (`{"run_id": ...}` for its dimensions) |
| `POST /api/runs/{run}/candidates/{candidate}/onshape` | Write variables, version, export, check |
| `POST /api/runs/{run}/print-pipeline` | Onshape (or `local_fixture`) → check → PreForm → specialist review |
| `POST /api/runs/{run}/print-reviews` | Re-review an existing candidate project (`live` optional) |
| `POST /api/formlabs/projects/{project}/queue` | Human-confirmed queue upload of an approved project |

`print-pipeline` body: `{"candidate_id", "profile": {machine_type, material_code,
layer_thickness_mm, print_setting, minimum_wall_mm}, "cad_source": "onshape" |
"local_fixture", "live": false}`. `live: true` asks the three configured model
providers for the review and charges the study's token budget. `local_fixture` uses
CoForm's own STL, labeled as such, for testing without Onshape credentials.

Authentication uses Onshape API keys over HTTP Basic, which Onshape documents as
suitable for local use. Export redirects are followed only within `*.onshape.com`;
credentials are never forwarded elsewhere. Error messages include Onshape's short
`message` text but never keys. Tested against a fake Onshape server; not yet against
the live service.
