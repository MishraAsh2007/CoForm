"""Does the design pipeline generalise beyond the cases it was tuned on? Two measurements.

1. Check of the checks (no AI): take designs known to work, break each in a way that must stop it working
   (the part pushed into or pulled off its mount, the payload lifted off or pushed into its support, the
   piece that retains it removed, screw holes filled), plus harmless changes that must not be flagged.
   Reports how many breakages the functional checks catch and how many harmless changes they reject.
2. Held-out live sample: requests nobody tuned the prompts or components on, run end to end with the
   configured models. Reports how many reach a working draft and a passing part, with the evidence where
   they stopped. Do not tune on HELD_OUT; when something gets fixed, add new requests instead.

    python -m coform.cli evaluate checks --output data/evaluations/checks
    python -m coform.cli evaluate live --count 5 --output data/evaluations/live
"""
from __future__ import annotations

import json
import re
import tempfile
import time
from copy import deepcopy
from pathlib import Path

from . import freeform as ff

TEMPLATES = Path(__file__).with_name("templates")
DESK = {"name": "desk", "role": "mount", "shape": "box", "at": [-500, -200, "-desk_thick"], "size": [500, 400, "desk_thick"],
        "touch": ["+z", "-z"], "insert": "+x"}
WALL = {"name": "wall", "role": "mount", "shape": "box", "at": [-100, -200, -100], "size": [100, 400, 300], "touch": ["+x"], "insert": "+x"}


def _template(name: str) -> dict:
    return json.loads((TEMPLATES / name).read_text(encoding="utf-8"))


def _screw_clamp_holder() -> dict:
    """Hand-built (no components): screw clamp on the desk edge, cup beside it."""
    low = "-desk_thick - gap - jaw_t"
    return {"name": "Hand-built clamp holder", "parameters": {
                "desk_thick": {"value": 25, "role": "fixed"}, "bottle_d": {"value": 75, "role": "fixed"}, "gap": {"value": 4, "role": "fixed"},
                "jaw_t": {"value": 5, "min": 3, "max": 8, "role": "thickness"}, "spine_t": {"value": 6, "min": 4, "max": 10, "role": "thickness"},
                "floor_t": {"value": 3, "min": 2, "max": 6, "role": "thickness"}, "wall_t": {"value": 3, "min": 2, "max": 6, "role": "thickness"}},
            "features": [
                {"label": "upper jaw", "shape": "box", "at": [-40, -20, 0], "size": ["40 + spine_t", 40, "jaw_t"]},
                {"label": "spine", "shape": "box", "at": [0, -20, "-60 - floor_t"], "size": ["spine_t", 40, "60 + floor_t + jaw_t"]},
                {"label": "lower jaw", "shape": "box", "at": [-40, -20, low], "size": ["40 + spine_t", 40, "jaw_t"]},
                {"label": "cup floor", "shape": "cylinder", "at": [48, 0, "-60 - floor_t"], "radius": "bottle_d / 2 + 1 + wall_t", "length": "floor_t"},
                {"label": "cup wall", "shape": "cylinder", "at": [48, 0, -60], "radius": "bottle_d / 2 + 1 + wall_t", "length": 40},
                {"label": "web", "shape": "box", "at": [0, -10, "-60 - floor_t"], "size": ["48 - bottle_d / 2 - 1", 20, "40 + floor_t"]},
                {"label": "cup void", "op": "subtract", "shape": "cylinder", "at": [48, 0, -60], "radius": "bottle_d / 2 + 1", "length": 41},
                {"label": "drain", "op": "subtract", "shape": "cylinder", "at": [48, 0, "-61 - floor_t"], "radius": 4, "length": "floor_t + 2"},
                {"label": "screw hole", "op": "subtract", "shape": "cylinder", "at": [-20, 0, f"{low} - 1"], "radius": 3, "length": "jaw_t + 2"}],
            "environment": [deepcopy(DESK),
                            {"name": "bottle", "role": "payload", "shape": "cylinder", "at": [48, 0, -60], "radius": "bottle_d / 2", "length": 200,
                             "touch": ["-z"], "force_n": [0, 0, -15], "insert": "-z", "retain": ["+x", "-x", "+y", "-y"]},
                            {"name": "screw", "role": "hardware", "shape": "cylinder", "at": [-20, 0, "-desk_thick - 40"], "radius": 3, "length": 40,
                             "insert": "+z", "retain": ["+x", "-x", "+y", "-y"], "bears_on": "desk"}],
            "requirements": {"max_displacement_mm": 2.0, "safety_factor": 2}}


