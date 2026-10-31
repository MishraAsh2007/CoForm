"""Traceable preparation through the official Formlabs SDK; never starts a printer.

Geometry comes from CAD. This adapter imports millimeter STL files, records actual
PreForm checks, saves a project, and uploads an explicitly reviewed snapshot.
"""
from contextlib import contextmanager
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import re
import struct
import threading

from pydantic import Field

from .domain import Record, identifier, utc_now


MAX_STL_BYTES = 25 * 1024 * 1024


class FormlabsError(ValueError):
    pass


class PrintProfile(Record):
    machine_type: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=64)
    material_code: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=64)
    layer_thickness_mm: float = Field(gt=0, le=1)
    print_setting: str = Field(default="DEFAULT", min_length=1, max_length=64)
    minimum_wall_mm: float = Field(gt=0, le=10)

    def scene_settings(self):
        return self.model_dump(exclude={"minimum_wall_mm"})


def _plain(value):
    return value.to_dict() if hasattr(value, "to_dict") else value


def _hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_stl(raw):
    """Bound input and check syntax/finite coordinates; PreForm checks topology."""
    if not 15 <= len(raw) <= MAX_STL_BYTES:
        raise ValueError("Upload an STL between 15 bytes and 25 MiB, exported in millimeters")
    count = struct.unpack_from("<I", raw, 80)[0] if len(raw) >= 84 else 0
    if count > 0 and len(raw) == 84 + count * 50:
        for offset in range(84, len(raw), 50):
            if not all(math.isfinite(v) for v in struct.unpack_from("<12f", raw, offset)):
                raise ValueError("STL contains nonfinite coordinates")
        return
    try:
        content = raw.decode("ascii").strip()
        if not content.startswith("solid") or "endsolid" not in content:
            raise ValueError
        vertices = re.findall(r"(?m)^\s*vertex\s+(\S+)\s+(\S+)\s+(\S+)\s*$", content)
        if not vertices or len(vertices) % 3 or any(not math.isfinite(float(v)) for row in vertices for v in row):
            raise ValueError
    except (UnicodeError, ValueError):
        raise ValueError("Invalid STL; export an ASCII or binary STL from CAD") from None


@contextmanager
def preform_connection():
    try:
        from formlabs_sdk import FormlabsClient, FormlabsError as SDKError
    except ImportError:
        raise FormlabsError("Formlabs SDK missing; install the project's [formlabs] extra") from None
    port = int(os.getenv("FORMLABS_PREFORM_PORT", "44388"))
    if not 1 <= port <= 65535:
        raise FormlabsError("FORMLABS_PREFORM_PORT must be between 1 and 65535")
    try:
        with FormlabsClient() as client:
            # Connect locally and preserve the account already signed in to PreForm.
            with client.preform.connect(port=port, timeout=180) as preform:
                yield preform
    except SDKError as exc:
        code = re.sub(r"[^A-Z0-9_]", "", str(exc.code))[:80]
        hint = "Open PreForm Insider, choose File > Start PreForm Server, then retry." if code == "SERVER_UNREACHABLE" else "Inspect the scene in PreForm; the operation was not retried."
        raise FormlabsError(f"PreForm {code}. {hint}") from None


def inspect_scene(preform, scene_id, model_ids, minimum_wall_mm):
    """Missing or incomplete check results fail closed, rather than claiming PASS."""
    checks, issues, warnings = {}, [], []
    for name in ("manifoldness", "in_bounds", "thin_features", "cups", "minima", "supportedness", "interferences"):
        kwargs = {"scene_id": scene_id}
        if name == "thin_features":
            kwargs["threshold_mm"] = minimum_wall_mm
        if name not in ("in_bounds", "interferences"):
            kwargs["timeout"] = 180
        try:
            checks[name] = _plain(getattr(preform, "validate_" + name)(**kwargs))
        except FormlabsError:
            raise
        except Exception:
            # Failure is retained as an incomplete check; no silent PASS.
            checks[name] = {"error": "check_failed"}
        result = checks[name]
        if name == "interferences":
            if not isinstance(result.get("model_ids"), list):
                issues.append("Collision check incomplete")
            elif result["model_ids"]:
                issues.append("Models or supports collide")
            continue
        rows = result.get("per_model_results", {})
        if not model_ids or set(rows) != set(model_ids):
            issues.append(f"{name}: missing or unexpected model results")
        for model_id in model_ids:
            row = rows.get(model_id, {})
            prefix = f"{name}/{model_id}: "
            if name == "manifoldness" and (row.get("is_manifold") is not True or row.get("status") != "MANIFOLD"):
                issues.append(prefix + "mesh is not verified manifold")
            if name == "in_bounds" and row.get("in_bounds") is not True:
                issues.append(prefix + "outside build volume or not checked")
            if name == "thin_features":
                if row.get("status") != "ANALYZED" or type(row.get("has_thin_walls")) is not bool:
                    issues.append(prefix + "thin walls could not be assessed")
                elif row["has_thin_walls"]:
                    warnings.append(prefix + "walls below the selected threshold")
            if name in ("cups", "minima", "supportedness"):
                field = {"cups":"cup_count", "minima":"minima_count", "supportedness":"percent_unsupported"}[name]
                value = row.get(field)
                if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                    issues.append(prefix + "analysis incomplete")
                elif value > 0:
                    warnings.append(prefix + f"{field}={value}")
    return {"grade": "FAIL" if issues else "WARN" if warnings else "PASS", "issues": issues,
            "warnings": warnings, "checks": checks, "source": "preform_api"}


