r"""Slides -> results/submission/slides.pptx (editable, python-pptx) and slides.pdf (same layout as HTML, printed
with Playwright). One layout spec drives both, so they match.

.venv\Scripts\python.exe scripts\make_slides.py
Every number comes from the run log / result files (see DEMO.md "Numbers to say").
"""
import csv
import html
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "submission"
IMG = OUT / "images"
UI = ROOT / "results" / "ui_audit" / "after"
REPO = "https://github.com/MishraAsh2007/ScienceClaw_Project/tree/Mo_ver"
BLOB = "https://github.com/MishraAsh2007/ScienceClaw_Project/blob/Mo_ver"
W, H = 13.333, 7.5

BG, PANEL, PANEL2, LINE = "141A1C", "1D2528", "182023", "30393C"
INK, MUTED, MINT, CYAN, PURPLE, WARN, BAD = "EDF4F1", "A9B8BB", "BBEFD5", "80C8EC", "C3B1E7", "D5AD83", "E59A7A"
FONT = "Calibri"

job = json.loads((ROOT / "results/print/formlabs/enclosure_a.json").read_text(encoding="utf-8"))
MIN = f"{job['print_time_s'] / 60:.1f}"
ML = f"{job['volume_ml']:.1f}"
rows = [r for r in csv.DictReader((ROOT / "results/benchmark/runs_haiku_v2_per_run.csv").open(encoding="utf-8"))
        if r["brief"] == "a_pi5_standard"]


def mean(mode, key):
    xs = [float(r[key]) for r in rows if r["mode"] == mode]
    return sum(xs) / len(xs)


FL = {m: f"{mean(m, 'all_failures'):g}" for m in ("one_shot", "generalist", "collective")}
COST = {m: f"${mean(m, 'cost_usd'):.3f}" for m in ("one_shot", "generalist", "collective")}
PASSED = {m: f"{sum(r['passed'] == 'True' for r in rows if r['mode'] == m)}/3" for m in ("one_shot", "generalist", "collective")}

# ------------------------------------------------------------------ element helpers (inches, points)


def T(x, y, w, h, text, size=16, color=INK, bold=False, align="l", italic=False):
    return {"k": "text", "x": x, "y": y, "w": w, "h": h, "text": text, "size": size, "color": color, "bold": bold,
            "align": align, "italic": italic}


def B(x, y, w, h, items, size=16, color=INK):
    return {"k": "bullets", "x": x, "y": y, "w": w, "h": h, "items": items, "size": size, "color": color}


def I(path, x, y, w, h, crop=None):  # noqa: E743
    return {"k": "img", "path": str(path), "x": x, "y": y, "w": w, "h": h, "crop": crop}


def CARD(x, y, w, h, fill=PANEL, line=LINE):
    return {"k": "card", "x": x, "y": y, "w": w, "h": h, "fill": fill, "line": line}


def PILL(x, y, w, text, color=MINT, h=0.34, size=11):
    return {"k": "pill", "x": x, "y": y, "w": w, "h": h, "text": text, "color": color, "size": size}


def STAT(x, y, w, value, label, color=INK):
    return [T(x, y, w, 0.8, value, 40, color, True), T(x, y + 0.8, w, 0.5, label, 13, MUTED)]


def slide(title, kicker, elems, notes, banner=None):
    return {"title": title, "kicker": kicker, "elems": elems, "notes": notes, "banner": banner}


def flat(xs):
    out = []
    for x in xs:
        out += x if isinstance(x, list) else [x]
    return out


# ------------------------------------------------------------------ content

