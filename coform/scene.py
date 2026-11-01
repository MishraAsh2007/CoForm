"""The world around a printed part, and evidence that the part works in it.

Reference bodies (freeform.Body) are the desk, bottle or screw the part works with. These checks run on
the nodes of the part's finite-element mesh, which fill its whole volume, before any strength result
counts:

- fit: no part material inside a body (the desk needs somewhere to be);
- contact: the part touches each body on the faces its contract names (a clamp grips the desk's top
  and bottom; a cup holds the bottle's base). A screw that bears on a body counts for the face it presses;
- insertion: the path a body takes into place is clear (the desk can slide in, the screw can be driven);
- retention: the part blocks every direction the body must not move (the bottle cannot slide out).

The simulation is then fixed where the part touches its mounts and loaded where it touches its payloads,
never in a volume someone declared, so a clamp that does not grip anything cannot pass on paper.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import freeform as ff

TOUCH_MM = 0.2     # a node this close to a body touches it
OVERLAP_MM = 0.2   # deeper inside a body than this is a collision, not a shared face
AXES = "xyz"
FUNCTION_CHECKS = ("fit", "contact", "insertion", "retention", "function")


@dataclass
class Solid:
    body: ff.Body
    lo: np.ndarray            # bounds (a cylinder's bounding box)
    hi: np.ndarray
    axis: int = 2             # cylinder only
    base: np.ndarray | None = None
    radius: float = 0.0
    length: float = 0.0
    force: tuple[float, float, float] | None = None  # evaluated force_n

    @property
    def name(self):
        return self.body.name

    def describe(self) -> dict:
        b = self.body
        out = {"name": b.name, "role": b.role, "shape": b.shape,
               "min": [round(float(v), 3) for v in self.lo], "max": [round(float(v), 3) for v in self.hi]}
        if b.shape == "cylinder":
            out.update(axis=AXES[self.axis], base=[round(float(v), 3) for v in self.base], radius=self.radius, length=self.length)
        for key in ("touch", "insert", "retain", "bears_on", "note"):
            value = getattr(b, key)
            if value:
                out[key] = list(value) if isinstance(value, tuple) else value
        if self.force:
            out["force_n"] = list(self.force)
        return out


def resolve(spec: ff.DesignSpec, values: dict[str, float]) -> list[Solid]:
    out = []
    for b in spec.environment:
        at = np.array(ff._vec(b.at, values), dtype=float)
        force = tuple(ff._vec(b.force_n, values)) if b.force_n else None
        if b.shape == "box":
            size = np.array(ff._vec(b.size, values), dtype=float)
            if (size <= 0).any():
                raise ff.BuildError(f"Reference body '{b.name}' has a non-positive size")
            out.append(Solid(b, at, at + size, force=force))
        else:
            r, length, a = ff.evaluate(b.radius, values), ff.evaluate(b.length, values), AXES.index(b.axis)
            if r <= 0 or length <= 0:
                raise ff.BuildError(f"Reference body '{b.name}' has a non-positive radius or length")
            lo, hi = at - r, at + r
            lo[a], hi[a] = at[a], at[a] + length
            out.append(Solid(b, lo, hi, a, at, r, length, force))
    return out


def depth(s: Solid, p: np.ndarray) -> np.ndarray:
    """Signed depth of points inside a body: positive inside, minus the distance outside."""
    if s.body.shape == "box":
        inside = np.minimum(p - s.lo, s.hi - p).min(axis=1)
        out = np.maximum(0.0, np.maximum(s.lo - p, p - s.hi))
        return np.where(inside > 0, inside, -np.sqrt((out ** 2).sum(axis=1)))
    a = s.axis
    u, v = (k for k in range(3) if k != a)
    along = p[:, a] - s.base[a]
    radial = np.hypot(p[:, u] - s.base[u], p[:, v] - s.base[v])
    inside = np.minimum(np.minimum(along, s.length - along), s.radius - radial)
    out = np.hypot(np.maximum(0.0, np.maximum(-along, along - s.length)), np.maximum(0.0, radial - s.radius))
    return np.where(inside > 0, inside, -out)


def swept(s: Solid, direction: str, p: np.ndarray, margin: float = OVERLAP_MM) -> np.ndarray:
    """Points the body passes through (deeper than `margin`) when it moves along `direction` without end."""
    k, sign = AXES.index(direction[1]), 1 if direction[0] == "+" else -1
    if s.body.shape == "box" or s.axis == k:
        lo, hi = s.lo.copy(), s.hi.copy()
        if sign > 0:
            hi[k] = np.inf
        else:
            lo[k] = -np.inf
        if s.body.shape == "box":
            return ((p > lo + margin) & (p < hi - margin)).all(axis=1)
        u, v = (m for m in range(3) if m != k)
        radial = np.hypot(p[:, u] - s.base[u], p[:, v] - s.base[v])
        return (p[:, k] > lo[k] + margin) & (p[:, k] < hi[k] - margin) & (radial < s.radius - margin)
    a = s.axis
    m = next(i for i in range(3) if i not in (a, k))
    along = p[:, a] - s.base[a]
    w = p[:, m] - s.base[m]
    reach = np.sqrt(np.clip((s.radius - margin) ** 2 - w ** 2, 0.0, None))
    return ((along > margin) & (along < s.length - margin) & (np.abs(w) < s.radius - margin)
            & (sign * (p[:, k] - s.base[k]) > -reach))


def distance_to_hit(s: Solid, direction: str, p: np.ndarray) -> np.ndarray:
    """How far the body travels along `direction` before reaching each point of its swept path."""
    k, sign = AXES.index(direction[1]), 1 if direction[0] == "+" else -1
    if s.body.shape == "box" or s.axis == k:
        return sign * p[:, k] - (s.hi[k] if sign > 0 else -s.lo[k])
    m = next(i for i in range(3) if i not in (s.axis, k))
    half_chord = np.sqrt(np.clip(s.radius ** 2 - (p[:, m] - s.base[m]) ** 2, 0.0, None))
    return sign * (p[:, k] - s.base[k]) - half_chord


def faces(s: Solid, p: np.ndarray) -> np.ndarray:
    """Which face of the body each (touching) point is on: '+x' ... '-z'."""
    names = np.array(["-x", "+x", "-y", "+y", "-z", "+z"])
    if s.body.shape == "box":
        dist = np.empty((len(p), 6))
        for k in range(3):
            others = [m for m in range(3) if m != k]
            within = ((p[:, others] >= s.lo[others] - TOUCH_MM) & (p[:, others] <= s.hi[others] + TOUCH_MM)).all(axis=1)
            dist[:, 2 * k] = np.where(within, np.abs(p[:, k] - s.lo[k]), np.inf)
            dist[:, 2 * k + 1] = np.where(within, np.abs(p[:, k] - s.hi[k]), np.inf)
        return names[dist.argmin(axis=1)]
    a = s.axis
    u, v = (k for k in range(3) if k != a)
    along = p[:, a] - s.base[a]
    du, dv = p[:, u] - s.base[u], p[:, v] - s.base[v]
    side = np.where(np.abs(du) >= np.abs(dv), np.where(du >= 0, f"+{AXES[u]}", f"-{AXES[u]}"),
                    np.where(dv >= 0, f"+{AXES[v]}", f"-{AXES[v]}"))
    return np.where(np.abs(along) <= TOUCH_MM, f"-{AXES[a]}",
                    np.where(np.abs(along - s.length) <= TOUCH_MM, f"+{AXES[a]}", side))


def _span(points: np.ndarray) -> str:
    lo, hi = points.min(axis=0), points.max(axis=0)
    return ", ".join(f"{AXES[k]} {lo[k]:.1f} to {hi[k]:.1f}" for k in range(3)) + " mm"


def _culprits(spec: ff.DesignSpec, values: dict[str, float], points: np.ndarray) -> str:
    """The added features that hold most of the given points."""
    counts = []
    for n, f in enumerate(spec.features):
        if f.op != "add":
            continue
        lo, hi = (np.array(v) for v in ff.feature_bounds(f, values))
        inside = int(((points >= lo - 0.01) & (points <= hi + 0.01)).all(axis=1).sum())
        if inside:
            counts.append((inside, ff._label(f, n)))
    return ", ".join(f"'{label}'" for _, label in sorted(counts, reverse=True)[:3]) or "the part"


def _gap(a: Solid, b: Solid) -> tuple[float, str | None]:
    """Gap between two bodies' bounds, and which face of `b` that `a` sits against when they touch."""
    gaps = np.maximum(0.0, np.maximum(a.lo - b.hi, b.lo - a.hi))
    face = None
    for k in range(3):
        # `a` ends on, or passes through, one face of `b`: a screw tip on the desk's underside, or a wall
        # screw driven 20 mm into the wall through its front face.
        if a.lo[k] <= b.lo[k] + TOUCH_MM and a.hi[k] >= b.lo[k] - TOUCH_MM and a.hi[k] < b.hi[k]:
            face = f"-{AXES[k]}"
            break
        if a.hi[k] >= b.hi[k] - TOUCH_MM and a.lo[k] <= b.hi[k] + TOUCH_MM and a.lo[k] > b.lo[k]:
            face = f"+{AXES[k]}"
            break
    return float(np.sqrt((gaps ** 2).sum())), face


