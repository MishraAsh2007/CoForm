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
    import families
    fam = families.for_brief(brief)
    try:
        built = fam.build(spec, brief, cfg, d)
        paths = dict(built["parts"])
        import trimesh
        empty = [n for n, p in paths.items() if len(trimesh.load(p).vertices) == 0]
        if empty:
            raise ValueError(f"empty geometry for {empty}")
    except Exception as e:  # a spec that cannot be built is a failed evaluation, not a crash
        bad = {"passed": False, "failures": [{"code": "build", "msg": f"geometry build failed: {e}"[:300],
                                              "amount_mm": 10.0}], "metrics": {}}
        results = {n: dict(bad, name=n) for n in fam.check_owners}
        out = {"key": k, "dir": str(d), "paths": {}, "pictures": {}, "results": results, "cached": False}
        f.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
        return out
    results = fam.checks(spec, brief, cfg, rules, built)
    pics = fam.render(built, d, cfg) if pictures else {}
    out = {"key": k, "dir": str(d), "paths": paths, "pictures": pics, "results": results, "cached": False,
           "family": fam.name, "experimental": fam.experimental}
    f.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


def real_preform(ev, cfg):
    """Real PreForm on an evaluated design (on request, and for every final design). Cached in the
    evaluation folder, so the same spec never goes through PreForm twice. Falls back to MOCK (labelled)
    if the server is down."""
    d = Path(ev["dir"])
    f = d / "preform_real.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    if not ev.get("paths"):
        return {"name": "preform", "passed": False, "failures": [{"code": "build", "msg": "no geometry",
                                                                   "amount_mm": 10.0}], "mocked": True}
    r = preform_check.check_parts(ev["paths"], cfg, out_dir=d, force_mock=False)
    r.pop("parts", None)
    if not r.get("mocked"):
        f.write_text(json.dumps(r, indent=1, default=str), encoding="utf-8")
    return r


def summary(results):
    """One line per check: pass/fail and failure count."""
    return {n: {"passed": r["passed"], "failures": len(r["failures"])} for n, r in results.items()}