S = []
S.append(slide("Agents that design print-ready parts", "SCIENCECLAW · MIT SCIENCECLAW HACKATHON", flat([
    T(0.6, 2.2, 6.4, 1.2, "Four specialist AI agents design a 3D-printed Raspberry Pi 5 case together, with no boss, and "
      "check it in Formlabs PreForm before it is printed.", 20, INK),
    T(0.6, 3.9, 6.0, 0.4, "TEAM", 12, MINT, True),
    B(0.6, 4.3, 6.2, 2.0, ["Mohamed Essadak Rakah (Launch Lead): enclosure agents",
                           "Ashutosh Mishra", "Quang Tuong Van Nguyen", "Jash Kotadiya: brackets, CalculiX simulation"], 16),
    I(IMG / "render_assembled.png", 7.0, 1.3, 5.9, 5.4),
]), "Hi, we are team ScienceClaw. Our project: AI agents that design parts which are ready to print, and check them "
    "before printing. My part, which I'll show, is a Raspberry Pi 5 case designed by four specialist agents. My "
    "teammates built the same idea for load-bearing brackets with a finite-element simulation."))

S.append(slide("A part that looks right in CAD often doesn't fit or print", "THE PROBLEM", flat([
    CARD(0.6, 1.6, 5.6, 5.2),
    STAT(0.95, 1.85, 5.0, "20", "check failures in the naive starting design (same start for every mode)", BAD),
    B(0.95, 3.25, 5.0, 3.4, ["The board can't drop in: connectors hit the walls",
                             "The lid sits on the USB stack; no vent over the fan",
                             "Board screws can't grip: posts too short",
                             "Resin-trapping cups only show up in the slicer",
                             "Clearances depend on the printer and resin"], 15),
    I(IMG / "render_first_vs_final.png", 6.5, 1.6, 6.3, 2.75, crop=(0, 0, 0.5, 1)),
    T(6.5, 4.45, 6.3, 0.9, "The naive design (v1): closed box with port cutouts, low lid, short standoffs, no tabs, "
      "vents or cable slots.", 13, MUTED),
    T(6.5, 5.45, 6.3, 1.2, "One AI call can draw a box. Making it fit a real board, print without defects and assemble "
      "needs several kinds of expertise and real tools.", 16, INK, True),
]), "Here is the starting design every mode gets: a closed box with port cutouts. Our checks find 20 problems: the "
    "board can't be lowered in, the lid sits on the USB ports, the screws have nothing to bite into. Some problems "
    "only appear in the printer software, like resin cups. And the right clearance depends on the printer and resin. "
    "That's several kinds of expertise, each with its own tool."))

AG = [("Design", "name, board kit", "CadQuery generator", WARN),
      ("Assembly", "box, ports, slots, tabs, lid screws", "assembly check: drop-in, lid gap, plugs, screws", CYAN),
      ("Manufacturing", "walls, ribs, vents, drain holes", "printability, Formlabs PreForm, orientation tool", BAD),
      ("Fit", "clearances, standoffs, fasteners", "fit rules: design guide or measured test plate", MINT)]
el = []
for i, (n, owns, tool, col) in enumerate(AG):
    x = 0.6 + i * 3.1
    el += [CARD(x, 1.7, 2.9, 3.0), T(x + 0.25, 1.9, 2.5, 0.5, n, 20, col, True),
           T(x + 0.25, 2.5, 2.5, 0.3, "OWNS", 11, MUTED, True), T(x + 0.25, 2.8, 2.5, 0.8, owns, 14),
           T(x + 0.25, 3.6, 2.5, 0.3, "TOOL", 11, MUTED, True), T(x + 0.25, 3.9, 2.5, 0.8, tool, 14)]
el += [CARD(0.6, 5.0, 12.1, 1.75, PANEL2),
       B(0.9, 5.15, 11.6, 1.5, ["No boss: each agent can only change its own keys (enforced in code)",
                                "A fix in someone else's part becomes a need to that owner; an edit is kept only if no check gets worse",
                                "Objections must carry numbers; the protocol (plain code) decides, not a model"], 15)]
S.append(slide("Specialist agents, each owning a real tool, no boss", "THE IDEA", el,
               "Our idea: split the job like a real engineering team. Four agents, each owns part of the design spec "
               "and one real tool. Assembly runs a geometric assembly check, Manufacturing runs Formlabs PreForm, Fit "
               "owns clearances. Nobody is in charge: if a fix lies in someone else's part, you post a need to them. "
               "And plain code, not a model, decides whether an edit is kept: only if no check gets worse."))

