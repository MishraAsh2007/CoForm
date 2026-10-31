"""Small helpers shared by every CAD script: config, labels, STL export."""
from pathlib import Path

import cadquery as cq
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(path=None):
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def engrave(part, text, x, y, z_top, cfg, size=None):
    """Cut text into the top face at height z_top, centered on (x, y)."""
    lab = cfg["labels"]
    depth = lab["depth_mm"]
    txt = (cq.Workplane("XY").workplane(offset=z_top - depth)
           .center(x, y)
           .text(text, size or lab["size_mm"], depth + 0.2, font=lab["font"], kind=lab.get("kind", "regular"),
                 halign="center", valign="center", combine=False))
    return part.cut(txt)


def export_stl(part, path, cfg):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    e = cfg["export"]
    cq.exporters.export(part, str(path), tolerance=e["stl_tolerance_mm"],
                        angularTolerance=e["stl_angular_tolerance_rad"])
    return path
