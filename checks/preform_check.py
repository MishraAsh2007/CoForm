"""PreForm check (owned by the Manufacturing agent).

check(stl_path, cfg=None, form_path=None) -> {print_time_s, volume_ml, warnings, passed, form_path, mocked}

Real mode: PreFormServer via checks/preform_client.py (the teammate's client). Needs PreFormServer
running and preform.machine_type / material_code set in config.yaml.
Mock mode: used automatically when the server is down or codes are missing. Every mocked result has
mocked=True, a "MOCK:" prefix on every warning, and mock_reason. Mock numbers are rough guesses from
the mesh (volume, height); they are NOT PreForm results.
"""
import math
from pathlib import Path

from cad.common import load_config, print_settings


class server_lock:
    """One PreForm job at a time across ALL processes on this PC (parallel benchmark workers)."""

    def __init__(self, path=None):
        from cad.common import ROOT
        self.path = Path(path or ROOT / "results" / "cache" / "preform.lock")

    def __enter__(self):
        import msvcrt
        import time
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a+")
        while True:
            try:
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError:
                time.sleep(1.0)

    def __exit__(self, *exc):
        import msvcrt
        self.f.seek(0)
        msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        self.f.close()


def preform_settings(cfg):
    """PreForm client settings: server options from `preform:`, printer/material/layer from `print:`."""
    return {**cfg["preform"], **print_settings(cfg)}


def check(stl_path, cfg=None, form_path=None, force_mock=False):
    cfg = cfg or load_config()
    pf = preform_settings(cfg)
    reason = None
    if force_mock:
        reason = "mock requested (--mock)"
    elif not pf.get("machine_type") or not pf.get("material_code"):
        reason = "preform.machine_type / material_code not set in config.yaml"
    else:
        from checks.preform_client import PreFormClient
        client = PreFormClient(pf)
        if not client.is_up():
            reason = f"PreFormServer not reachable at {pf['base_url']}"
        else:
            with server_lock():
                out = client.evaluate(stl_path, form_path)
            out["mocked"] = False
            out["print_settings"] = print_settings(cfg)
            return _as_check(out)
    out = mock(stl_path, pf, reason)
    out["print_settings"] = print_settings(cfg)
    return _as_check(out)


def mock(stl_path, pf, reason):
    import trimesh
    m = pf["mock"]
    mesh = trimesh.load(str(stl_path))
    warnings = [{"type": "mock", "description": f"MOCK: not a PreForm result ({reason})"}]
    if not mesh.is_watertight:
        warnings.append({"type": "not_manifold", "description": "MOCK: mesh is not watertight (trimesh)"})
    ext = sorted(mesh.extents)
    vol = sorted(m["build_volume_mm"])
    if any(e > v for e, v in zip(ext, vol)):
        warnings.append({"type": "out_of_bounds", "description": f"MOCK: part {ext} larger than build volume {vol}"})
    # height after a typical tilt: the longest side leaning at tilt_deg
    tilt = math.radians(m["tilt_deg"])
    height = ext[2] * math.sin(tilt) + ext[1] * math.cos(tilt)
    layers = height / pf["layer_thickness_mm"]
    real_warnings = [w for w in warnings if w["type"] != "mock"]
    return {"print_time_s": round(layers * m["seconds_per_layer"]),
            "volume_ml": round(abs(mesh.volume) / 1000 * m["support_volume_factor"], 2),
            "warnings": warnings, "passed": not real_warnings, "form_path": None,
            "mocked": True, "mock_reason": reason}


def _as_check(out):
    """Same dict, plus the common check fields (name, failures) the board uses."""
    fails = [{"code": w["type"], "msg": w["description"], "amount_mm": 0.0}
             for w in out["warnings"] if w["type"] != "mock"]
    out.update({"name": "preform", "failures": fails,
                "metrics": {"print_time_s": out["print_time_s"], "volume_ml": out["volume_ml"],
                            "mocked": out["mocked"]}})
    return out


def check_parts(paths, cfg=None, out_dir=None, force_mock=False):
    """Run check() on every part and merge into one result (failures prefixed with the part name)."""
    results = {}
    for name, p in paths.items():
        form = str(Path(out_dir) / f"{name}.form") if out_dir else None
        results[name] = check(p, cfg, form, force_mock)
    fails = [{**f, "msg": f"{n}: {f['msg']}"} for n, r in results.items() for f in r["failures"]]
    mocked = any(r["mocked"] for r in results.values())
    return {"name": "preform", "passed": not fails, "failures": fails, "mocked": mocked,
            "mock_reason": next((r.get("mock_reason") for r in results.values() if r.get("mock_reason")), None),
            "print_settings": print_settings(cfg or load_config()),
            "form_paths": {n: r.get("form_path") for n, r in results.items()},
            "metrics": {"print_time_s": sum(r["print_time_s"] or 0 for r in results.values()),
                        "volume_ml": round(sum(r["volume_ml"] or 0 for r in results.values()), 2),
                        "warnings": sum(len(r["failures"]) for r in results.values()),
                        "mocked": mocked},
            "parts": results}