S.append(slide("How it works", "SYSTEM", [I(IMG / "system_diagram.png", 0.6, 1.35, 12.1, 5.9)],
               "Everything goes through a shared board in SQLite: proposals, needs, objections, the per-failure "
               "breakdown of every edit, and the protocol's decision. The output is a CadQuery model, exported to STL "
               "and to a PreForm .form file for the Form Studio 1 with Alpha Fast resin at 0.15 mm, plus FDM STL and STEP."))

ex = [("1 · ASSEMBLY'S CHECK (v1)", "board_screw_1: no stock length gives 4.0 mm engagement within 3.5 mm of post", BAD),
      ("2 · NEED: ASSEMBLY → FIT", "the fix is in Fit's keys, so a need is posted: suggest standoffs/height 2.9", PURPLE),
      ("3 · FIT'S FIX (v3)", "raise standoffs 1.5 → 2.9 mm: problems 13 → 5, fixed 8, broke 0. Accepted: no check got worse", MINT),
      ("4 · COUNTER-ARGUMENT WITH NUMBERS (v5)", "Fit declines a later need to lower them to 2.0 mm: it \"would bring back the "
       "0.3 mm base overlaps with microsd_socket and both cooler pin tips\"", WARN)]
el = []
for i, (h, t, col) in enumerate(ex):
    y = 1.55 + i * 1.32
    el += [CARD(0.6, y, 8.1, 1.15), T(0.85, y + 0.12, 7.7, 0.35, h, 12, col, True), T(0.85, y + 0.45, 7.7, 0.7, t, 15)]
el += [I(ROOT / "results/demo_run/a_pi5_standard__collective__generic__s0__claude-sonnet-5-5/versions/v3/snapshot.png",
         9.0, 1.55, 3.8, 2.9),
       T(9.0, 4.55, 3.8, 1.6, "Quoted from the run log of the print candidate (Sonnet, 4 agents). This run had no "
         "objections; the decline is how Fit argued back.", 12, MUTED)]
S.append(slide("A real exchange from the log", "HOW THE AGENTS WORK TOGETHER", el,
               "This is a real exchange, quoted from the log. Assembly's check says the board screws can't grip. The "
               "fix is the standoff height, which Fit owns, so Assembly posts a need. Fit raises the standoffs from 1.5 "
               "to 2.9 mm: problems go from 13 to 5, nothing breaks, so the protocol keeps it. Later another need asks "
               "to lower them again, and Fit declines with numbers: that would bring back the overlaps it just fixed. "
               "To be accurate: this run had no formal objections; the decline is the pushback."))


