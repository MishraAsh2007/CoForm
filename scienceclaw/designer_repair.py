"""Bounded edits and evidence validation for the part designer's repair loop."""
from copy import deepcopy

from . import freeform as ff


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
"""


_FIELDS = {"features": {"at", "size", "radius", "length", "axis", "rotate", "legs", "thickness"},
           "supports": {"min", "max"}, "loads": {"min", "max"}}


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


def repair_from_spec(spec: ff.DesignSpec, raw: dict) -> dict:
    """Recover allowed edits from a full-spec reply without permitting a redesign.

    Cosmetic name/summary/parameter notes are ignored; identities, ranges, loads and
    requirements must stay intact. The resulting patch still goes through apply_repair.
    """
    proposed = ff.DesignSpec.model_validate(raw)
    before, after = spec.model_dump(mode="json"), proposed.model_dump(mode="json")
    if proposed.requirements != spec.requirements:
        raise ValueError("Repair cannot change requirements")
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
                raise ValueError(f"Repair cannot change {section}[{index}] identity, operation, shape or force")
            changes = {k: new[k] for k in sorted(fields)
                       if _constant_expressions(old[k]) != _constant_expressions(new[k])}
            if changes:
                edits.append({"index": index, "changes": changes})
        if edits:
            patch[section] = edits
    return patch


def apply_repair(spec: ff.DesignSpec, patch: dict) -> ff.DesignSpec:
    """Apply a small edit without allowing requirements or loads to be weakened."""
    allowed = {"parameters", "features", "supports", "loads"}
    if not isinstance(patch, dict) or not patch or set(patch) - allowed:
        raise ValueError("Repair must contain only parameters, features, supports or loads")
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
            seen.add(index)
            raw[section][index].update({k: v for k, v in changes.items()
                                        if _constant_expressions(v) != _constant_expressions(raw[section][index].get(k))})
    repaired = ff.DesignSpec.model_validate(raw)
    # Count effective changes after validation, not repeated unchanged fields in the reply.
    count = sum(p.value != spec.parameters[n].value for n, p in repaired.parameters.items())
    for section in _FIELDS:
        count += sum(_constant_expressions(a.model_dump(mode="json")) !=
                     _constant_expressions(b.model_dump(mode="json"))
                     for a, b in zip(getattr(spec, section), getattr(repaired, section)))
    if count > 6:
        raise ValueError("Repair at most six parameters, features or regions per attempt")
    if not count:
        raise ValueError("Repair makes no change; address the recorded failure")
    return repaired


def check_summary(result):
    return [{k: c[k] for k in ("name", "label", "value", "limit", "passed", "detail") if k in c}
            for c in result.get("checks", [])]


def evidence_for(spec, result):
    """Stable references within this candidate, not claims that simulation proves real-world safety."""
    evidence = {f"check:{c['name']}": c for c in check_summary(result)}
    for section in ("features", "supports", "loads"):
        for i, item in enumerate(getattr(spec, section)):
            evidence[f"{section}:{i}"] = item.model_dump(mode="json", exclude_none=True)
    evidence["requirements"] = spec.requirements.model_dump()
    if result.get("design_intent"):
        evidence["design_intent"] = result["design_intent"]
    if result.get("design_brief"):
        evidence["design_brief"] = result["design_brief"]
    return evidence


def assess_review(role, data, failure, evidence):
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
        status = "contradicts_check" if severity == "must_fix" and conflicts else "cited" if valid else "unverified"
        review["issues"].append({"severity": severity, "feature": str(item.get("feature") or "")[:80],
                                 "problem": item["problem"][:600], "fix": str(item.get("fix") or "")[:400],
                                 "evidence_ids": refs[:8], "evidence": explanation, "verification": status})
    must = [i for i in review["issues"] if i["severity"] == "must_fix"]
    if any(i["verification"] != "cited" for i in must) or (data["verdict"] == "revise" and not must):
        review.update(verdict="needs_evidence", note="Clarify the objection using this candidate's evidence before changing geometry")
    else:
        review["verdict"] = "revise" if must else "approve"
    return review
