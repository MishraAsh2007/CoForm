# Copied unchanged from C:/Users/rkhmh/Downloads/Apidaengineers/preform/client.py (teammate's PreForm connection).
# Replace this file with their latest version; checks/preform_check.py only uses PreFormClient.evaluate() and is_up().
"""Thin wrapper over the PreFormServer Local API (v1.0) using requests.

Endpoint paths and body fields were taken from Formlabs' generated client
(formlabs_preform_api 1.0.0a0 for PreForm 3.64.2). In this API version the
scene_id goes in the JSON body, not the URL. Errors come back as
{"error": {"code": ..., "message": ...}}. Long operations accept ?async=true,
return an operation_id, and are polled at /v1/operation/get/.
"""
import threading
import time
from pathlib import Path

import requests

# One lock for the whole process: a scene is never touched from two threads at once.
_SERVER_LOCK = threading.RLock()


class PreFormError(RuntimeError):
    def __init__(self, code, message, status=None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.status = code, message, status


class PreFormClient:
    def __init__(self, cfg):
        """cfg: the 'preform' section of config.yaml."""
        self.cfg = cfg
        self.base = cfg["base_url"].rstrip("/")
        self.http = requests.Session()

    # ---------- low level ----------
    def _call(self, method, path, body=None, params=None, timeout=None):
        url = self.base + path
        r = self.http.request(method, url, json=body, params=params,
                              timeout=timeout or self.cfg["request_timeout_s"])
        data = r.json() if r.content else {}
        if r.status_code >= 400 or (isinstance(data, dict) and "error" in data and data["error"]):
            err = data.get("error", {}) if isinstance(data, dict) else {}
            raise PreFormError(err.get("code", f"HTTP_{r.status_code}"),
                               err.get("message", r.text[:200]), r.status_code)
        return data

    def _run(self, path, body):
        """POST with ?async=true and poll until done. Returns the operation's result dict."""
        data = self._call("POST", path, body, params={"async": "true"})
        op_id = data.get("operation_id") if isinstance(data, dict) else None
        if not op_id:
            return data  # server answered synchronously
        deadline = time.monotonic() + self.cfg["operation_timeout_s"]
        while time.monotonic() < deadline:
            # oneshot: the server deletes the record once we've read a finished status.
            op = self._call("GET", "/v1/operation/get/",
                            params={"operation_id": op_id, "mode": "oneshot"})
            status = op.get("status")
            result = op.get("result") or {}
            if status == "SUCCEEDED":
                return result
            if status == "FAILED":
                err = result.get("error") or {}
                raise PreFormError(err.get("code", "OPERATION_FAILED"), err.get("message", path))
            time.sleep(self.cfg["poll_interval_s"])
        raise TimeoutError(f"{path} operation {op_id} not finished after {self.cfg['operation_timeout_s']} s")

    # ---------- server ----------
    def is_up(self):
        try:
            self._call("GET", "/v1/", timeout=3)
            return True
        except (requests.RequestException, PreFormError, ValueError):
            return False

    def version(self):
        return self._call("GET", "/v1/")

    def list_materials(self):
        """Flat list of every (printer, material, setting) the server knows."""
        data = self._call("POST", "/v1/print-setting/list/", {})
        rows = []
        for printer in data.get("printer_types", []):
            for mat in printer.get("materials", []):
                for ms in mat.get("material_settings", []):
                    s = ms.get("scene_settings", {})
                    rows.append({
                        "printer": printer.get("label"),
                        "products": printer.get("supported_product_names"),
                        "machine_types": printer.get("supported_machine_type_ids"),
                        "material": mat.get("label"),
                        "setting": ms.get("label"),
                        "machine_type": s.get("machine_type"),
                        "material_code": s.get("material_code"),
                        "layer_thickness_mm": s.get("layer_thickness_mm"),
                        "print_setting": s.get("print_setting"),
                    })
        return rows

    # ---------- scenes ----------
    def list_scenes(self):
        data = self._call("GET", "/v1/scene/list/")
        return data.get("scenes", data) if isinstance(data, dict) else data

    def create_scene(self):
        c = self.cfg
        if not c["machine_type"] or not c["material_code"]:
            raise ValueError("preform.machine_type / material_code not set in config.yaml")
        scenes = self.list_scenes()
        if isinstance(scenes, list) and len(scenes) >= c["max_scenes"]:
            raise PreFormError("TOO_MANY_SCENES", f"{len(scenes)} scenes open on the server")
        body = {"machine_type": c["machine_type"], "material_code": c["material_code"],
                "layer_thickness_mm": c["layer_thickness_mm"], "print_setting": c["print_setting"]}
        return self._call("POST", "/v1/scene/create/", body)["scene_id"]

    def delete_scene(self, scene_id):
        self._call("DELETE", "/v1/scene/delete/", {"scene_id": scene_id})

    def import_stl(self, scene_id, stl_path):
        p = Path(stl_path)
        if not p.is_absolute():
            raise ValueError(f"PreForm needs a full absolute path, got {stl_path}")
        # REPAIR fixes small mesh defects instead of failing the import.
        res = self._run("/v1/scene/model/import/",
                        {"scene_id": scene_id, "file": str(p), "name": p.stem, "repair_behavior": "REPAIR"})
        return [m["model_id"] for m in res.get("new_models", [])]

    def auto_orient(self, scene_id):
        self._run("/v1/scene/model/auto-orient/", {"scene_id": scene_id})

    def auto_support(self, scene_id):
        self._run("/v1/scene/model/auto-support/", {"scene_id": scene_id})

    def auto_layout(self, scene_id):
        self._run("/v1/scene/model/auto-layout/", {"scene_id": scene_id})

    def thin_walls(self, scene_id):
        return self._run("/v1/scene/model/validate/thin-features/",
                         {"scene_id": scene_id, "threshold_mm": self.cfg["thin_wall_threshold_mm"]})

    def run_checks(self, scene_id):
        """Run every printability check. Returns a list of {type, description} warnings."""
        sid = {"scene_id": scene_id}
        warnings = []

        def per_model(res):
            return (res or {}).get("per_model_results", {}).items()

        for _, r in per_model(self._run("/v1/scene/model/validate/manifoldness/", sid)):
            if not r.get("is_manifold", True):
                warnings.append({"type": "not_manifold", "description": f"mesh not closed ({r.get('status')})"})
        bounds = self._call("POST", "/v1/scene/model/validate/in-bounds/", sid)
        if not bounds.get("all_in_bounds", True):
            warnings.append({"type": "out_of_bounds", "description": "part outside the build volume"})
        inter = self._call("POST", "/v1/scene/model/validate/interferences/", sid)
        if inter.get("model_ids"):
            warnings.append({"type": "interference", "description": f"{len(inter['model_ids'])} colliding pair(s)"})
        for _, r in per_model(self._run("/v1/scene/model/validate/cups/", sid)):
            if r.get("cup_count"):
                warnings.append({"type": "cups", "description": f"{r['cup_count']} cup(s) that trap resin"})
        for _, r in per_model(self._run("/v1/scene/model/validate/minima/", sid)):
            if r.get("minima_count"):
                warnings.append({"type": "unsupported_minima", "description": f"{r['minima_count']} unsupported minima"})
        for _, r in per_model(self._run("/v1/scene/model/validate/supportedness/", sid)):
            pct = r.get("percent_unsupported", 0)
            if pct > self.cfg["max_unsupported_percent"]:
                warnings.append({"type": "under_supported", "description": f"{pct:.1f}% of surface under-supported"})
        for _, r in per_model(self.thin_walls(scene_id)):
            if r.get("status") != "ANALYZED":
                warnings.append({"type": "thin_wall_unchecked", "description": f"thin-wall check: {r.get('status')}"})
            elif r.get("has_thin_walls"):
                warnings.append({"type": "thin_walls", "description":
                                 f"{r['thin_region_count']} region(s) < {self.cfg['thin_wall_threshold_mm']} mm, "
                                 f"{r['thin_volume_mm3']:.1f} mm3"})
        return warnings

    def print_time_s(self, scene_id):
        """Fast estimate; some printers don't support it, so fall back to the full calculation."""
        try:
            return self._call("POST", "/v1/scene/estimate-print-time/", {"scene_id": scene_id})["total_print_time_s"]
        except PreFormError as e:
            if e.code != "ESTIMATION_NOT_SUPPORTED":
                raise
            return self._run("/v1/scene/calculate-print-time/", {"scene_id": scene_id})["total_print_time_s"]

    def volume_ml(self, scene_id):
        """Resin volume including supports, from the scene's material usage."""
        scene = self._call("POST", "/v1/scene/get/", {"scene_id": scene_id})
        return scene.get("material_usage", {}).get("volume_ml")

    def save_form(self, scene_id, form_path):
        p = Path(form_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        self._run("/v1/scene/save/", {"file": str(p), "scene_ids": [scene_id]})
        return str(p)

    # ---------- one-shot ----------
    def evaluate(self, stl_path, form_path=None):
        """Full pipeline for one design in its own scene; the scene is always deleted after.

        Returns {print_time_s, volume_ml, warnings, passed, form_path}.
        """
        stl_path = str(Path(stl_path).resolve())
        with _SERVER_LOCK:
            scene_id = self.create_scene()
            try:
                if not self.import_stl(scene_id, stl_path):
                    raise PreFormError("IMPORT_EMPTY", f"no models imported from {stl_path}")
                self.auto_orient(scene_id)
                if self.cfg["layout_after_orient"]:
                    self.auto_layout(scene_id)
                self.auto_support(scene_id)
                warnings = self.run_checks(scene_id)
                out = {
                    "print_time_s": self.print_time_s(scene_id),
                    "volume_ml": self.volume_ml(scene_id),
                    "warnings": warnings,
                    "passed": not warnings,
                    "form_path": self.save_form(scene_id, form_path) if form_path else None,
                }
            finally:
                try:
                    self.delete_scene(scene_id)
                except Exception:
                    pass  # don't hide the real error; a leftover scene is cleared on server restart
        return out