def result_slide(printed):
    el = [I(IMG / "render_exploded.png", 0.5, 1.4, 5.6, 3.75)]
    el += flat([STAT(6.4, 1.5, 2.0, "0", "PreForm warnings (print job)", MINT), STAT(8.5, 1.5, 2.0, MIN, "min print time"),
                STAT(10.6, 1.5, 2.2, ML, "ml resin incl. supports")])
    el += [CARD(6.4, 3.0, 6.3, 2.15, PANEL2),
           T(6.65, 3.1, 5.9, 0.35, "THE CUP STORY (REAL PREFORM 3.64.2)", 12, CYAN, True),
           B(6.65, 3.45, 5.9, 1.7, ["With PreForm's own auto-orient the final design had 1 resin-trapping cup in the base",
                                    "Manufacturing's orientation tool compared 6 orientations in PreForm; flat won for both parts",
                                    f"Result: 0 warnings, both parts on one platform, {MIN} min, {ML} ml"], 13)]
    if printed:
        el += [CARD(0.5, 5.35, 12.2, 1.55, PANEL), T(0.75, 5.45, 3.0, 0.35, "IF PRINTED: FILL IN", 12, WARN, True),
               T(0.75, 5.8, 8.0, 1.0, "[Photo of the printed case with the Pi inside here]  [Checklist from results/print/"
                 "ASSEMBLY.md: board drops in __ · screws grip __/4 · lid closes __ · ports fit __/7 · fan clears __]",
                 14, INK),
               T(9.0, 5.5, 3.5, 1.3, "Say only what you tested.", 14, WARN, True)]
    else:
        el += [CARD(0.5, 5.35, 12.2, 1.55, PANEL), T(0.75, 5.45, 6.0, 0.35, "NOT PRINTED YET: FIT CHECKED IN GEOMETRY",
                                                     12, PURPLE, True),
               B(0.75, 5.8, 11.8, 1.1, ["Onshape: base + lid + official Pi 5 STEP in one frame; Boolean intersect = empty means no overlap",
                                        "Lid 4.4 mm over the fan keep-out, 0.8 mm over the USB stack; top risk: wide USB-A plug, "
                                        "upper pair, 0.07 mm (use the lower pair)"], 13)]
    return slide("Print-ready result: 0 PreForm warnings", "RESULT · " + ("IF PRINTED" if printed else "IF NOT PRINTED"), el,
                 ("IF PRINTED version. " if printed else "IF NOT PRINTED version. ")
                 + "The final design: local check failures 20 to 0. In real PreForm, with PreForm's own auto-orient, there "
                   "was still one resin-trapping cup in the base, so the run itself ends as 'not all passing'. Then "
                   "Manufacturing's orientation tool compared six orientations in PreForm and picked flat for both parts: "
                   f"zero warnings, one platform, {MIN} minutes, {ML} ml. "
                 + ("Then show the print and say only what you checked on the checklist."
                    if printed else "We have not printed it yet. The fit is checked in geometry with the official Pi 5 "
                                    "model in Onshape; the one real risk is a wide USB-A plug in the upper pair."),
                 banner=("IF PRINTED: keep this slide and DELETE the next one" if printed
                         else "IF NOT PRINTED: keep this slide and DELETE the previous one"))


S.append(result_slide(True))
S.append(result_slide(False))

S.append(slide("Benchmark: the team leaves the fewest failures, and no run passes", "BENCHMARK · HAIKU · 3 SEEDS PER MODE", flat([
    I(IMG / "chart_benchmark_failures.png", 0.5, 1.35, 8.0, 4.5),
    CARD(8.8, 1.45, 4.0, 5.3),
    T(9.05, 1.6, 3.6, 0.35, "MEAN FAILURES LEFT", 12, MINT, True),
    T(9.05, 2.0, 3.6, 1.4, f"Team of 4: {FL['collective']}\nOne generalist: {FL['generalist']}\nOne shot: {FL['one_shot']}", 18, INK, True),
    B(9.05, 3.5, 3.6, 3.2, [f"Passed every check: {PASSED['collective']}, {PASSED['generalist']}, {PASSED['one_shot']}",
                            f"Cost per run: team {COST['collective']}, generalist {COST['generalist']}, one shot {COST['one_shot']}",
                            "Same model, tools, naive start, budget 12",
                            "Sonnet set incomplete (4/18 runs): no Sonnet claims"], 13),
    T(0.6, 6.0, 8.0, 0.9, "Only the Haiku set for brief a is complete. Failures include the real final PreForm warnings.",
      12, MUTED),
]), f"A fair comparison: same model, Claude Haiku, same tools, same naive start, same budget, three seeds each. "
    f"The one-shot model leaves {FL['one_shot']} failures, one generalist agent {FL['generalist']}, the team "
    f"{FL['collective']} on average. Honestly: none of the nine runs passed every check, the seeds vary a lot, and the "
    f"team costs more per run, {COST['collective']} vs {COST['generalist']}. We have only 4 of 18 Sonnet runs, so we make "
    "no claims about Sonnet."))

