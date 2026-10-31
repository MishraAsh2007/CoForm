"""Wall-thickness heat map (tool owned by the Manufacturing agent; NOT used in the benchmark).

heatmap(stl_path, out_png, cfg) -> {"png", "min_mm", "p1_mm", "thin_fraction"}
Local thickness at each mesh vertex = distance along the inward normal to the opposite surface (ray cast).
Colours run from red (thinner than the minimum wall rule) to blue (thick); the scale is fixed so pictures
of different designs compare directly.
"""
from pathlib import Path

import numpy as np
import pyvista as pv
import trimesh

pv.OFF_SCREEN = True


def vertex_thickness(mesh, max_mm=10.0):
    normals = mesh.vertex_normals
    origins = mesh.vertices - normals * 1e-3
    loc, idx, _ = mesh.ray.intersects_location(origins, -normals, multiple_hits=False)
    t = np.full(len(mesh.vertices), max_mm)
    if len(idx):
        d = np.linalg.norm(loc - origins[idx], axis=1)
        t[idx] = np.minimum(d, max_mm)
    return t


def heatmap(stl_path, out_png, cfg, min_wall=None, scale_max=4.0):
    mesh = trimesh.load(str(stl_path))
    # subdivide long faces so the colour field has enough vertices
    mesh = mesh.subdivide_to_size(max_edge=2.0, max_iter=4) if len(mesh.vertices) < 200000 else mesh
    t = vertex_thickness(mesh)
    min_wall = min_wall or cfg["clearances"]["generic"]["min_wall"]
    faces = np.hstack([np.full((len(mesh.faces), 1), 3), mesh.faces]).ravel()
    pm = pv.PolyData(mesh.vertices, faces)
    pm["thickness_mm"] = t
    pl = pv.Plotter(off_screen=True, window_size=(1200, 900))
    pl.set_background("white")
    pl.add_mesh(pm, scalars="thickness_mm", cmap="RdYlBu", clim=[0, scale_max],
                scalar_bar_args={"title": f"wall thickness (mm), red < {min_wall}", "color": "black"})
    pl.view_isometric()
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    pl.screenshot(str(out_png))
    pl.close()
    return {"png": str(out_png), "min_mm": round(float(t.min()), 3), "p1_mm": round(float(np.percentile(t, 1)), 3),
            "thin_fraction": round(float((t < min_wall).mean()), 4), "min_wall_rule_mm": min_wall}
