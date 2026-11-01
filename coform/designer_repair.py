"""Bounded edits and evidence validation for the part designer's repair loop."""
from copy import deepcopy

from . import freeform as ff
from .design_geometry import feature_measurements, verify_claim


DESIGN_INTENT_RULES = """
Translate the request into a physical design brief BEFORE choosing coordinates. This applies to
any part type, not just brackets or hooks. Preserve this brief throughout repairs.
1. Function: what must the finished part do, and what object does it hold, enclose, guide or join?
2. Interfaces: identify contact/mating surfaces, mounting or gripping locations, insertion
   directions, fasteners and assembly order. Give the intended coordinate plane or region.
3. Keep-clear space: identify surrounding objects, fastener/tool access, insertion paths,
   moving-part travel, and openings needed for the function. State which side of each interface
   can contain material. Keep ribs, bosses and curves out of these regions. Do not assume every
   interface is a wall or every part needs screw holes. Intentional offsets need an explanation.
4. Load path: connect the real contact/load region to the real attachment region with continuous
   material. A numerical fixed support is not a substitute for a physically usable attachment.
5. Process: preserve drainage, open functional pockets, minimum walls and assembly clearances.
   For each hollow region, identify a continuous opening to the exterior in the proposed print
   and drain orientation. A blind recess does not drain a cavity behind it. Cut through the full
   separating wall with a small overlap past the exterior. Solid parts need no invented drains.
   Avoid unsupported geometry types. Prefer simple geometry that satisfies the brief.
6. Before returning: check each rotated feature's final bounds, feature-to-feature alignment,
   openings and access against this brief; check loads, units and limits against the request.
Do not invent precise mating dimensions or hardware as known facts: list provisional choices
in assumptions. Do not weaken the request to make a simulation pass. Keep the brief concise
and use it to constrain the geometry, rather than merely describing the geometry afterwards.
"""


REPAIR_RULES = """When previous_spec is present, repair that design instead of drafting a replacement.
Return {"repair": {"parameters": {"existing_name": new_value},
"features": [{"index": 0, "changes": {"at": [x,y,z]}}],
"environment": [{"index": 1, "changes": {"at": [x,y,z]}}] (only a payload or screw may move; the desk or wall never does),
"supports": [{"index": 0, "changes": {"min": [x,y,z], "max": [x,y,z]}}],
"loads": [{"index": 0, "changes": {"min": [x,y,z], "max": [x,y,z]}}]},
"assumptions": ["Explain which failure this fixes"]}.
Omit unchanged sections and fields. Change at most SIX parameters, features or regions total
per repair (a feature counts once even if several of its fields change). Indices are zero-based.
Feature changes may contain only at, size,
radius, length, axis, rotate, legs, thickness. Keep feature identities, intended functions, load forces and
requirements. Extend or reposition a trapped pocket; do not delete a functional feature.
Use failure_history and measured check results; do not repeat a failed edit. Change only the
features or regions implicated by the evidence and explain the correction. Preserve the
design_intent, including interfaces, functional openings, assembly access and keep-clear space.
Fix blocking geometry failures before optimising bend or mass. Use previous_geometry for
evaluated feature bounds after rotation, not just the unrotated dimensions or pivot. Moving a
pivot to the interface does not make the rotated feature clear it. Check the entire new bounds.
Return only the repair and assumptions, not a replacement spec or a rewritten design_intent.
Mounts, keep-clear space, every body's size and contract, and every "fixed" parameter are the world: no repair
changes them. A payload or screw may move with the design (environment "at"): the bottle stands in the moved cup.
ADJUST OR REPLACE: a repair adjusts dimensions and positions of the existing mechanism. When the evidence shows
the mechanism itself is wrong (the desk cannot fit between the jaws, the bottle has nothing to stand in or
nothing around it, a screw cannot be reached or does not press on anything, a shelf where a retaining cage is
needed), small edits will not fix it. Then return instead {"redesign": SPEC, "design_intent": {...}, "reason": "which
evidence shows the concept is wrong and how the new one works"}: a complete new spec for the same job that keeps
every mount and keep-clear body, every payload's size, force and contract, every fixed parameter and the
requirements exactly. Features, parameters, where the payload stands and the screws are free to change.
At most two redesigns are allowed per drafting run, so use them for concepts, not for dimensions.
If checkpoint is present, previous_spec is that passing checkpoint, NOT the last failed branch.
Preserve its contact, fit, insertion and retention relationships. Change related pieces together in one
repair, keeping their shared parameter expressions. A failed branch is evidence, not the next baseline.
Read contact_diagnostics: a cut can remove an intended supporting face even when the feature coordinates
look aligned. Restore the material or limit that cut before moving the body it must support.
Preserve declared relationships. These compare feature faces and keep component interiors, walls and
through-cuts aligned as parameters change. Do not remove the constraints to make a repair pass.
"""