S.append(slide("Trust: every claim traces back to the log", "WHY THIS DESIGN", [
    I(UI / "06_why.png", 0.5, 1.35, 6.2, 3.9, crop=(0.2, 0.07, 1.0, 0.8)),
    I(IMG / "chart_candidate_failures.png", 6.9, 1.35, 5.9, 3.32),
    B(0.6, 5.45, 12.1, 1.5, ["\"Why this design\" is built from the board log: failed check → need → proposal → breakdown → decision",
                             "The AI summary may not write numbers: placeholders are filled from the log, any digit it writes is removed and flagged",
                             "Charts are plain code over the logs; MOCK, UNVERIFIED and EXPERIMENTAL labels stay on everything"], 14),
], "Can you trust it? The Why-this-design view is built only from the log. The AI summary is not allowed to write "
   "numbers; code fills them in, and if the model writes a digit, it's removed and flagged. Every chart is code over "
   "the logs, and anything simulated or unmeasured stays labelled MOCK or UNVERIFIED."))

S.append(slide("Why it matters for Formlabs", "FORMLABS", flat([
    I(UI / "07_print.png", 6.6, 1.35, 6.2, 3.9, crop=(0.2, 0.07, 1.0, 0.75)),
    B(0.6, 1.5, 5.8, 5.2, ["PreForm in the loop: the Manufacturing agent calls the PreForm Local API (PreFormServer) "
                           "and acts on real warnings (cups, supports)",
                           f"Orientation tool picks the print orientation: 0 warnings, {MIN} min, {ML} ml on one Form Studio 1 platform",
                           "Output is a .form file that prints as is (Alpha Fast V1, 0.15 mm)",
                           "Fit agent: clearances measured on a printed test plate for one printer + resin, not generic rules",
                           "Same design also as SLA STL, FDM STL (not PreForm-checked) and STEP"], 15),
    T(6.6, 5.4, 6.2, 1.3, "Every PreForm number on this slide is from results/print/formlabs/enclosure_a.json (real PreForm).",
      12, MUTED),
]), "Why Formlabs should care: PreForm is not the last step, it's a tool an agent uses during design. The "
    "Manufacturing agent reads real PreForm warnings and changes the design or the orientation. The output is a "
    ".form file you can print as is. And the Fit agent is built to use clearances measured on your own printer and "
    "resin, from a printed tolerance plate."))

S.append(slide("[Brackets + CalculiX simulation]", "TEAMMATES: REPLACE THIS SLIDE", [
    CARD(0.6, 1.5, 12.1, 5.3, PANEL2),
    B(0.9, 1.7, 11.6, 5.0, ["[Part designer: describe a bracket in words → parametric CAD → 3D finite elements (Gmsh + CalculiX)]",
                            "[Requirements: design load, max bend, least resin; result numbers from your logs]",
                            "[Virtual test rig vs measured bend: what was validated, what is simulated]",
                            "[Picture: stress / bend view from the Design Lab]",
                            "[Keep labels honest: simulated_pass vs physical_pass]"], 18, MUTED)],
    "TEAMMATES: replace this slide with the bracket part family and the CalculiX simulation. Use numbers from your logs only.",
    banner="TEAMMATES: replace this"))

S.append(slide("[Multi-provider agents]", "TEAMMATES: REPLACE THIS SLIDE", [
    CARD(0.6, 1.5, 12.1, 5.3, PANEL2),
    B(0.9, 1.7, 11.6, 5.0, ["[Mechanics / Fabrication / Measurement specialists on different providers (OpenAI, Claude, Gemini, NVIDIA Nemotron)]",
                            "[What a live round showed: only if it was actually run and logged]",
                            "[Onshape / Fusion / FreeCAD link and PreForm checks on your side]",
                            "[NVIDIA prize: only if the live Part Designer test really used Nemotron]"], 18, MUTED)],
    "TEAMMATES: replace this slide with the multi-provider agents. Only claim a live three-provider round or Nemotron "
    "use if it was logged.", banner="TEAMMATES: replace this"))

