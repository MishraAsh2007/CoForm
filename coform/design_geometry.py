"""Measured geometry for review claims and contact diagnostics, independent of part names."""
import math
import operator

import numpy as np

from . import freeform as ff


def feature_measurements(spec, values):
    out = {}
    for i, feature in enumerate(spec.features):
        lo, hi = ff.feature_bounds(feature, values)
        for field, vec in (("min", lo), ("max", hi), ("at", ff._vec(feature.at, values))):
            for axis, value in zip("xyz", vec):
                out[f"measure:features:{i}:{field}:{axis}"] = float(value)
        for field in ("radius", "length", "thickness"):
            expr = getattr(feature, field)
            if expr is not None:
                out[f"measure:features:{i}:{field}"] = ff.evaluate(expr, values)
    for name, value in values.items():
        out[f"measure:parameters:{name}"] = value
    return out


def relationship_check(spec, values):
    """Check declared component relationships on evaluated faces, for any feature names and axes."""
    by_label = {f.label: f for f in spec.features}
    failures = []
    for relation in spec.relationships:
        k = "xyz".index(relation.axis)
        lo, hi = ff.feature_bounds(by_label[relation.left_feature], values)
        a, b = ff.feature_bounds(by_label[relation.right_feature], values)
        left = (lo if relation.left_bound == "min" else hi)[k]
        right = (a if relation.right_bound == "min" else b)[k] + ff.evaluate(relation.offset_mm, values)
        passed = {"le": left <= right + 1e-6, "ge": left >= right - 1e-6, "eq": abs(left - right) <= 1e-6}[relation.op]
        if not passed:
            failures.append(f"'{relation.left_feature}' {relation.left_bound}.{relation.axis}={left:g} must be "
                            f"{relation.op} '{relation.right_feature}' {relation.right_bound}.{relation.axis} + offset={right:g} mm")
    return {"name": "relationships", "label": "Component face relationships preserved", "passed": not failures,
            "value": len(failures), "limit": 0, "owner": "mechanics", **({"detail": "; ".join(failures)} if failures else {})}


def axial_span(spec, cut_index, solid_index, axis):
    """Does an axis-aligned cut span a named primitive along an axis it intersects?

    This proves a local through-cut, not global drainage, accessibility or print orientation.
    Rotated/unsupported cases stay unverified rather than using a bounding-box guess.
    """
    if (type(cut_index) is not int or type(solid_index) is not int or
            not 0 <= cut_index < len(spec.features) or not 0 <= solid_index < len(spec.features) or axis not in "xyz" or len(axis) != 1):
        raise ValueError("Use valid cut/solid feature indices and axis x, y or z")
    cut, solid = spec.features[cut_index], spec.features[solid_index]
    if cut.op != "subtract" or solid.op != "add" or cut_index <= solid_index:
        raise ValueError("The cut must follow the added primitive in the CAD sequence")
    if any(f.rotate is not None or f.shape == "wedge" or (f.shape == "cylinder" and f.axis != axis) for f in (cut, solid)):
        raise ValueError("Axial-span proof supports unrotated boxes and cylinders parallel to the requested axis")
    values = ff.values_of(spec)
    lo, hi = ff.feature_bounds(cut, values)
    a, b = ff.feature_bounds(solid, values)
    k, other = "xyz".index(axis), [i for i in range(3) if i != "xyz".index(axis)]
    if cut.shape == solid.shape == "box":
        intersects = all(min(hi[i], b[i]) > max(lo[i], a[i]) for i in other)
    elif cut.shape == solid.shape == "cylinder":
        centers = [ff._vec(f.at, values) for f in (cut, solid)]
        intersects = math.dist([centers[0][i] for i in other], [centers[1][i] for i in other]) < sum(ff.evaluate(f.radius, values) for f in (cut, solid))
    else:
        cyl, box = (cut, solid) if cut.shape == "cylinder" else (solid, cut)
        center = ff._vec(cyl.at, values)
        blo, bhi = ff.feature_bounds(box, values)
        intersects = sum(max(blo[i] - center[i], 0, center[i] - bhi[i]) ** 2 for i in other) < ff.evaluate(cyl.radius, values) ** 2
    if not intersects:
        raise ValueError("The cut and primitive do not intersect in the perpendicular plane")
    spans = lo[k] <= a[k] + 1e-6 and hi[k] >= b[k] - 1e-6
    return {"spans": spans, "axis": axis, "cut_interval_mm": [lo[k], hi[k]], "solid_interval_mm": [a[k], b[k]],
            "scope": "named primitive only; does not prove a drain path through the entire final solid"}