def _component_part(name, world, components, requirements=None) -> dict:
    fixed = {"desk_thick": {"value": 25, "role": "fixed"}, "strap_w": {"value": 25, "role": "fixed"}, "tube_r": {"value": 15, "role": "fixed"}}
    return {"name": name, "parameters": fixed, "environment": world, "components": components, "features": [],
            "requirements": requirements or {"max_displacement_mm": 3.0, "safety_factor": 2}}


def known_good() -> dict[str, dict]:
    """Designs that pass every functional check: the bases the breakages start from."""
    strap = lambda z: {"name": "strap", "role": "payload", "shape": "box", "at": [15, -12.5, z], "size": [30, "strap_w", 10],  # noqa: E731
                       "touch": ["-z"], "force_n": [0, 0, -98.1], "insert": "-z", "retain": ["+x"]}
    return {
        "shelf bracket (template)": _template("shelf_bracket.json"),
        "desk bottle holder (worked example)": _template("desk_bottle_holder.json"),
        "hand-built screw-clamp holder": _screw_clamp_holder(),
        "wall backpack hook": _component_part("Wall backpack hook", [deepcopy(WALL), strap(70)], [
            {"type": "wall_plate", "name": "plate", "mounts": "wall", "face": "+x", "center": [0, 60], "width": 40, "height": 80},
            {"type": "hook", "name": "hook", "holds": "strap", "direction": "+x", "start": 1}]),
        "desk backpack hook": _component_part("Desk backpack hook", [deepcopy(DESK), strap(-80)], [
            {"type": "desk_clamp", "name": "clamp", "grips": "desk", "edge": "+x", "along": 0, "drop": 56},
            {"type": "hook", "name": "hook", "holds": "strap", "direction": "+x", "start": 1}], {"max_displacement_mm": 5.0, "safety_factor": 2}),
        "wall tube cradle": _component_part("Wall tube cradle", [deepcopy(WALL), {
            "name": "tube", "role": "payload", "shape": "cylinder", "at": [21, -40, 50], "axis": "y", "radius": "tube_r", "length": 80,
            "touch": ["-z"], "force_n": [0, 0, -10], "insert": "-z", "retain": ["+x", "-x"]}], [
            {"type": "wall_plate", "name": "plate", "mounts": "wall", "face": "+x", "center": [0, 50], "width": 40, "height": 80},
            {"type": "cradle", "name": "saddle", "holds": "tube"}]),
    }


# ---- breakages with a known answer ------------------------------------------------------------------------
def _shift(expr, d):
    return f"({expr}) + ({d:g})"


def _move_design(raw: dict, axis: int, d: float) -> dict:
    """Translate the part and what travels with it (payloads, screws), not the desk or wall."""
    out = deepcopy(raw)
    for f in out["features"]:
        f["at"][axis] = _shift(f["at"][axis], d)
    for b in out["environment"]:
        if b["role"] in ("payload", "hardware"):
            b["at"][axis] = _shift(b["at"][axis], d)
    return out


def _face(direction: str) -> tuple[int, int]:
    return "xyz".index(direction[1]), 1 if direction[0] == "+" else -1