S.append(slide("Limitations and next steps", "HONEST STATUS", [
    CARD(0.6, 1.5, 5.9, 5.3), T(0.85, 1.65, 5.5, 0.4, "NOT YET VALIDATED", 13, BAD, True),
    B(0.85, 2.1, 5.5, 4.6, ["Several sizes are UNVERIFIED (plug sizes, cooler footprint and height): ruler checks in RISK_CHECK.md",
                            "Measured clearances need the tolerance test print (test plate not printed yet: guide values used)",
                            "FDM version passes our checks but was not checked by PreForm",
                            "Sonnet benchmark incomplete (4/18); no run passed every check, including real PreForm"], 14),
    CARD(6.8, 1.5, 5.9, 5.3), T(7.05, 1.65, 5.5, 0.4, "NEXT", 13, MINT, True),
    B(7.05, 2.1, 5.5, 4.6, ["Print the tolerance plate, fill in the fit test, rerun with measured clearances",
                            "Let the orientation tool run inside the loop so runs end with 0 PreForm warnings",
                            "Finish the benchmark (both briefs, Sonnet), compare cost per fixed failure",
                            "More part families behind the same interface (brackets from the team, free-form EXPERIMENTAL)"], 14),
], "What we have not proven yet: several part sizes come from drawings or typical plugs, not measurements. The measured "
   "clearances need the tolerance plate to be printed. The FDM version was not checked in PreForm. And the benchmark "
   "is incomplete for Sonnet. Next we print the plate, rerun with measured clearances, and finish the benchmark."))

S.append(slide("Links", "TRY IT", flat([
    I(IMG / "thumbnail_1200x800.png", 7.6, 1.5, 5.2, 3.5),
    T(0.6, 1.6, 6.5, 0.4, "CODE (BRANCH Mo_ver: ENCLOSURE AGENTS)", 12, MINT, True),
    T(0.6, 2.0, 6.6, 0.8, REPO, 13),
    T(0.6, 2.9, 6.5, 0.4, "DEMO VIDEO (2:50)", 12, MINT, True),
    T(0.6, 3.3, 6.6, 0.8, f"{BLOB}/results/submission/demo.mp4", 12),
    T(0.6, 4.2, 6.5, 0.4, "RUN IT WITHOUT API KEYS", 12, MINT, True),
    T(0.6, 4.6, 6.6, 1.4, "pip install -r requirements.txt\nstreamlit run app.py   (Mock mode on by default)", 15),
    T(0.6, 6.2, 12, 0.5, "[Teammates: add the Jash_ver / main links here]", 13, MUTED),
]), "Everything is on GitHub, branch Mo_ver, including this deck, the demo video and the print files. You can run "
    "the app without any API key: Mock mode is on by default. Thank you."))


# ------------------------------------------------------------------ PPTX


def _fit(path, w, h, crop):
    im = Image.open(path)
    iw, ih = im.size
    if crop:
        iw, ih = iw * (crop[2] - crop[0]), ih * (crop[3] - crop[1])
    s = min(w / iw, h / ih)
    return iw * s, ih * s


