# Connects to PreForm, imports geometry, prepares it, and retrieves manufacturing feedback.
#
# Requires PreForm with its API server running (File -> Start PreForm Server).
# Rules:
#   - Status before verdict: each check is recorded completed/failed/not_evaluated
#     first. A check that errored, or could not analyze the model (e.g. thin-wall
#     status other than ANALYZED, which still returns has_thin_walls=false),
#     never counts as a pass.
#   - Only scenes created here are touched, and each is deleted afterwards; the
#     .form file is the durable record (scene ids die with the server session).
#   - There is deliberately no print submission here. Sending a job to a printer
#     must be a separate, human-confirmed action.
import contextlib

from formlabs_preform_api.models import EulerAngles, ImportUnits
from formlabs_sdk import FormlabsClient

import config
from schemas import CheckResult, PrintabilityResult, PrintSettings, Quantity

CHECKS = ("minima", "supportedness", "thin_features", "cups",
          "interferences", "manifoldness", "in_bounds")


@contextlib.contextmanager
def connect():
    """Yield a PreForm API handle attached to the running local server."""
    with FormlabsClient() as client:
        yield client.preform.connect(port=config.PREFORM_PORT)


def _d(response):
    return response.to_dict() if hasattr(response, "to_dict") else response


def scene_settings():
    return {"machine_type": config.PRINTER_MACHINE_TYPE,
            "material_code": config.PRINTER_MATERIAL_CODE,
            "layer_thickness_mm": config.LAYER_THICKNESS_MM,
            "print_setting": config.PRINT_SETTING_PROFILE}


def _per_model(response, model_id):
    results = _d(response).get("per_model_results", {})
    if model_id not in results:
        raise RuntimeError(f"no result returned for model {model_id}")
    return results[model_id]


def _evaluate_check(preform, name, scene_id, model_id):
    """Run one check; return (status, passed, details)."""
    if name == "minima":
        r = _per_model(preform.validate_minima(scene_id=scene_id), model_id)
        return "completed", r["minima_count"] == 0, r
    if name == "supportedness":
        r = _per_model(preform.validate_supportedness(scene_id=scene_id), model_id)
        return ("completed", r["percent_unsupported"] <= config.MAX_UNSUPPORTED_PERCENT,
                {**r, "max_unsupported_percent": config.MAX_UNSUPPORTED_PERCENT})
    if name == "thin_features":
        r = _per_model(preform.validate_thin_features(
            scene_id=scene_id, threshold_mm=config.THIN_WALL_THRESHOLD_MM), model_id)
        details = {"status": r["status"], "has_thin_walls": r["has_thin_walls"],
                   "thin_volume_mm3": r["thin_volume_mm3"],
                   "thin_region_count": r["thin_region_count"],
                   "threshold_mm": config.THIN_WALL_THRESHOLD_MM}
        if r["status"] != "ANALYZED":
            return "failed", None, details
        return "completed", not r["has_thin_walls"], details
    if name == "cups":
        r = _per_model(preform.validate_cups(scene_id=scene_id), model_id)
        return "completed", r["cup_count"] == 0, r
    if name == "interferences":
        pairs = _d(preform.validate_interferences(scene_id=scene_id))["model_ids"]
        return "completed", not pairs, {"colliding_pairs": pairs}
    if name == "manifoldness":
        r = _per_model(preform.validate_manifoldness(scene_id=scene_id), model_id)
        return "completed", r["status"] == "MANIFOLD", r
    if name == "in_bounds":
        r = _per_model(preform.validate_in_bounds(scene_id=scene_id), model_id)
        return "completed", bool(r["in_bounds"]), r
    raise ValueError(f"unknown check {name}")


def run_checks(preform, scene_id, model_id):
    checks = {}
    for name in CHECKS:
        try:
            status, passed, details = _evaluate_check(preform, name, scene_id, model_id)
        except Exception as e:  # SDK / server errors are recorded, never treated as a pass
            status, passed, details = "failed", None, {"error": f"{type(e).__name__}: {e}"}
        checks[name] = CheckResult(name=name, status=status, passed=passed, details=details)
    return checks


def _quantity(fetch, unit):
    try:
        return Quantity(value=float(fetch()), unit=unit, source="preform", status="completed")
    except Exception as e:
        return Quantity(unit=unit, source="preform", status="failed", error=f"{type(e).__name__}: {e}")


def prepare_print(preform, stl_path, print_settings: PrintSettings, form_path=None,
                  name="bracket"):
    """Prepare one STL in a fresh scene, run every check, time it, and optionally save a .form.

    Returns a PrintabilityResult. Preparation failures are recorded in `error`
    with every check `not_evaluated`, rather than raised.
    """
    settings = scene_settings()
    scene_id = str(_d(preform.create_scene(**settings))["scene_id"])
    try:
        try:
            imported = _d(preform.import_models(scene_id=scene_id, file=str(stl_path),
                                                units=ImportUnits.MILLIMETERS, name=name))
            model_id = str(imported["new_models"][0]["model_id"])

            if print_settings.orientation_deg is None:
                preform.auto_orient_models(scene_id=scene_id, model_ids=[model_id])
            else:
                x, y, z = print_settings.orientation_deg
                preform.orient_models(scene_id=scene_id, model_ids=[model_id],
                                      orientation=EulerAngles(x=x, y=y, z=z))

            support = {k: v for k, v in (("density", print_settings.support_density),
                                         ("touchpoint_size_mm", print_settings.touchpoint_size_mm))
                       if v is not None}
            preform.auto_support_models(scene_id=scene_id, model_ids=[model_id],
                                        raft_label_enabled=False, **support)
            # Place on the platform without undoing the chosen orientation.
            preform.auto_layout_models(scene_id=scene_id, lock_rotation=True, seed=0)
        except Exception as e:
            return PrintabilityResult(
                print_settings=print_settings, scene_settings=settings,
                preform_version=preform.version, error=f"{type(e).__name__}: {e}",
                checks={n: CheckResult(name=n, status="not_evaluated") for n in CHECKS})

        checks = run_checks(preform, scene_id, model_id)
        print_time = _quantity(
            lambda: _d(preform.calculate_print_time(scene_id=scene_id))["total_print_time_s"], "s")
        resin_volume = _quantity(
            lambda: _d(preform.get_scene(scene_id=scene_id))["material_usage"]["volume_ml"], "mL")

        saved = None
        if form_path is not None:
            preform.save_form_file(file=str(form_path), scene_ids=[scene_id])
            saved = str(form_path)

        return PrintabilityResult(print_settings=print_settings, checks=checks,
                                  print_time=print_time, resin_volume=resin_volume,
                                  form_path=saved, scene_settings=settings,
                                  preform_version=preform.version)
    finally:
        preform.delete_scene(scene_id=scene_id)


if __name__ == "__main__":
    # Smoke test: prepare runs/smoke/c0/bracket.stl (make it with `python bracket.py`).
    import store

    stl, _ = store.geometry_paths("smoke", "c0")
    with connect() as preform:
        result = prepare_print(preform, stl, PrintSettings(), form_path=stl.with_suffix(".form"))
    print(result.model_dump_json(indent=2))
