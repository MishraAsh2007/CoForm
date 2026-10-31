"""Compare print orientations in PreForm (tool owned by the Manufacturing agent; NOT used in the benchmark).

compare(stl_path, cfg, candidates=None) -> list of {name, mocked, support_ml, total_ml, part_ml, cups, warnings,
print_time_s}. Each candidate is a rotation (degrees about x, y) applied to the STL before import; "auto" uses
PreForm's auto-orient. Supports, layout and checks are done by PreForm. If PreFormServer is down every row is a
labelled MOCK estimate (support volume guessed from overhang area).
"""
import math
import tempfile
from pathlib import Path

import numpy as np
import trimesh

from checks.preform_check import preform_settings, server_lock

DEFAULT = [("auto", None), ("flat", (0, 0)), ("tilt_30_x", (30, 0)), ("tilt_45_y", (0, 45))]


def _rotated(mesh, rot):
    m = mesh.copy()
    if rot:
        rx, ry = (math.radians(a) for a in rot)
        m.apply_transform(trimesh.transformations.rotation_matrix(rx, [1, 0, 0]))
        m.apply_transform(trimesh.transformations.rotation_matrix(ry, [0, 1, 0]))
    m.apply_translation(-m.bounds[0])
    return m


def _mock_row(name, m, reason):
    nz = m.face_normals[:, 2]
    down = nz < -0.5                                       # faces pointing at the build plate
    overhang = float((m.area_faces[down] * -nz[down]).sum())   # projected (support-carrying) area
    part = abs(m.volume) / 1000
    return {"name": name, "mocked": True, "mock_reason": reason, "part_ml": round(part, 2),
            "support_ml": round(overhang * 0.002, 2), "total_ml": round(part + overhang * 0.002, 2),
            "cups": None, "warnings": ["MOCK: not a PreForm result"],
            "print_time_s": round(m.extents[2] / 0.15 * 6)}


def compare(stl_path, cfg, candidates=None):
    from checks.preform_client import PreFormClient
    candidates = candidates or DEFAULT
    mesh = trimesh.load(str(stl_path))
    pf = preform_settings(cfg)
    client = PreFormClient(pf)
    up = client.is_up()
    rows = []
    tmp = Path(tempfile.mkdtemp(prefix="orient_"))
    for name, rot in candidates:
        m = _rotated(mesh, rot)
        if not up:
            rows.append(_mock_row(name, m, f"PreFormServer not reachable at {pf['base_url']}"))
            continue
        p = tmp / f"{name}.stl"
        m.export(p)
        with server_lock():
            sid = client.create_scene()
            try:
                client.import_stl(sid, str(p.resolve()))
                if rot is None:
                    client.auto_orient(sid)
                client.auto_layout(sid)
                client.auto_support(sid)
                warns = client.run_checks(sid)
                total = client.volume_ml(sid)
                t = client.print_time_s(sid)
            finally:
                try:
                    client.delete_scene(sid)
                except Exception:
                    pass
        part = abs(m.volume) / 1000
        cups = sum(int(w["description"].split()[0]) for w in warns if w["type"] == "cups")
        rows.append({"name": name, "mocked": False, "part_ml": round(part, 2), "total_ml": round(total or 0, 2),
                     "support_ml": round((total or 0) - part, 2), "cups": cups,
                     "warnings": [w["description"] for w in warns], "warning_types": [w["type"] for w in warns],
                     "print_time_s": t})
    return rows


def best(rows):
    """Fewest cups, then least support, then shortest print."""
    ok = [r for r in rows if not r["mocked"]] or rows
    return min(ok, key=lambda r: ((r["cups"] or 0), r["support_ml"], r["print_time_s"] or 0))