REVIEW_CLAIM_RULES = """
Every must_fix needs a machine-verifiable claim describing the alleged failure. A citation alone is not
verification. Use a failed check reference, or add one of these claim objects to the issue:
- {"kind":"comparison", "left":"measure:features:3:radius", "op":"lt", "right":3.2}
  Operands are supplied measure: references or finite numbers; at least one must be a reference.
  Ops: lt, le, eq, ge, gt. Reference keys include measure:features:I:min:x / max:z / at:y /
  radius / length / thickness and measure:parameters:NAME. State why this measured condition violates
  the request, rather than inventing an arbitrary threshold.
- {"kind":"axial_span", "cut":9, "solid":3, "axis":"z", "expected":false}
  Tests whether a cut spans a named added primitive. Use expected:false for an alleged blind cut.
  Supports unrotated boxes and cylinders parallel to the axis, with overlapping cross-sections.
  This local proof does not establish drainage through other features or in every print orientation.
- {"kind":"check_failed", "check":"contact"}
The condition must be true to support the objection. Machine results override numerical assertions in
prose. On clarification, retract a false numerical claim or supply a different specific tested concern.
If a concern cannot be checked with these tools, keep it explicitly unverified for human review; do not
invent a comparison to justify it. Passing code checks do not prove every physical assumption.
"""


_FIELDS = {"features": {"at", "size", "radius", "length", "axis", "rotate", "legs", "thickness"},
           "supports": {"min", "max"}, "loads": {"min", "max"},
           # A payload or screw may move with the design (the bottle stands in the moved cup); nothing else of the world.
           "environment": {"at"}}
MOVABLE = ("payload", "hardware")


