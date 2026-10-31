"""Enclosure family: the board enclosure (spec schema, generator, assembly check) behind the family interface."""
from pathlib import Path

from cad.common import export_stl
from cad.components import load_bundle
from families.base import PartFamily


class EnclosureFamily(PartFamily):
    name = "enclosure"
    experimental = False
    key_owner = {
        "box": "assembly", "ports": "assembly", "cable_slots": "assembly", "mount_tabs": "assembly",
        "lid_screws": "assembly",
        "walls": "manufacturing", "ribs": "manufacturing", "vents": "manufacturing", "drain_holes": "manufacturing",
        "clearances": "fit", "fasteners": "fit", "standoffs": "fit", "lid": "fit",
        "name": "design", "bundle": "design", "clearance_source": "fit",
    }
    check_owners = {"assembly": "assembly", "printability": "manufacturing", "preform": "manufacturing", "fit": "fit"}

    def default_spec(self, brief, rules, cfg, start="naive"):
        from cad.spec import default_spec
        return default_spec(brief["bundle"], rules, cfg, start=start)

    def validate(self, spec):
        from cad.spec import validate
        return validate(spec)

    def build(self, spec, brief, cfg, out_dir):
        from cad.enclosure import build
        built = build(spec)
        out = Path(out_dir)
        built["parts"] = {"base": str(export_stl(built["base"], out / "base.stl", cfg)),
                          "lid": str(export_stl(built["lid"], out / "lid.stl", cfg))}
        return built

    def checks(self, spec, brief, cfg, rules, built):
        from checks import assembly, fit_rules, preform_check, printability
        res = {"assembly": assembly.check(spec, brief, cfg, built),
               "printability": printability.check(spec, cfg, rules, built, mesh_paths=built["parts"]),
               "preform": preform_check.check_parts(built["parts"], cfg, force_mock=True),
               "fit": fit_rules.check(spec, rules)}
        res["preform"].pop("parts", None)
        return res

    def quality(self, spec, ev, rules, cfg, brief, real_preform=None):
        from core.quality import quality
        return quality(spec, ev, rules, cfg, brief, real_preform)

    def hint(self, check, failure, spec, ctx):
        from agents.mock_policy import fix
        return fix(check, failure, spec, ctx)

    def render(self, built, out_dir, cfg):
        from cad.enclosure import render_views
        return {k: str(v) for k, v in render_views(built, out_dir, cfg).items()}

    def context(self, spec, brief):
        comp = load_bundle(brief["bundle"])
        return {"name": comp["name"], "board": comp["board"],
                "ports": [{"name": p["name"], "side": p["side"]} for p in comp["ports"]],
                "cable_access": [{"name": a["name"], "side": a["side"]} for a in comp["cable_access"]]}