def build_pptx(path):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.oxml.ns import qn
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W), Inches(H)
    blank = prs.slide_layouts[6]
    rgb = lambda h: RGBColor.from_string(h)  # noqa: E731
    AL = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}

    def textbox(sl, x, y, w, h, paras, size, color, bold=False, align="l", italic=False, bullets=False):
        tb = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = MSO_ANCHOR.TOP
        for i, line in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = AL[align]
            r = p.add_run()
            r.text = line
            f = r.font
            f.size, f.bold, f.italic, f.name = Pt(size), bold, italic, FONT
            f.color.rgb = rgb(color)
            if bullets:
                p.space_after = Pt(size * 0.45)
                pPr = p._p.get_or_add_pPr()
                pPr.set("marL", str(int(Inches(0.28))))
                pPr.set("indent", str(-int(Inches(0.22))))
                bc = pPr.makeelement(qn("a:buClr"), {})
                sc = bc.makeelement(qn("a:srgbClr"), {"val": MINT})
                bc.append(sc)
                pPr.append(bc)
                pPr.append(pPr.makeelement(qn("a:buChar"), {"char": "•"}))
        return tb

    def rrect(sl, x, y, w, h, fill, line, text=None, size=11, color=INK):
        s = sl.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        s.adjustments[0] = min(0.5, 0.12 / max(0.1, min(w, h)) * 1.6) if text else 0.06
        s.fill.solid()
        s.fill.fore_color.rgb = rgb(fill)
        s.line.color.rgb = rgb(line)
        s.line.width = Pt(1)
        s.shadow.inherit = False
        tf = s.text_frame
        if text:
            tf.margin_left = tf.margin_right = Inches(0.08)
            tf.margin_top = tf.margin_bottom = 0
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            r = p.add_run()
            r.text = text
            r.font.size, r.font.bold, r.font.name = Pt(size), True, FONT
            r.font.color.rgb = rgb(color)
        return s

    for n, sd in enumerate(S, 1):
        sl = prs.slides.add_slide(blank)
        bgf = sl.background.fill
        bgf.solid()
        bgf.fore_color.rgb = rgb(BG)
        textbox(sl, 0.6, 0.38, 10.5, 0.35, [sd["kicker"]], 12, MINT, True)
        textbox(sl, 0.6, 0.68, 12.1, 0.7, [sd["title"]], 30, INK, True)
        if sd["banner"]:
            rrect(sl, 8.4, 0.3, 4.35, 0.4, "3A2A16", WARN, sd["banner"], 11, WARN)
        textbox(sl, 12.2, 7.05, 0.6, 0.3, [str(n)], 10, MUTED, align="r")
        for e in sd["elems"]:
            k = e["k"]
            if k == "text":
                textbox(sl, e["x"], e["y"], e["w"], e["h"], e["text"].split("\n"), e["size"], e["color"], e["bold"],
                        e["align"], e["italic"])
            elif k == "bullets":
                textbox(sl, e["x"], e["y"], e["w"], e["h"], e["items"], e["size"], e["color"], bullets=True)
            elif k == "card":
                rrect(sl, e["x"], e["y"], e["w"], e["h"], e["fill"], e["line"])
            elif k == "pill":
                rrect(sl, e["x"], e["y"], e["w"], e["h"], BG, e["color"], e["text"], e["size"], e["color"])
            elif k == "img":
                fw, fh = _fit(e["path"], e["w"], e["h"], e["crop"])
                x, y = e["x"] + (e["w"] - fw) / 2, e["y"] + (e["h"] - fh) / 2
                pic = sl.shapes.add_picture(e["path"], Inches(x), Inches(y), Inches(fw), Inches(fh))
                if e["crop"]:
                    l, t, r, b = e["crop"]
                    pic.crop_left, pic.crop_top, pic.crop_right, pic.crop_bottom = l, t, 1 - r, 1 - b
                pic.name = Path(e["path"]).stem
        sl.notes_slide.notes_text_frame.text = sd["notes"]
    prs.save(path)


# ------------------------------------------------------------------ HTML -> PDF