def breakages(raw: dict, evidence: list[dict]) -> list[tuple[str, bool, dict]]:
    """(name, should_still_work, spec) for one known-good design, already expanded to plain features."""
    out = []
    mount = next(b for b in raw["environment"] if b["role"] == "mount")
    k, s = _face(mount["touch"][0])
    out.append(("part pushed 5 mm into its mount", False, _move_design(raw, k, -5 * s)))
    out.append(("part pulled 3 mm off its mount", False, _move_design(raw, k, 3 * s)))
    for i, b in enumerate(raw["environment"]):
        if b["role"] != "payload":
            continue
        k, s = _face(b["touch"][0])
        for label, d in (("lifted 3 mm off its support", -3 * s), ("pushed 3 mm into its support", 3 * s)):
            moved = deepcopy(raw)
            moved["environment"][i]["at"][k] = _shift(moved["environment"][i]["at"][k], d)
            out.append((f"{b['name']} {label}", False, moved))
        held = next((e.get("retained_by") or {} for e in evidence if e["name"] == b["name"]), {})
        for direction, by in held.items():
            labels = set(re.findall(r"'([^']+)'", by or ""))
            if labels:
                cut = deepcopy(raw)
                cut["features"] = [f for f in cut["features"] if f.get("label") not in labels or f.get("op") == "subtract"]
                out.append((f"what holds {b['name']} on {direction} removed ({', '.join(sorted(labels))})", False, cut))
                break
    hardware = [b for b in raw["environment"] if b["role"] == "hardware"]
    if hardware:
        spec = ff.DesignSpec.model_validate(raw)
        values = ff.values_of(spec)
        from .scene import resolve
        boxes = [(s.lo, s.hi) for s in resolve(spec, values) if s.body.role == "hardware"]
        keep = []
        for f, model in zip(raw["features"], spec.features):
            lo, hi = ff.feature_bounds(model, values)
            if model.op == "subtract" and any(all(lo[m] < bh[m] and hi[m] > bl[m] for m in range(3)) for bl, bh in boxes):
                continue
            keep.append(f)
        out.append(("screw holes filled in", False, {**deepcopy(raw), "features": keep}))
    for label, step in (("every wall 1 mm thicker", 1.0), ("every wall 0.5 mm thinner", -0.5)):
        harmless = deepcopy(raw)
        for p in harmless["parameters"].values():
            if p.get("role") == "thickness":
                p["value"] = min(p["max"], max(p["min"], p["value"] + step))
        out.append((label, True, harmless))
    # Deliberate deletion cases test physical function, not dangling relationship references.
    # Ordinary repairs cannot remove these constraints; this is only the mutation benchmark.
    for _, should_work, variant in out:
        if not should_work:
            labels = {f.get("label") for f in variant["features"]}
            variant["relationships"] = [r for r in variant.get("relationships", [])
                                        if r["left_feature"] in labels and r["right_feature"] in labels]
    return out


def _verdict(raw: dict, folder: Path) -> tuple[bool, str]:
    """(works, why): works means every functional check passed and strength was computed."""
    try:
        spec = ff.DesignSpec.model_validate(raw)
        result = ff.analyze(spec, ff.values_of(spec), folder)
    except ff.BuildError as exc:
        return False, str(exc)
    except ValueError as exc:
        return False, f"invalid: {exc}"
    failed = [c["name"] for c in result["checks"] if not c["passed"] and c["name"] in ("fit", "contact", "insertion", "retention", "function")]
    return not failed, ("passes its functional checks" if not failed else f"failed {failed}")


