# How to test the tolerance print (no calipers needed)

Parts: `plate.stl` (88 x 60 mm) and `key.stl` (46 x 12 mm, with a 5.00 mm peg and a 2.00 mm tongue).
You also need: 2-3 M2.5 screws (6-10 mm long), 2-3 M2.5 hex nuts, a small screwdriver.

**Print** both parts in the same material and layer thickness as everything else (`print:` in config.yaml: Alpha Fast V1, 0.15 mm; see `PRINT_SETTINGS.json`).

**Before testing:** wash, dry fully, and post-cure **both parts the same way you will treat the enclosure**,
and remove all support nubs. Don't sand any test feature. Fill in `data/fit_test_results.yaml` as you go.
Write down the cure settings and orientation under `meta`.

Every feature has its value engraved below it. The row letter is on the left edge.

| Word | Meaning |
|---|---|
| `free` | goes in with no force and wobbles |
| `snug` | goes in with light finger force, no wobble, comes out by hand |
| `press` | needs firm thumb pressure or a light tap, stays put |
| `no_fit` | won't go in |
| `cracked` | part cracked or split |
| `failed_to_print` | feature is missing, closed over, or broken off |

## Row P: peg holes (5.0 to 5.5)
1. Push the key's **peg** straight down into each hole, starting with the largest (5.5).
2. Push it all the way through. Don't twist. Stop at firm thumb pressure.
3. Record the word. Expect `free` on the big holes and `snug`/`press`/`no_fit` going smaller.

## Row G: grooves (2.0 to 2.5, 3 mm deep)
1. Push the key's **tongue** straight down into each groove until the handle sits on the plate.
2. Record the word. This sets the lid lip clearance. A good lid fit is `free` or `snug`.

## Row H: M2.5 through-holes (2.5 to 3.0)
1. Push an M2.5 screw through each hole **by hand, without turning it**.
2. `free` = drops through, `snug` = needs a little push, `press` = only goes in if you turn it (it cuts threads), `no_fit` = won't start.

## Row N: hex nut pockets (5.0 to 5.4 across flats, 2.2 mm deep)
1. Line up the nut flats with the pocket flats and press it in flat with your thumb.
2. `free` = falls out if you flip the plate, `snug` = stays in when flipped and comes out with a fingernail,
   `press` = needs a firm push, `no_fit` = won't seat flat. Note if the pocket wall `cracked`.
3. Optional: put a screw through from the bottom and tighten into the nut. Write in `notes` whether the nut spins.

## Row T: screw posts (outer diameter 5, 6, 7 mm, 2.1 mm pilot hole)
1. Drive an M2.5 screw into each post **slowly**, by hand, about 5 mm deep. Use a fresh spot each time.
2. Look closely at the post (a phone flashlight helps) for hairline cracks running down the side.
3. `snug` = threads cut fine, no crack. `press` = very hard to drive but no crack. `cracked` = any visible crack or split.
   This decides between pilot-hole posts and through-hole + nut posts.

## Row W: thin walls (0.4 to 1.2 mm)
1. Look: is each wall fully there, all 6 mm tall, with no holes?
2. Push the top edge sideways gently with a fingernail.
3. `free` = printed whole and stiff, `cracked` = broke with that light push, `failed_to_print` = missing or partial.

## When done
Save `data/fit_test_results.yaml`. The Fit agent reads it with `checks/fit_rules.py`, and measured mode picks
clearances and fastener style from it. If a result looks borderline, test it twice and write that in `notes`.