def build_html(path):
    import base64

    def data_uri(p):
        return "data:image/png;base64," + base64.b64encode(Path(p).read_bytes()).decode()
    css = f"""@page{{size:{W}in {H}in;margin:0}}*{{box-sizing:border-box}}
body{{margin:0;font-family:Calibri,Carlito,'Segoe UI',sans-serif}}
.s{{position:relative;width:{W}in;height:{H}in;background:#{BG};overflow:hidden;page-break-after:always}}
.e{{position:absolute;white-space:pre-wrap;line-height:1.18;margin:0}}
ul.e{{padding:0;list-style:none}}ul.e li{{position:relative;padding-left:0.28in}}
ul.e li:before{{content:'•';position:absolute;left:0.06in;color:#{MINT}}}
.card{{position:absolute;border-radius:0.12in}}
.pill{{position:absolute;border-radius:0.2in;display:flex;align-items:center;justify-content:center;font-weight:bold;text-align:center}}
.imgbox{{position:absolute;overflow:hidden}}"""
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>ScienceClaw slides</title><style>{css}</style></head><body>"]

    def text(x, y, w, h, s, size, color, bold=False, align="l", italic=False):
        return (f"<p class='e' style='left:{x}in;top:{y}in;width:{w}in;height:{h}in;font-size:{size}pt;color:#{color};"
                f"font-weight:{'bold' if bold else 'normal'};text-align:{ {'l': 'left', 'c': 'center', 'r': 'right'}[align]};"
                f"font-style:{'italic' if italic else 'normal'}'>{html.escape(s)}</p>")
    for n, sd in enumerate(S, 1):
        out.append("<div class='s'>")
        out.append(text(0.6, 0.38, 10.5, 0.35, sd["kicker"], 12, MINT, True))
        out.append(text(0.6, 0.68, 12.1, 0.7, sd["title"], 30, INK, True))
        if sd["banner"]:
            out.append(f"<div class='pill' style='left:8.4in;top:0.3in;width:4.35in;height:0.4in;background:#3A2A16;"
                       f"border:1px solid #{WARN};color:#{WARN};font-size:11pt'>{html.escape(sd['banner'])}</div>")
        out.append(text(12.2, 7.05, 0.6, 0.3, str(n), 10, MUTED, align="r"))
        for e in sd["elems"]:
            k = e["k"]
            if k == "text":
                out.append(text(e["x"], e["y"], e["w"], e["h"], e["text"], e["size"], e["color"], e["bold"], e["align"],
                                 e["italic"]))
            elif k == "bullets":
                lis = "".join(f"<li style='margin-bottom:{e['size'] * 0.45}pt'>{html.escape(i)}</li>" for i in e["items"])
                out.append(f"<ul class='e' style='left:{e['x']}in;top:{e['y']}in;width:{e['w']}in;font-size:{e['size']}pt;"
                           f"color:#{e['color']}'>{lis}</ul>")
            elif k == "card":
                out.append(f"<div class='card' style='left:{e['x']}in;top:{e['y']}in;width:{e['w']}in;height:{e['h']}in;"
                           f"background:#{e['fill']};border:1px solid #{e['line']}'></div>")
            elif k == "pill":
                out.append(f"<div class='pill' style='left:{e['x']}in;top:{e['y']}in;width:{e['w']}in;height:{e['h']}in;"
                           f"border:1px solid #{e['color']};color:#{e['color']};font-size:{e['size']}pt'>"
                           f"{html.escape(e['text'])}</div>")
            elif k == "img":
                fw, fh = _fit(e["path"], e["w"], e["h"], e["crop"])
                x, y = e["x"] + (e["w"] - fw) / 2, e["y"] + (e["h"] - fh) / 2
                c = e["crop"] or (0, 0, 1, 1)
                iw, ih = fw / (c[2] - c[0]), fh / (c[3] - c[1])
                out.append(f"<div class='imgbox' style='left:{x}in;top:{y}in;width:{fw}in;height:{fh}in'>"
                           f"<img src='{data_uri(e['path'])}' style='position:absolute;left:{-c[0] * iw}in;"
                           f"top:{-c[1] * ih}in;width:{iw}in;height:{ih}in'></div>")
        out.append("</div>")
    out.append("</body></html>")
    Path(path).write_text("".join(out), encoding="utf-8")


def build_pdf(html_path, pdf_path, png_dir=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": int(W * 96), "height": int(H * 96)})
        pg.goto(Path(html_path).resolve().as_uri())
        pg.wait_for_timeout(800)
        pg.pdf(path=str(pdf_path), width=f"{W}in", height=f"{H}in", print_background=True)
        if png_dir:
            Path(png_dir).mkdir(parents=True, exist_ok=True)
            for i, el in enumerate(pg.locator(".s").all(), 1):
                el.screenshot(path=str(Path(png_dir) / f"slide-{i:02d}.png"))
        br.close()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    build_pptx(OUT / "slides.pptx")
    tmp = ROOT / "results" / "tmp" / "slides"
    tmp.mkdir(parents=True, exist_ok=True)
    build_html(tmp / "slides.html")
    build_pdf(tmp / "slides.html", OUT / "slides.pdf", sys.argv[1] if len(sys.argv) > 1 else None)
    print("slides:", len(S), "pptx MB", round((OUT / "slides.pptx").stat().st_size / 1e6, 1),
          "pdf MB", round((OUT / "slides.pdf").stat().st_size / 1e6, 1))
