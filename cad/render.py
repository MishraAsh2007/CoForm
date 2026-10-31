"""Off-screen pictures of STL files with pyvista (works on Windows without a display)."""
from pathlib import Path

import pyvista as pv

pv.OFF_SCREEN = True
COLORS = ["#4a90d9", "#e0a030", "#5cb85c", "#d9534f", "#9b59b6", "#888888"]


def render(meshes, out_png, view="iso", size=(1200, 900), opacity=None, title=None):
    """meshes: list of STL paths or (path, color) or (path, color, opacity)."""
    pl = pv.Plotter(off_screen=True, window_size=size)
    pl.set_background("white")
    for i, m in enumerate(meshes):
        if isinstance(m, (str, Path)):
            m = (m, COLORS[i % len(COLORS)])
        path, color = m[0], m[1]
        op = m[2] if len(m) > 2 else (opacity or 1.0)
        mesh = path if isinstance(path, pv.DataSet) else pv.read(str(path))
        pl.add_mesh(mesh, color=color, opacity=op, smooth_shading=False, show_edges=False)
    {"iso": pl.view_isometric, "top": pl.view_xy, "front": pl.view_xz, "side": pl.view_yz}[view]()
    if title:
        pl.add_text(title, font_size=10, color="black")
    pl.enable_parallel_projection() if view != "iso" else None
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    pl.screenshot(str(out_png))
    pl.close()
    return Path(out_png)