def _constant_expressions(value):
    """Treat 0, 0.0 and '0' as the same geometry, preserving parameter expressions."""
    if isinstance(value, dict):
        return {k: _constant_expressions(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_constant_expressions(v) for v in value]
    if isinstance(value, str):
        try:
            return ff.evaluate(value, {})
        except ValueError:
            pass
    return value


MAX_REDESIGNS = 2  # per drafting run: enough to change a wrong concept, not enough to thrash between concepts


def redesign_from(spec: ff.DesignSpec, raw) -> ff.DesignSpec:
    """A new mechanism for the same job: anything about the part may change, nothing about the brief.

    The requirements, every fixed dimension, every mount and keep-clear body, and every payload's size, force
    and contract stay exactly as they were, so a redesign cannot pass by changing the problem. The payload may
    stand somewhere else and the screws may change with the mechanism. A design without an environment must
    gain one.
    """
    if not isinstance(raw, dict):
        raise ValueError("A redesign is a complete spec")
    proposed = ff.DesignSpec.model_validate(raw)
    if proposed.requirements != spec.requirements:
        raise ValueError("A redesign keeps the requirements")
    fixed = lambda s: {n: p for n, p in s.parameters.items() if p.role == "fixed"}  # noqa: E731
    if fixed(proposed) != fixed(spec):
        raise ValueError("A redesign keeps every fixed dimension (role \"fixed\") with the same name and value")
    if spec.environment:
        # The world stays: the same desks, walls and keep-clear space, and the same payloads (size, force,
        # contract). A new mechanism may stand the bottle somewhere else and bring its own screws.
        world = lambda s: [b.model_dump(mode="json") for b in s.environment if b.role in ("mount", "keep_clear")]  # noqa: E731
        carried = lambda s: {b.name: {**b.model_dump(mode="json"), "at": None} for b in s.environment if b.role == "payload"}  # noqa: E731
        if world(proposed) != world(spec):
            raise ValueError("A redesign keeps every mount and keep-clear body exactly: the desk or wall is the world it must work in")
        if carried(proposed) != carried(spec):
            raise ValueError("A redesign keeps every payload's name, size, force and contract; only where it stands may change")
    if not proposed.environment:
        raise ValueError("A redesign must model the environment the part works in")
    if proposed.model_dump(mode="json") == spec.model_dump(mode="json"):
        raise ValueError("Redesign makes no change; address the recorded failure")
    return proposed


def repair_from_spec(spec: ff.DesignSpec, raw: dict) -> dict:
    """Recover allowed edits from a full-spec reply without permitting a redesign.

    Cosmetic name/summary/parameter notes are ignored; identities, ranges, loads and
    requirements must stay intact. The resulting patch still goes through apply_repair.
    """
    proposed = ff.DesignSpec.model_validate(raw)
    before, after = spec.model_dump(mode="json"), proposed.model_dump(mode="json")
    if proposed.requirements != spec.requirements:
        raise ValueError("Repair cannot change requirements")
    if proposed.relationships != spec.relationships:
        raise ValueError("Repair must preserve component relationships; use a verified redesign to change the mechanism")
    if set(proposed.parameters) != set(spec.parameters):
        raise ValueError("Repair must preserve parameter names and ranges")
    patch = {"parameters": {}}
    for name, param in proposed.parameters.items():
        old = spec.parameters[name]
        if (param.min, param.max, param.role) != (old.min, old.max, old.role):
            raise ValueError(f"Repair must preserve the range and role of parameter '{name}'")
        if param.value != old.value:
            patch["parameters"][name] = param.value
    for section, fields in _FIELDS.items():
        if len(before[section]) != len(after[section]):
            raise ValueError(f"Repair must preserve {section} count and identities")
        edits = []
        for index, (old, new) in enumerate(zip(before[section], after[section])):
            protected = set(old) - fields
            if any(old[k] != new[k] for k in protected):
                raise ValueError(f"Repair cannot change environment[{index}] ('{old['name']}') except where a payload or screw stands"
                                 if section == "environment" else f"Repair cannot change {section}[{index}] identity, operation, shape or force")
            changes = {k: new[k] for k in sorted(fields)
                       if _constant_expressions(old[k]) != _constant_expressions(new[k])}
            if changes:
                edits.append({"index": index, "changes": changes})
        if edits:
            patch[section] = edits
    return patch


_POSITION = {"features": ("at",), "supports": ("min", "max"), "loads": ("min", "max"), "environment": ("at",)}


def _rigid_move(section, old, new, old_values, new_values):
    """The (dx, dy, dz) that turns `old` into `new` when a translation is all that changed, else None.

    A feature moves with its `at` (rotations turn about it); a region needs min and max shifted alike.
    """
    a, b = old.model_dump(mode="json"), new.model_dump(mode="json")
    keys = _POSITION[section]
    if any(_constant_expressions(a[k]) != _constant_expressions(b[k]) for k in a if k not in keys):
        return None
    try:
        shifts = {tuple(round(ff.evaluate(y, new_values) - ff.evaluate(x, old_values), 3) for x, y in zip(a[k], b[k]))
                  for k in keys}
    except ValueError:
        return None
    return shifts.pop() if len(shifts) == 1 else None


def apply_repair(spec: ff.DesignSpec, patch: dict) -> ff.DesignSpec:
    """Apply a small edit without allowing requirements or loads to be weakened."""
    allowed = {"parameters", "features", "supports", "loads", "environment"}
    if not isinstance(patch, dict) or not patch or set(patch) - allowed:
        raise ValueError("Repair must contain only parameters, features, supports, loads or environment (payload/screw positions)")
    raw = deepcopy(spec.model_dump(mode="json"))
    params = patch.get("parameters", {})
    if not isinstance(params, dict):
        raise ValueError("Repair parameters must map existing names to values")
    values = ff.values_of(spec, params)
    for name in params:
        if isinstance(params[name], bool):
            raise ValueError("Repair dimensions must be numbers")
        raw["parameters"][name]["value"] = values[name]
    for section, fields in _FIELDS.items():
        edits = patch.get(section, [])
        if not isinstance(edits, list):
            raise ValueError(f"Repair {section} must be a list of indexed changes")
        seen = set()
        for edit in edits:
            if not isinstance(edit, dict) or set(edit) != {"index", "changes"}:
                raise ValueError("Each repair edit needs index and changes")
            index, changes = edit["index"], edit["changes"]
            if type(index) is not int or not 0 <= index < len(raw[section]) or index in seen:
                raise ValueError(f"Invalid or repeated {section} index")
            if not isinstance(changes, dict) or not changes or set(changes) - fields:
                raise ValueError(f"Unsupported {section} repair fields; allowed: {sorted(fields)}")
            if section == "environment" and raw[section][index]["role"] not in MOVABLE:
                raise ValueError(f"'{raw[section][index]['name']}' is a {raw[section][index]['role']}: the desk, wall or keep-clear "
                                 "space does not move; only a payload or screw may")
            seen.add(index)
            raw[section][index].update({k: v for k, v in changes.items()
                                        if _constant_expressions(v) != _constant_expressions(raw[section][index].get(k))})
    repaired = ff.DesignSpec.model_validate(raw)
    from .design_geometry import relationship_check
    relation = relationship_check(repaired, ff.values_of(repaired))
    if not relation["passed"]:
        raise ValueError("Repair breaks component relationships: " + relation["detail"])
    # Count effective changes after validation, not repeated unchanged fields in the reply.
    changed = [f"parameter {n}" for n, p in repaired.parameters.items() if p.value != spec.parameters[n].value]
    old_values, new_values = ff.values_of(spec), ff.values_of(repaired)
    moves = {}
    for section in _FIELDS:
        for i, (a, b) in enumerate(zip(getattr(spec, section), getattr(repaired, section))):
            if _constant_expressions(a.model_dump(mode="json")) != _constant_expressions(b.model_dump(mode="json")):
                changed.append(f"{section[:-1]} {i} '{getattr(a, 'label', None) or getattr(a, 'name', '')}'")
                offset = _rigid_move(section, a, b, old_values, new_values)
                if offset is not None:
                    moves.setdefault(offset, []).append(changed[-1])
    # Moving a sub-assembly (a cup with its void, drain holes and load) by one offset is one decision.
    count = len(changed) - sum(len(group) - 1 for group in moves.values())
    if count > 6:
        # Say what was counted: a drafter told only "at most six" kept resending 8- and 9-item repairs.
        raise ValueError(f"Repair at most six parameters, features or regions per attempt; this one changed {count}: "
                         f"{', '.join(changed)}. Pieces moved together by exactly the same offset count once. Send the "
                         "six that fix the recorded failure; the rest can follow next attempt.")
    if not count:
        raise ValueError("Repair makes no change; address the recorded failure")
    return repaired


def check_summary(result):
    return [{k: c[k] for k in ("name", "label", "value", "limit", "passed", "detail") if k in c}
            for c in result.get("checks", [])]


def evidence_for(spec, result):
    """Stable references within this candidate, not claims that simulation proves real-world safety."""
    evidence = {f"check:{c['name']}": c for c in check_summary(result)}
    evidence.update(feature_measurements(spec, result.get("values", ff.values_of(spec))))
    for section in ("features", "supports", "loads", "environment"):
        for i, item in enumerate(getattr(spec, section)):
            evidence[f"{section}:{i}"] = item.model_dump(mode="json", exclude_none=True)
    # What the functional checks measured for each body: faces touched, what holds it, whether it can get in.
    for body in result.get("scene", []):
        evidence[f"scene:{body['name']}"] = body
    evidence["requirements"] = spec.requirements.model_dump()
    if result.get("design_intent"):
        evidence["design_intent"] = result["design_intent"]
    if result.get("design_brief"):
        evidence["design_brief"] = result["design_brief"]
    return evidence


def assess_review(role, data, failure, evidence, spec=None):
    """Keep unverified or contradictory objections visible; never convert missing replies into approval."""
    review = {"agent": role, "verdict": "no_reply", "issues": [], "mode": "llm"}
    if (failure or not isinstance(data, dict) or data.get("verdict") not in ("approve", "revise")
            or not isinstance(data.get("issues"), list)):
        return {**review, "note": f"No usable review ({failure or 'invalid review schema'})"}
    for item in data["issues"][:8]:
        if not isinstance(item, dict) or not isinstance(item.get("problem"), str) or not item["problem"].strip():
            return {**review, "note": "Review contains an invalid issue"}
        refs = item.get("evidence_ids", [])
        refs = refs if isinstance(refs, list) and all(isinstance(r, str) for r in refs) else []
        severity = item.get("severity", "note")
        if severity not in ("must_fix", "should_fix", "note"):
            return {**review, "note": "Review contains an invalid severity"}
        explanation = str(item.get("evidence") or "")[:600]
        valid = bool(refs) and all(r in evidence for r in refs) and bool(explanation.strip())
        conflicts = [r for r in refs if r.startswith("check:") and evidence.get(r, {}).get("passed") is True]
        proof = verify_claim(item.get("claim"), evidence, spec) if severity == "must_fix" else None
        failed_check = any(r.startswith("check:") and evidence.get(r, {}).get("passed") is False for r in refs)
        status = "cited" if valid else "unverified"
        if severity == "must_fix":
            if conflicts or proof["status"] == "contradicted":
                status = "contradicts_check"
            elif valid and (proof["status"] == "verified" or ("claim" not in item and failed_check)):
                status = "verified"
            else:
                status = "unverified"
        review["issues"].append({"severity": severity, "feature": str(item.get("feature") or "")[:80],
                                 "problem": item["problem"][:600], "fix": str(item.get("fix") or "")[:400],
                                 "evidence_ids": refs[:8], "evidence": explanation, "verification": status,
                                 **({"claim": item.get("claim"), "claim_result": proof} if proof else {})})
    must = [i for i in review["issues"] if i["severity"] == "must_fix"]
    if any(i["verification"] != "verified" for i in must) or (data["verdict"] == "revise" and not must):
        review.update(verdict="needs_evidence", note="Verify the claimed failure with a measured check before changing geometry")
    else:
        review["verdict"] = "revise" if must else "approve"
    return review