class Manufacturing:
    def __init__(self, store, connector=None):
        self.store = store
        self.connect = connector or preform_connection
        self.lock = threading.RLock()
        self.directory = store.directory / "formlabs"
        self.directory.mkdir(exist_ok=True)

    def status(self):
        try:
            with self.connect() as pf:
                return {"connected": True, "version": str(pf.version), "devices": _plain(pf.list_devices(can_print=True)).get("devices", [])}
        except FormlabsError as exc:
            return {"connected": False, "sdk_installed": importlib.util.find_spec("formlabs_sdk") is not None, "detail": str(exc), "devices": []}

    def catalog(self, printer=None):
        with self.connect() as pf:
            return _plain(pf.list_print_settings(**({"compatible_with": printer} if printer else {})))

    def add_asset(self, filename, raw, *, run_id="manufacturing", candidate_id=None, source=None):
        if not filename.lower().endswith(".stl"):
            raise ValueError("Export the Onshape part as an STL in millimeters")
        validate_stl(raw)
        sha = hashlib.sha256(raw).hexdigest()
        dest = self.directory / (sha + ".stl")
        if not dest.exists():
            dest.write_bytes(raw)
        asset = {"id": identifier("mesh"), "created_at": utc_now(), "run_id": run_id, "candidate_id": candidate_id,
                 "label": filename.replace("\\", "/").split("/")[-1][:200], "filename": dest.name,
                 "sha256": sha, "bytes": len(raw), "units": "mm",
                 "source": source or ("simulation_fixture" if candidate_id else "external_cad_export"),
                 "mechanical_validation": "Not established by print preparation"}
        return self.store.put("print_asset", run_id, asset)

    def candidate_asset(self, run_id, candidate_id):
        self.store.get(run_id, "run")
        candidates = self.store.list(run_id, "candidate")
        candidate = next((c for c in candidates if c["id"] == candidate_id), None)
        if candidate is None:
            raise ValueError("Candidate does not belong to this study")
        artifact = candidate["artifacts"]["stl"]
        path = self.store.artifacts / artifact["filename"]
        if _hash(path) != artifact["sha256"]:
            raise ValueError("Candidate mesh hash mismatch")
        return self.add_asset(candidate_id + ".stl", path.read_bytes(), run_id=run_id, candidate_id=candidate_id)

    def _file(self, filename, expected_hash):
        path = (self.directory / filename).resolve()
        if path.parent != self.directory.resolve() or not path.is_file() or _hash(path) != expected_hash:
            raise ValueError("Manufacturing artifact missing or modified; prepare a new project")
        return path

    def prepare(self, asset_id, profile):
        with self.lock:
            asset = self.store.get(asset_id, "print_asset")
            path = self._file(asset["filename"], asset["sha256"])
            project_id = identifier("build")
            output = self.directory / (project_id + ".form")
            self.store.event(asset["run_id"], "preform_preparation_started", {"project_id": project_id, "asset_id": asset_id})
            try:
                from formlabs_preform_api.models import ImportUnits, RepairBehavior
                with self.connect() as pf:
                    catalog = _plain(pf.list_print_settings())
                    if not any(profile.machine_type in p.get("supported_machine_type_ids", []) and re.match(r"^Form(?:\s|$)", p.get("label", ""), re.I) for p in catalog.get("printer_types", [])):
                        raise ValueError("This preparation workflow requires a Form-series SLA printer in PreForm's catalog")
                    if _plain(pf.validate_print_setting(**profile.scene_settings())).get("valid") is not True:
                        raise ValueError("Printer, resin, layer thickness and variant are not a valid PreForm setting")
                    scene = _plain(pf.create_scene(**profile.scene_settings()))
                    scene_id = scene["scene_id"]
                    imported = _plain(pf.import_models(scene_id=scene_id, file=str(path), name=asset["label"],
                        units=ImportUnits.MILLIMETERS, repair_behavior=RepairBehavior.ERROR, timeout=180))
                    model_ids = [m["model_id"] for m in imported["new_models"]]
                    if not model_ids:
                        raise ValueError("PreForm imported no models")
                    pf.auto_orient_models(scene_id=scene_id, timeout=180)
                    pf.auto_support_models(scene_id=scene_id, timeout=180)
                    pf.auto_layout_models(scene_id=scene_id, timeout=180)
                    validation = inspect_scene(pf, scene_id, model_ids, profile.minimum_wall_mm)
                    scene_state = _plain(pf.get_scene(scene_id=scene_id))
                    estimate = _plain(pf.calculate_print_time(scene_id=scene_id, timeout=180))
                    pf.save_form_file(file=str(output), scene_ids=[scene_id], project_name=project_id, timeout=180)
                    if not output.is_file():
                        raise ValueError("PreForm did not save the project on this computer")
                    record = {"id": project_id, "created_at": utc_now(), "run_id": asset["run_id"], "asset_id": asset_id,
                        "candidate_id": asset["candidate_id"], "profile": profile.model_dump(), "preform_version": str(pf.version),
                        "scene_id": scene_id, "scene_settings": scene_state["scene_settings"], "model_ids": model_ids,
                        "validation": validation, "estimate": estimate, "material_usage": scene_state.get("material_usage"),
                        "filename": output.name, "sha256": _hash(output), "status": "prepared_for_review", "physical_result": None}
                return self.store.put("print_project", asset["run_id"], record)
            except Exception:
                self.store.event(asset["run_id"], "preform_preparation_failed", {"project_id": project_id, "asset_id": asset_id})
                raise

    def queue(self, project_id, printer, review_notes):
        """Exactly one upload attempt per saved project, including ambiguous timeouts."""
        if not printer.strip() or not review_notes.strip():
            raise ValueError("Select a printer and record your PreForm review before queueing")
        with self.lock:
            project = self.store.get(project_id, "print_project")
            if project["validation"]["grade"] == "FAIL":
                raise ValueError("Resolve failed checks in CAD/PreForm and prepare a new project")
            if project.get("candidate_id"):
                # Study candidates print only after the specialists' recorded approval.
                reviews = [r for r in self.store.list(project["run_id"], "print_review") if r["project_id"] == project_id]
                if not reviews or reviews[-1]["status"] != "approved":
                    raise ValueError("Study candidates need an approved specialist print review before queueing")
            prior = [a for a in self.store.list(None, "print_submission") if a["project_id"] == project_id]
            if prior:
                raise ValueError("This project already has an upload attempt; check the printer queue before preparing another")
            path = self._file(project["filename"], project["sha256"])
            with self.connect() as pf:
                devices = _plain(pf.list_devices(can_print=True)).get("devices", [])
                device = next((d for d in devices if d["id"] == printer), None)
                if not device or device.get("is_connected") is not True:
                    raise ValueError("Selected printer is not connected in PreForm")
                catalog = _plain(pf.list_print_settings(compatible_with=printer))
                if not any(project["profile"]["machine_type"] in p.get("supported_machine_type_ids", []) for p in catalog.get("printer_types", [])):
                    raise ValueError("Selected printer is incompatible with this project's machine type")
                loaded = device.get("tank_material_code")
                if loaded and loaded != project["profile"]["material_code"]:
                    raise ValueError("Loaded resin differs from the project's resin; resolve this in PreForm first")
                # Reopen the reviewed file; never upload a possibly edited in-memory scene.
                opened = _plain(pf.open_form_file(file=str(path), timeout=180))["scenes"]
                if len(opened) != 1:
                    raise ValueError("Expected one scene in the reviewed project")
                scene = opened[0]
                if scene["scene_settings"] != project["scene_settings"]:
                    raise ValueError("Saved scene settings changed; prepare and review a new project")
                check = inspect_scene(pf, scene["scene_id"], scene["new_model_ids"], project["profile"]["minimum_wall_mm"])
                if check["grade"] == "FAIL":
                    raise ValueError("Reopened scene failed printability checks")
                attempt = {"id": identifier("upload"), "created_at": utc_now(), "project_id": project_id,
                    "printer": printer, "review_notes": review_notes, "validation": check, "print_now": False}
                # Commit intent before the external side effect; crashes never cause automatic resubmission.
                with self.store.atomic():
                    if any(a["project_id"] == project_id for a in self.store.list(None, "print_submission")):
                        raise ValueError("An upload attempt already exists for this project")
                    self.store.put("print_submission", project["run_id"], attempt)
                try:
                    job = _plain(pf.print_scene(scene_id=scene["scene_id"], printer=printer, job_name=project_id,
                        print_now=False, timeout=180))
                except Exception:
                    self.store.put("print_receipt", project["run_id"], {"id": identifier("receipt"), "submission_id": attempt["id"],
                        "project_id": project_id, "status": "upload_outcome_unknown", "created_at": utc_now()})
                    raise FormlabsError("Upload outcome unknown; check PreForm and the printer queue. No retry was made.") from None
                return self.store.put("print_receipt", project["run_id"], {"id": identifier("receipt"), "submission_id": attempt["id"],
                    "project_id": project_id, "job_id": job["job_id"], "printer": printer, "status": "queued",
                    "print_now": False, "created_at": utc_now(), "physical_result": None})
