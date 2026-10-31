r"""Put several parts on ONE PreForm build platform and save a single .form, requiring zero warnings.

  .venv\Scripts\python.exe -m cad.print_job <out.form> <part1.stl> <part2.stl> ...

Strategy: (A) import all parts, PreForm auto-orient + layout + supports + every check. If any warning (cups,
supports, thin walls, out of bounds, interference...), (B) pick per part the orientation with zero warnings using
checks/orientations.py, import the parts pre-rotated (no auto-orient), layout + supports + checks again.
Uses the one `print:` setting (printer, material, layer). Writes <out>.json with what PreForm reported.
"""
import json
import sys
import tempfile
from pathlib import Path

import trimesh

from cad.common import load_config, print_settings
from checks.orientations import DEFAULT, _rotated, compare
from checks.preform_check import preform_settings, server_lock
from checks.preform_client import PreFormClient


def _scene(client, stls, orient):
    sid = client.create_scene()
    try:
        for p in stls:
            client.import_stl(sid, str(Path(p).resolve()))
        if orient:
            client.auto_orient(sid)
        client.auto_layout(sid)
        client.auto_support(sid)
        warns = client.run_checks(sid)
        return sid, warns
    except Exception:
        client.delete_scene(sid)
        raise


def make(out_form, stls, cfg=None, allow=()):
    """allow: warning types accepted ON PURPOSE (e.g. thin_walls for the tolerance plate's 0.4 mm test wall);
    they are recorded in the report, every other warning still fails."""
    allow = set(allow)

    def bad(ws):
        return [w for w in ws if w["type"] not in allow]

    cfg = cfg or load_config()
    client = PreFormClient(preform_settings(cfg))
    if not client.is_up():
        raise RuntimeError("PreFormServer not running: cannot make a real .form")
    out_form = Path(out_form).resolve()
    report = {"print_settings": print_settings(cfg), "parts": [str(p) for p in stls], "attempts": []}
    with server_lock():
        sid, warns = _scene(client, stls, orient=True)
        report["attempts"].append({"strategy": "auto-orient all", "warnings": warns})
        chosen = None
        if not bad(warns):
            chosen = sid
        else:
            client.delete_scene(sid)
    if chosen is None:
        tmp = Path(tempfile.mkdtemp(prefix="printjob_"))
        rotated = []
        for p in stls:
            rows = compare(p, cfg, candidates=[c for c in DEFAULT if c[1] is not None])
            ok = [r for r in rows if not r["mocked"] and not [t for t in r.get("warning_types", ["?"] * len(r["warnings"]))
                                                               if t not in allow]]
            report.setdefault("orientation_compare", {})[Path(p).name] = rows
            if not ok:
                raise RuntimeError(f"{Path(p).name}: no candidate orientation without PreForm warnings: "
                                   f"{[(r['name'], r['warnings']) for r in rows]}")
            best = min(ok, key=lambda r: (r["support_ml"], r["print_time_s"] or 0))
            rot = dict(DEFAULT)[best["name"]]
            q = tmp / f"{Path(p).stem}.stl"
            _rotated(trimesh.load(str(p)), rot).export(q)
            rotated.append(q)
            report.setdefault("orientation_chosen", {})[Path(p).name] = best["name"]
        with server_lock():
            sid, warns = _scene(client, rotated, orient=False)
            report["attempts"].append({"strategy": "fixed per-part orientations", "warnings": warns})
            if bad(warns):
                client.delete_scene(sid)
                raise RuntimeError(f"PreForm still reports warnings: {warns}")
            chosen = sid
    with server_lock():
        try:
            report["print_time_s"] = client.print_time_s(chosen)
            report["volume_ml"] = client.volume_ml(chosen)
            report["form_path"] = client.save_form(chosen, out_form)
            report["warnings"] = [w for w in report["attempts"][-1]["warnings"]]
            report["accepted_on_purpose"] = sorted(allow)
        finally:
            client.delete_scene(chosen)
    out_form.with_suffix(".json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


if __name__ == "__main__":
    args = sys.argv[1:]
    allow = []
    if args and args[0].startswith("--allow="):
        allow = args.pop(0).split("=", 1)[1].split(",")
    r = make(args[0], args[1:], allow=allow)
    print(json.dumps({k: r[k] for k in ("form_path", "print_time_s", "volume_ml", "warnings")}, indent=1))
    print("attempts:", [(a["strategy"], a["warnings"]) for a in r["attempts"]])