def nearest_material(spec: ff.DesignSpec, values: dict[str, float], s: Solid, face: str, points: np.ndarray) -> dict | None:
    """Where the part stops short of a face it should touch: the nearest material beyond that face, within the
    body's footprint (for a cylinder's end, within its circle), and how far away it is."""
    k, sign = AXES.index(face[1]), 1 if face[0] == "+" else -1
    if s.body.shape == "cylinder" and k != s.axis:
        return None  # a round side needs curved sampling, as removed_contact says
    other = [m for m in range(3) if m != k]
    plane = s.hi[k] if sign > 0 else s.lo[k]
    within = ((points[:, other] >= s.lo[other]) & (points[:, other] <= s.hi[other])).all(axis=1)
    if s.body.shape == "cylinder":
        within &= ((points[:, other] - s.base[other]) ** 2).sum(axis=1) <= s.radius ** 2
    beyond = within & (sign * (points[:, k] - plane) > -TOUCH_MM)
    side = {"+z": "above", "-z": "under", "+x": "beyond the +x face of", "-x": "beyond the -x face of",
            "+y": "beyond the +y face of", "-y": "beyond the -y face of"}[face]
    if not beyond.any():
        return {"body": s.name, "face": face, "gap_mm": None,
                "detail": f"No part material lies {side} '{s.name}' within its footprint at all: add a support there."}
    distance = sign * (points[beyond, k] - plane)
    gap = float(distance.min())
    near = points[beyond][distance <= gap + 0.5]
    culprit = _culprits(spec, values, near)
    return {"body": s.name, "face": face, "gap_mm": round(gap, 2), "nearest_features": culprit,
            "detail": f"The nearest material {side} '{s.name}' is {culprit}, {gap:.1f} mm away ({face[1]} = {near[:, k].mean():.1f} mm "
                      f"vs the face at {plane:.1f} mm): extend that support to the face or move the {s.body.role} onto it."}