def check_the_checks(output: str | Path) -> dict:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows, started = [], time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="coform_eval_"))
    for name, base in known_good().items():
        raw = ff.DesignSpec.model_validate(base).model_dump(mode="json")  # components expanded to plain features
        try:
            baseline = ff.analyze(ff.DesignSpec.model_validate(raw), ff.values_of(ff.DesignSpec.model_validate(raw)), work / f"{len(rows)}")
        except ff.BuildError as exc:
            rows.append({"design": name, "case": "baseline", "expected_to_work": True, "works": False, "why": str(exc)[:600]})
            continue
        rows.append({"design": name, "case": "baseline", "expected_to_work": True, "works": True, "why": "passes"})
        for case, should_work, broken in breakages(raw, baseline["scene"]):
            works, why = _verdict(broken, work / f"{len(rows)}")
            rows.append({"design": name, "case": case, "expected_to_work": should_work, "works": works, "why": why[:600]})
    breaks = [r for r in rows if not r["expected_to_work"]]
    harmless = [r for r in rows if r["expected_to_work"] and r["case"] != "baseline"]
    report = {"caught": sum(not r["works"] for r in breaks), "breakages": len(breaks),
              "false_alarms": sum(not r["works"] for r in harmless), "harmless_changes": len(harmless),
              "baselines_passing": sum(r["works"] for r in rows if r["case"] == "baseline"),
              "baselines": sum(r["case"] == "baseline" for r in rows), "seconds": round(time.monotonic() - started), "rows": rows}
    (output / "check_of_the_checks.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    lines = [f"# Check of the checks\n", f"Caught {report['caught']} of {report['breakages']} breakages; "
             f"{report['false_alarms']} false alarms in {report['harmless_changes']} harmless changes; "
             f"{report['baselines_passing']} of {report['baselines']} known-good designs pass.\n",
             "| Design | Change | Should work | Works | Why |", "|---|---|---|---|---|"]
    lines += [f"| {r['design']} | {r['case']} | {'yes' if r['expected_to_work'] else 'no'} | {'yes' if r['works'] else 'no'} | "
              f"{r['why'][:220].replace('|', '/')} |" for r in rows]
    (output / "check_of_the_checks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


# ---- held-out live requests ---------------------------------------------------------------------------------
# Written before any of them was run. Not for tuning: add new ones after fixing something.
HELD_OUT = [
    {"prompt": "A hook that clamps onto the edge of a 30 mm thick desk to hang a backpack weighing up to 6 kg.",
     "constraints": {"load_n": 59}, "coverage": "components: desk_clamp + hook"},
    {"prompt": "A clip that holds a 5 mm USB charging cable along the edge of a 20 mm thick desk so it does not fall behind.",
     "constraints": {}, "coverage": "partly: desk_clamp + cradle"},
    {"prompt": "A wall-screwed holder for a 120 mm diameter round plant pot weighing 2 kg.",
     "constraints": {"load_n": 20}, "coverage": "partly: wall_plate + cup"},
    {"prompt": "A stand that holds a smartphone upright on a desk at a comfortable viewing angle.",
     "constraints": {}, "coverage": "none: no stand component, tilted support"},
    {"prompt": "A hanger for over-ear headphones that screws to the underside of a desk.",
     "constraints": {}, "coverage": "none: plate on an underside, hook hanging down"},
    {"prompt": "A bracket that holds a 22 mm diameter curtain rod 60 mm out from the wall.",
     "constraints": {}, "coverage": "partly: wall_plate + cradle"},
    {"prompt": "A holder for six AA batteries that screws to the side of a desk drawer.",
     "constraints": {}, "coverage": "none"},
    {"prompt": "A hook for a 3 kg shopping bag that clamps to a 40 mm thick shelf.",
     "constraints": {"load_n": 30}, "coverage": "components: desk_clamp + hook"},
    {"prompt": "A wall mount for a 70 mm wide TV remote control.",
     "constraints": {}, "coverage": "none: pocket holder"},
    {"prompt": "A cable organiser that clamps to a 25 mm desk edge and keeps four cables apart.",
     "constraints": {}, "coverage": "partly: desk_clamp + cradles"},
    {"prompt": "A holder for a 65 mm diameter coffee mug that hangs from a 18 mm shelf edge.",
     "constraints": {"load_n": 5}, "coverage": "components: desk_clamp + cup"},
    {"prompt": "A key hook rack with three hooks that screws to a wall.",
     "constraints": {}, "coverage": "partly: wall_plate + hooks"},
]


def _kind(text: str) -> str:
    for tag, kind in (("[fit]", "fit"), ("[contact]", "contact"), ("[insertion]", "insertion"), ("[retention]", "retention"),
                      ("separate solids", "separate pieces"), ("did not answer", "provider timeout"), ("HTTP", "provider error"),
                      ("could not connect", "provider unreachable"), ("No reference bodies", "no environment"),
                      ("validation error", "invalid spec"), ("spec:", "invalid spec"), ("Repair", "repair rejected")):
        if tag in text:
            return kind
    return "other"


def live_sample(output: str | Path, count: int = 5, rounds: int = 3, concurrency: int = 2) -> dict:
    """Run held-out requests end to end with the configured models, in their own data folder."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    from dotenv import load_dotenv
    load_dotenv(override=False)
    os.environ["COFORM_DESIGN_MAX_ROUNDS"] = str(rounds)
    from .designer import PROGRESS, Designer
    from .service import CoForm
    output = Path(output)
    service = CoForm(output / "store")
    designer = Designer(service)
    chosen = HELD_OUT[:count]  # the first five span covered, partly covered and uncovered mechanisms

    def run(case):
        started = time.monotonic()
        try:
            pid = designer.create(case["prompt"], case["constraints"], live=True)["project"]["id"]
        except Exception as exc:  # noqa: BLE001 - recorded as the outcome
            return {**case, "outcome": "could not start", "detail": str(exc)[:400]}
        while PROGRESS.get(pid, {}).get("running", True):
            time.sleep(2)
        state = designer.state(pid)
        attempts = state["attempts"]
        usage = [a.get("usage", {}) for a in attempts] + [r.get("usage", {}) for r in state["rounds"]]
        last = (attempts[-1].get("errors") or [""])[0] if attempts else ""
        spec = state["specs"][-1]["spec"] if state["specs"] else None
        labels = [f["label"] for f in spec["features"]] if spec else []
        used = sorted({t for t in ("clamp", "cup", "saddle", "plate", "hook", "web") if any(l.startswith(t) for l in labels)})
        return {**case, "project_id": pid, "outcome": "passing part" if state["best"] else "working draft, not yet strong enough"
                if state["specs"] else "no working draft",
                "attempts": len(attempts), "redesigns": sum(a.get("repair_source") == "redesign" for a in attempts),
                "code_fixes": sorted({a["repair_source"] for a in attempts if str(a.get("repair_source", "")).startswith("code")}),
                "components_seen": used, "rounds": len(state["rounds"]),
                "stopped_on": None if state["specs"] else _kind(last), "last_error": last[:500],
                "calls": sum(u.get("calls", 0) for u in usage), "minutes": round((time.monotonic() - started) / 60, 1),
                "best_mass_g": state["best"]["results"]["mass_g"] if state["best"] else None}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(run, chosen))
    report = {"requests": len(results), "working_drafts": sum(r["outcome"] != "no working draft" for r in results),
              "passing_parts": sum(r["outcome"] == "passing part" for r in results), "rounds_per_request": rounds, "results": results}
    output.mkdir(parents=True, exist_ok=True)
    (output / "live_sample.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    lines = ["# Held-out live sample\n", f"{report['working_drafts']} of {report['requests']} reached a working draft; "
             f"{report['passing_parts']} reached a passing part ({rounds} optimisation rounds each).\n",
             "| Request | Coverage | Outcome | Attempts | Stopped on | Calls | Minutes |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['prompt'][:70]} | {r['coverage']} | {r['outcome']} | {r.get('attempts', '')} | {r.get('stopped_on') or ''} | "
              f"{r.get('calls', '')} | {r.get('minutes', '')} |" for r in results]
    (output / "live_sample.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    service.store.close()
    return report
