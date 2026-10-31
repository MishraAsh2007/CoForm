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


def print_settings(cfg):
    """The one printer + material + layer setting every print uses (config.yaml `print:`)."""
    p = cfg["print"]
    return {k: p[k] for k in ("printer", "machine_type", "material", "material_code", "layer_thickness_mm",
                              "print_setting")}


def write_print_info(out_dir, cfg, extra=None):
    """PRINT_SETTINGS.json next to every set of STLs, so each output says how it must be printed."""
    import json
    info = {**print_settings(cfg),
            "note": "Print with exactly this material and layer thickness: the fit-test results only apply to it.",
            **(extra or {})}
    p = Path(out_dir) / "PRINT_SETTINGS.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return p