ADVICE = {"mount": "it needs that space, so keep the part outside it", "payload": "it would not fit, so leave room for it",
          "hardware": "there is no hole or room for it there", "keep_clear": "that space must stay empty"}


def sag(s: Solid, mesh_mm: float) -> float:
    """How far a straight mesh edge can bow into a round body (zero for a box)."""
    if s.body.shape == "box":
        return 0.0
    half = mesh_mm / 2
    return s.radius - np.sqrt(s.radius ** 2 - half ** 2) if half < s.radius else s.radius


def evaluate(spec: ff.DesignSpec, values: dict[str, float], nodes: dict, body_ids, mesh_mm: float, corners=None) -> dict:
    """Functional checks plus the supports and loads they imply, from the part's mesh nodes.

    Collisions and paths are judged on `corners` (tetrahedron corners, which lie on the true surfaces);
    contact uses every node, with room for mid-edge nodes that sit on chords of a round surface.
    """
    ids = np.array(sorted(body_ids))
    p = np.array([nodes[i] for i in ids], dtype=float)
    exact = np.isin(ids, np.array(sorted(corners))) if corners else np.ones(len(ids), dtype=bool)
    solids = resolve(spec, values)
    by_name = {s.name: s for s in solids}
    findings = {name: [] for name in ("fit", "contact", "insertion", "retention")}
    evidence, touching = [], {}
    for s in solids:
        d = depth(s, p)
        inside = (d > OVERLAP_MM) & exact
        touch = (d >= -TOUCH_MM) & (d <= OVERLAP_MM + sag(s, mesh_mm))
        touching[s.name] = touch
        on = faces(s, p[touch]) if touch.any() else np.array([])
        counts = {f: int((on == f).sum()) for f in sorted(set(on.tolist()))}
        item = {**s.describe(), "touched_faces": counts, "overlap_points": int(inside.sum())}
        if inside.any():
            findings["fit"].append(f"{_culprits(spec, values, p[inside])} reach up to {d[inside].max():.1f} mm into "
                                   f"'{s.name}' ({s.body.role}) at {_span(p[inside])}: {ADVICE[s.body.role]}.")
        if s.body.insert:
            path = swept(s, ff.opposite(s.body.insert), p) & exact & ~inside
            item["insertion_clear"] = not path.any()
            if path.any():
                findings["insertion"].append(f"'{s.name}' cannot move into place (moving {s.body.insert}): "
                                             f"{_culprits(spec, values, p[path])} block its path at {_span(p[path])}.")
        held = {}
        for direction in s.body.retain:
            blocking = swept(s, direction, p) & exact & ~inside
            if blocking.any():
                # Credit what it would hit first, not whatever has the most material further along.
                k = AXES.index(direction[1])
                travel = distance_to_hit(s, direction, p[blocking])
                first = p[blocking][travel <= travel.min() + max(2.0, mesh_mm)]
                held[direction] = f"{_culprits(spec, values, first)} at {AXES[k]} = {first[:, k].mean():.1f} mm"
            else:
                held[direction] = None
                findings["retention"].append(f"Nothing stops '{s.name}' moving {direction}: no part of the design is in its way on that side.")
        if held:
            item["retained_by"] = held
        evidence.append(item)
    # Hardware that bears on a body and is held by the part supplies that body's contact on its face.
    pressed = {}
    for s in solids:
        target = by_name.get(s.body.bears_on)
        if target is None:
            continue
        gap, face = _gap(s, target)
        engaged = bool(touching[s.name].any()) or (bool(s.body.retain) and all(
            evidence[solids.index(s)]["retained_by"].get(d) for d in s.body.retain))
        if gap > TOUCH_MM:
            findings["contact"].append(f"'{s.name}' does not reach '{target.name}' (gap {gap:.1f} mm), so it cannot press on it.")
        elif not engaged:
            findings["contact"].append(f"'{s.name}' is not held by the part: nothing touches or surrounds it, so it cannot press "
                                       f"on '{target.name}'.")
        else:
            if face:  # unresolved overlap: it still bears, but credits no particular face
                pressed.setdefault(target.name, set()).add(face)
    for s, item in zip(solids, evidence):
        reached = set(item["touched_faces"]) | pressed.get(s.name, set())
        missing = [f for f in s.body.touch if f not in reached]
        if missing:
            have = ", ".join(sorted(reached)) or "none"
            findings["contact"].append(f"Nothing touches the {', '.join(missing)} face of '{s.name}' (needs {', '.join(s.body.touch)}; "
                                       f"touched: {have}).")
            from .design_geometry import removed_contact
            item["contact_diagnostics"] = []
            for face in missing:
                # A cut took the support away, the support stops short of the face, or both: say which.
                cut = removed_contact(spec, values, s, face)
                short = nearest_material(spec, values, s, face, p[exact])
                item["contact_diagnostics"] += [d for d in (cut, short) if d]
                if cut and short and short["gap_mm"] is not None:
                    # Intact material close by: moving or extending reaches it, so lead with that, and keep the cut as a fact.
                    names = lambda ids: ", ".join(f"features:{i} '{spec.features[i].label or spec.features[i].shape}'" for i in ids)  # noqa: E731
                    detail = short["detail"] + f" (At that face, {names(cut['cut_features'])} remove material added by {names(cut['support_features'])}.)"
                else:
                    detail = (cut or short or {}).get("detail")
                if detail:
                    findings["contact"][-1] += " " + detail
    # Supports where the part touches what holds it (directly, or through a screw pressing on it).
    holders = {s.name for s in solids if s.body.role == "mount"}
    holders |= {s.name for s in solids if s.body.role == "hardware" and s.body.bears_on in holders}
    fixed_mask = np.zeros(len(ids), dtype=bool)
    near_mask = np.zeros(len(ids), dtype=bool)
    for s in solids:
        if s.name in holders:
            fixed_mask |= touching[s.name]
            near_mask |= depth(s, p) >= -(TOUCH_MM + mesh_mm)
    loads = [(s.name, s.force, ids[touching[s.name]].tolist()) for s in solids if s.body.role == "payload"]
    fatal = []
    if not fixed_mask.any():
        fatal.append(f"The part touches none of {', '.join(repr(n) for n in sorted(holders))}, so nothing holds it.")
    fatal += [f"Nothing supports '{name}': the part does not touch it." for name, _, nodes_on in loads if not nodes_on]
    regions = {"supports": [{"name": f"{n} ({', '.join(sorted(item['touched_faces']))})",
                             "min": p[touching[n]].min(axis=0).tolist(), "max": p[touching[n]].max(axis=0).tolist()}
                            for n, item in zip(by_name, evidence) if n in holders and touching[n].any()],
               "loads": [{"name": n, "min": p[touching[n]].min(axis=0).tolist(), "max": p[touching[n]].max(axis=0).tolist(),
                          "force_n": list(force)} for n, force, nodes_on in loads if nodes_on]}
    return {"findings": findings, "fatal": fatal, "fixed": ids[fixed_mask].tolist(), "near_fixed": set(ids[near_mask].tolist()),
            "loads": loads, "regions": regions, "bodies": evidence}


LABELS = {"fit": "Leaves room for what it works with (no material inside the desk, bottle or screw)",
          "contact": "Touches what it attaches to and carries, on the faces that make it work",
          "insertion": "Everything can be put in place (clear insertion and tool paths)",
          "retention": "Holds what it must hold (blocked in every direction that matters)"}


def checks(result: dict) -> list[dict]:
    """One check per kind of evidence; details name the bodies, features and coordinates involved."""
    out = []
    for name, label in LABELS.items():
        items = result["findings"][name]
        out.append({"name": name, "label": label, "value": len(items), "limit": 0, "unit": "problems",
                    "passed": not items, "owner": "mechanics", "status": "failed" if items else "passed",
                    **({"detail": " ".join(items)} if items else {})})
    return out


UNVERIFIED = {"name": "function", "label": "The part's function is checked against what it works with", "value": 1, "limit": 0,
              "unit": "unverified", "passed": False, "owner": "mechanics", "status": "unverified",
              "detail": "No reference bodies (desk, wall, bottle, screw...) are declared, so nothing shows that the part fits, "
                        "attaches or holds anything; strength results alone do not establish that it works."}
