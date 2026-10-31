"""EXPERIMENTAL free-form family: the Design agent writes CadQuery code for any part from a text description.
Only the general checks run: PreForm, wall thickness, interference between parts, fit clearances on mating
pairs. Every result from this family is labelled "experimental".

Spec: {"family": "freeform", "name", "description", "code", "mating_pairs": [["a", "b"], ...]}
"""
import json
import math
import subprocess
import sys
from pathlib import Path

import jsonschema

from cad.common import ROOT
from families.base import PartFamily

SCHEMA = {"type": "object", "required": ["family", "name", "description", "code", "mating_pairs"],
          "additionalProperties": False,
          "properties": {"family": {"const": "freeform"}, "name": {"type": "string"},
                         "description": {"type": "string"}, "code": {"type": "string"},
                         "mating_pairs": {"type": "array", "items": {"type": "array", "items": {"type": "string"},
                                                                     "minItems": 2, "maxItems": 2}}}}

CODE_RULES = ("Write CadQuery Python code. `cq` and `math` are available; import nothing else. Define "
              "parts = {'name': cq.Workplane, ...} with every part in its ASSEMBLED position, units mm.")


class FreeformFamily(PartFamily):
    name = "freeform"
    experimental = True
    key_owner = {"code": "design", "description": "design", "name": "design", "family": "design",
                 "mating_pairs": "fit"}
    check_owners = {"printability": "manufacturing", "preform": "manufacturing", "interference": "assembly",
                    "fit": "fit"}

    def default_spec(self, brief, rules, cfg, start="naive"):
        return {"family": "freeform", "name": brief["name"], "description": brief["description"],
                "code": brief.get("start_code", ""), "mating_pairs": brief.get("mating_pairs", [])}

    def validate(self, spec):
        try:
            jsonschema.validate(spec, SCHEMA)
        except jsonschema.ValidationError as e:
            raise ValueError(f"free-form spec invalid: {e.message}") from None
        return spec

    def build(self, spec, brief, cfg, out_dir, timeout_s=120):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        code = out / "part_code.py"
        code.write_text(spec["code"], encoding="utf-8")
        if not spec["code"].strip():
            raise ValueError("no code yet: the design agent must write CadQuery code")
        # -I: isolated mode (no env vars, no user site, script dir not trusted beyond the executor itself)
        r = subprocess.run([sys.executable, "-I", str(ROOT / "families" / "freeform_exec.py"), str(code), str(out)],
                           cwd=str(out), capture_output=True, text=True, timeout=timeout_s)
        if r.returncode != 0:
            raise ValueError(f"code failed: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'unknown'}")
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        return {"parts": {n: m["stl"] for n, m in manifest.items()}, "steps": {n: m["step"] for n, m in manifest.items()},
                "meta": {"experimental": True}}

    def checks(self, spec, brief, cfg, rules, built):
        import cadquery as cq
        import trimesh
        from checks.geom import overlap
        from checks.preform_check import check_parts
        from checks.printability import mesh_thickness_p1
        v = rules["values"]
        solids = {n: cq.importers.importStep(p).val() for n, p in built["steps"].items()}
        # printability: closed meshes, thin regions
        pf = []
        for n, p in built["parts"].items():
            m = trimesh.load(p)
            if not m.is_watertight:
                pf.append({"code": "mesh", "msg": f"{n}: mesh not closed", "amount_mm": 1.0, "target": n})
            t1 = mesh_thickness_p1(p)
            if t1 is not None and t1 < v["min_supported_wall"]:
                pf.append({"code": "mesh_thin", "msg": f"{n}: 1% of surface thinner than {t1:.2f} mm "
                                                      f"(min {v['min_supported_wall']})",
                           "amount_mm": round(v["min_supported_wall"] - t1, 3), "target": n})
        # interference: no two parts may overlap
        inter, names = [], sorted(solids)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                d = overlap(solids[a], solids[b])
                if d > 0:
                    inter.append({"code": "interference", "msg": f"{a} and {b} overlap by {d} mm", "amount_mm": d,
                                  "target": f"{a}|{b}"})
        # fit: mating pairs need at least the clearance rule
        fit, gaps = [], {}
        for a, b in spec["mating_pairs"]:
            if a not in solids or b not in solids:
                fit.append({"code": "fit", "msg": f"mating pair {a}/{b}: part missing", "amount_mm": 1.0,
                            "target": f"{a}|{b}"})
                continue
            gap = round(solids[a].distance(solids[b]), 3)
            gaps[f"{a}|{b}"] = gap
            if gap < v["part_gap"] - 1e-6:
                fit.append({"code": "clearance", "msg": f"{a}/{b} clearance {gap} mm < {v['part_gap']} "
                                                        f"({rules['source']} rule)",
                            "amount_mm": round(v["part_gap"] - gap, 3), "target": f"{a}|{b}"})
        pre = check_parts(built["parts"], cfg, force_mock=True)
        pre.pop("parts", None)
        tag = {"experimental": True}
        return {"printability": {"name": "printability", "passed": not pf, "failures": pf, "metrics": tag},
                "interference": {"name": "interference", "passed": not inter, "failures": inter, "metrics": tag},
                "fit": {"name": "fit", "passed": not fit, "failures": fit, "metrics": {**tag, "gaps_mm": gaps}},
                "preform": pre}

    def quality(self, spec, ev, rules, cfg, brief, real_preform=None):
        import trimesh
        q = cfg["quality"]
        vol, ext = 0.0, [0.0, 0.0, 0.0]
        for p in ev.get("paths", {}).values():
            m = trimesh.load(p)
            vol += abs(m.volume) / 1000
            ext = [max(a, b) for a, b in zip(ext, sorted(m.extents))]
        tilt = math.radians(q["tilt_deg"])
        height = ext[2] * math.sin(tilt) + ext[1] * math.cos(tilt)
        gaps = ev["results"].get("fit", {}).get("metrics", {}).get("gaps_mm", {})
        margin = max(0.0, min([g - rules["values"]["part_gap"] for g in gaps.values()] or [0.0]))
        margin = min(margin, q["clearance_margin_cap_mm"])
        warnings = len(real_preform.get("failures", [])) if real_preform else None
        score = (q["base"] - q["w_volume"] * vol - q["w_height"] * height - q["w_warning"] * (warnings or 0)
                 + q["w_clearance"] * margin)
        return {"score": round(score, 2), "volume_ml": round(vol, 2), "print_height_mm": round(height, 1),
                "preform_warnings": warnings, "clearance_margin_mm": round(margin, 3), "experimental": True}

    def render(self, built, out_dir, cfg):
        from cad.render import render
        p = render(list(built["parts"].values()), Path(out_dir) / "assembled.png", title="EXPERIMENTAL free-form")
        return {"assembled": str(p)}

    def context(self, spec, brief):
        return {"family": "freeform (EXPERIMENTAL)", "description": brief["description"],
                "mating_pairs": brief.get("mating_pairs", []), "code_rules": CODE_RULES}
