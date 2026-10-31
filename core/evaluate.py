"""One full evaluation = build the spec, export STLs, run every check, render a picture.

Results are cached by a hash of (spec, brief, rules, mock flag), so the same design is never
evaluated twice, across modes and seeds. The budget still counts every evaluation an agent asks for.
"""
import hashlib
import json
from pathlib import Path

from cad.common import ROOT
from cad.enclosure import build, render_views
from cad.common import export_stl
from checks import assembly, fit_rules, preform_check, printability


def key(spec, brief, rules, mock_preform):
    from cad.common import load_config, print_settings
    blob = json.dumps({"spec": spec, "brief": brief, "rules": rules["values"], "src": rules["source"],
                       "mock": mock_preform, "print": print_settings(load_config())}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def evaluate(spec, brief, cfg, rules, mock_preform=True, pictures=True):
    k = key(spec, brief, rules, mock_preform)
    d = ROOT / cfg["run"]["cache_dir"] / k
    f = d / "results.json"
    if f.exists():
        out = json.loads(f.read_text(encoding="utf-8"))
        out["cached"] = True
        return out
    d.mkdir(parents=True, exist_ok=True)
    built = build(spec)
    paths = {"base": str(export_stl(built["base"], d / "base.stl", cfg)),
             "lid": str(export_stl(built["lid"], d / "lid.stl", cfg))}
    results = {
        "assembly": assembly.check(spec, brief, cfg, built),
        "printability": printability.check(spec, cfg, rules, built, mesh_paths=paths),
        "preform": preform_check.check_parts(paths, cfg, out_dir=d, force_mock=mock_preform),
        "fit": fit_rules.check(spec, rules),
    }
    results["preform"].pop("parts", None)
    pics = {}
    if pictures:
        pics = {n: str(p) for n, p in render_views(built, d, cfg).items()}
    out = {"key": k, "dir": str(d), "paths": paths, "pictures": pics, "results": results, "cached": False}
    f.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


def summary(results):
    """One line per check: pass/fail and failure count."""
    return {n: {"passed": r["passed"], "failures": len(r["failures"])} for n, r in results.items()}