def verify_claim(claim, evidence, spec=None):
    """Evaluate the condition a reviewer says is wrong. True conditions support an objection."""
    try:
        if not isinstance(claim, dict):
            raise ValueError("A must-fix geometry objection needs a structured claim, not just a citation")
        kind = claim.get("kind")
        if kind == "comparison":
            def number(value):
                if isinstance(value, str):
                    if not value.startswith("measure:") or value not in evidence:
                        raise ValueError("Comparison operands must cite supplied measure: references")
                    value = evidence[value]
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("Comparison operands must be finite numbers")
                return value
            if not any(isinstance(claim.get(k), str) for k in ("left", "right")):
                raise ValueError("At least one operand must be a measured reference")
            left, right = number(claim.get("left")), number(claim.get("right"))
            ops = {"lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge,
                   "eq": lambda a, b: math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)}
            if claim.get("op") not in ops:
                raise ValueError("Comparison op must be lt, le, gt, ge or eq")
            true = ops[claim["op"]](left, right)
            facts = {"left": left, "op": claim["op"], "right": right}
        elif kind == "axial_span":
            if spec is None or type(claim.get("expected")) is not bool:
                raise ValueError("Axial span needs a spec and a boolean expected value")
            facts = axial_span(spec, claim.get("cut"), claim.get("solid"), claim.get("axis", ""))
            true = facts["spans"] == claim["expected"]
        elif kind == "check_failed":
            check = evidence.get(f"check:{claim.get('check')}")
            if not isinstance(check, dict) or type(check.get("passed")) is not bool:
                raise ValueError("Unknown or unmeasured check")
            facts, true = check, not check["passed"]
        else:
            raise ValueError("Unsupported claim; use comparison, axial_span or check_failed")
        return {"status": "verified" if true else "contradicted", "facts": facts}
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        return {"status": "unverified", "reason": str(exc)}


def inside_feature(feature, values, points):
    """Point membership in a primitive, including rotated boxes, cylinders and wedges."""
    q = np.asarray(points, dtype=float) - np.array(ff._vec(feature.at, values))
    if feature.rotate:
        axis = np.eye(3)["xyz".index(feature.rotate.axis)]
        angle = -math.radians(ff.evaluate(feature.rotate.degrees, values))
        q = q * math.cos(angle) + np.cross(axis, q) * math.sin(angle) + np.outer(q @ axis, axis) * (1 - math.cos(angle))
    if feature.shape == "box":
        size = np.array(ff._vec(feature.size, values))
        return ((q >= -1e-7) & (q <= size + 1e-7)).all(axis=1)
    if feature.shape == "cylinder":
        k = "xyz".index(feature.axis)
        other = [i for i in range(3) if i != k]
        return ((q[:, k] >= -1e-7) & (q[:, k] <= ff.evaluate(feature.length, values) + 1e-7)
                & ((q[:, other] ** 2).sum(axis=1) <= ff.evaluate(feature.radius, values) ** 2 + 1e-7))
    ks = ["xyz".index(leg.axis) for leg in feature.legs]
    lengths = [ff.evaluate(leg.length, values) for leg in feature.legs]
    k = next(i for i in range(3) if i not in ks)
    lengths.append(ff.evaluate(feature.thickness, values))
    if any(abs(v) < 1e-9 for v in lengths):
        raise ValueError("Wedge dimensions must be nonzero")
    t = q[:, [*ks, k]] / lengths
    return ((t >= -1e-7).all(axis=1) & (t[:, 0] + t[:, 1] <= 1 + 1e-7) & (t[:, 2] <= 1 + 1e-7))


def removed_contact(spec, values, solid, face):
    """Trace sample points just outside a required contact face through the ordered CSG.

    Diagnostic only: the mesh checks still decide pass/fail. Does not assume any part type or labels.
    """
    k = "xyz".index(face[1])
    other = [i for i in range(3) if i != k]
    # Side contact on a round body needs curved-face sampling, not its bounding-box face.
    if solid.body.shape == "cylinder" and k != solid.axis:
        return None
    plane = solid.lo[k] if face[0] == "-" else solid.hi[k]
    points = []
    for u in np.linspace(0.1, 0.9, 9):
        for v in np.linspace(0.1, 0.9, 9):
            p = (solid.lo + solid.hi) / 2
            p[k] = plane + (-0.05 if face[0] == "-" else 0.05)
            p[other] = solid.lo[other] + np.array([u, v]) * (solid.hi[other] - solid.lo[other])
            if solid.body.shape == "cylinder" and sum((p[i] - solid.base[i]) ** 2 for i in other) >= (solid.radius * 0.95) ** 2:
                continue
            points.append(p)
    points = np.array(points)
    owners = np.full(len(points), -1)
    removed, sources = np.full(len(points), -1), np.full(len(points), -1)
    for i, f in enumerate(spec.features):
        inside = inside_feature(f, values, points)
        if f.op == "add":
            owners[inside], removed[inside], sources[inside] = i, -1, -1
        else:
            hit = inside & (owners >= 0)
            removed[hit], sources[hit], owners[hit] = i, owners[hit], -1
    erased = (owners < 0) & (removed >= 0)
    if not erased.any():
        return None
    cuts = sorted(set(int(i) for i in removed[erased]))
    adds = sorted(set(int(i) for i in sources[erased]))
    names = lambda ids: ", ".join(f"features:{i} '{spec.features[i].label or spec.features[i].shape}'" for i in ids)
    return {"body": solid.name, "face": face, "cut_features": cuts, "support_features": adds,
            "removed_samples": int(erased.sum()), "samples": len(points), "plane_mm": float(plane),
            "detail": f"At '{solid.name}' {face} contact ({face[1]}={plane:g} mm), {names(cuts)} remove material "
                      f"added by {names(adds)} at {int(erased.sum())}/{len(points)} sampled support locations. "
                      "Restore that contact material or limit the cut; moving the payload alone does not restore removed material."}
