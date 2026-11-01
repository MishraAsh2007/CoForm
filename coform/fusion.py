"""Autodesk Fusion link through a local add-in (fusion/CoForm); no API key or cloud.

The add-in polls this server. CoForm queues "build" jobs that set the design's
sc_* user parameters; the add-in rebuilds the parametric timeline, exports STL/STEP and
posts them back. People can also edit the design in Fusion and press "Send to
CoForm": changed dimensions become a new human-proposed candidate, and every
export is checked against the analyzed section, so edits the beam model cannot
represent (fillets, holes, frozen dimensions) are reported instead of trusted.
"""
from __future__ import annotations

import base64
import hashlib
import math
import struct
import threading
import time

from .cad import VARIABLES, check_mesh, mesh_metrics, _triangles
from .domain import Geometry, identifier, utc_now
from . import mechanics

POLL_FRESH_S = 15


def _stl_binary(triangles, label: bytes = b"coform-fusion") -> bytes:
    raw = bytearray(label[:80].ljust(80, b" ") + struct.pack("<I", len(triangles)))
    for tri in triangles:
        raw += struct.pack("<12fH", 0, 0, 0, *(float(v) for v in tri.reshape(-1)), 0)
    return bytes(raw)


def normalize_units(raw: bytes, geometry: Geometry) -> tuple[bytes, str]:
    """Fusion STL exports may be in cm. Rescale to mm only when cm explains the extents."""
    metrics = mesh_metrics(raw)
    expected = sorted([geometry.length_m * 1000, geometry.base_width_m * 1000,
                       max(y for _, y in mechanics._section_polygon_mm(geometry))])
    actual = sorted(metrics["extents_mm"])
    if all(abs(a * 10 - e) <= 0.05 for a, e in zip(actual, expected)):
        return _stl_binary(_triangles(raw) * 10), "cm_rescaled_to_mm"
    return raw, "mm"


class FusionLink:
    def __init__(self, service, manufacturing):
        self.service, self.store, self.manufacturing = service, service.store, manufacturing
        self.directory = self.store.directory / "fusion"
        self.directory.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.claimed, self.done = set(), {}
        self.results = {r["job_id"]: r for r in self.store.list(None, "fusion_result")}
        self.last_poll, self.addin = 0.0, {}

    # ---- add-in side -----------------------------------------------------------------
    def next_job(self, addin: dict | None = None):
        with self.lock:
            self.last_poll = time.monotonic()
            if addin:
                self.addin = {k: str(v)[:200] for k, v in addin.items()}
            for job in self.store.list("fusion", "fusion_job"):
                if job["id"] not in self.claimed and job["id"] not in self.results:
                    if job.get("kind") == "view_step" and not self._can_view_step():
                        self.submit_result(job["id"], {"error": "Reload the CoForm add-in in Fusion; this version cannot open STEP jobs"})
                        continue
                    self.claimed.add(job["id"])
                    return job
            return None

    def submit_result(self, job_id: str, payload: dict):
        job = self.store.get(job_id, "fusion_job")
        with self.lock:
            if job_id in self.results:
                raise ValueError("This Fusion job already has a result")
        try:
            if payload.get("error"):
                raise ValueError(f"Fusion reported: {str(payload['error'])[:500]}")
            if job.get("kind") == "view_step":
                if not isinstance(payload.get("document"), str) or not payload["document"].strip():
                    raise ValueError("Fusion did not confirm an imported document")
                outcome = {"status": "done", "document": str(payload.get("document") or "")[:200]}
            else:
                revision = self._revision(job["run_id"], job["candidate_id"], payload, origin="agent_build")
                outcome = {"status": "done", "cad_revision_id": revision["id"]}
        except ValueError as exc:
            outcome = {"status": "failed", "error": str(exc)[:500]}
        record = self.store.put("fusion_result", job["run_id"], {"id": identifier("fusion_result"), "job_id": job_id,
                                                                  **outcome, "created_at": utc_now()})
        with self.lock:
            self.results[job_id] = record
            self.done.setdefault(job_id, threading.Event()).set()
        return record

    def user_submission(self, payload: dict):
        """A person edited the design in Fusion and pressed 'Send to CoForm'."""
        run_id, linked_id = payload.get("run_id"), payload.get("candidate_id")
        if not run_id or not linked_id:
            raise ValueError("This Fusion design is not linked to a study yet. Build a candidate from CoForm first.")
        run = self.store.get(run_id, "run")
        linked = next((c for c in self.store.list(run_id, "candidate") if c["id"] == linked_id), None)
        if linked is None:
            raise ValueError("The linked candidate does not belong to that study")
        geometry = self._geometry(Geometry(**linked["geometry"]), payload)
        frozen = [f for f in ("length_m", "thickness_m") if not math.isclose(getattr(geometry, f), linked["geometry"][f], abs_tol=1e-9)]
        if frozen:
            names = ", ".join(n for n, k in VARIABLES.items() if k in frozen)
            raise ValueError(f"{names} is frozen for this study. Change only sc_base_width and sc_wall_height, "
                             "or start a new study with the new value.")
        candidate_id, created = linked_id, False
        if geometry.model_dump() != linked["geometry"]:
            existing = next((c for c in self.store.list(run_id, "candidate") if c["geometry"] == geometry.model_dump()), None)
            if existing:
                candidate_id = existing["id"]
            else:
                try:
                    candidate_id = self.service.add_candidate(run_id, geometry, parent_id=linked_id, proposed_by="human:fusion")["id"]
                except ValueError as exc:
                    step = run["protocol"]["geometry_step_m"] * 1000
                    raise ValueError(f"{exc}. Use the study's allowed ranges on a {step:g} mm grid.") from None
                created = True
        revision = self._revision(run_id, candidate_id, payload, origin="user_edit")
        diagnosis = self.service.diagnose(run_id, candidate_id)
        return {"candidate_id": candidate_id, "new_candidate": created, "cad_revision_id": revision["id"],
                "check": revision["check"]["status"], "unit_handling": revision["fusion"]["units"],
                "requirements": [{"label": r["label"], "passed": r["passed"]} for r in diagnosis["requirements"]],
                "issues": [{"title": i["title"], "detail": i["detail"], "owner": i["owner"]} for i in diagnosis["issues"]][:6],
                "diagnosis_status": diagnosis["status"]}

    # ---- CoForm side ------------------------------------------------------------
    def status(self):
        with self.lock:
            age = time.monotonic() - self.last_poll if self.last_poll else None
            pending = [j["id"] for j in self.store.list("fusion", "fusion_job") if j["id"] not in self.results]
        return {"connected": age is not None and age < POLL_FRESH_S, "seconds_since_poll": age,
                "addin": self.addin, "pending_jobs": len(pending), "supports_view_step": self._can_view_step()}

    def _can_view_step(self):
        if "view_step" in str(self.addin.get("capabilities", "")).split(","):
            return True
        try:
            return tuple(int(n) for n in self.addin.get("addin_version", "").split(".")) >= (0, 2, 0)
        except ValueError:
            return False

    def job_status(self, job_id):
        job = self.store.get(job_id, "fusion_job")
        with self.lock:
            return {"id": job_id, "kind": job.get("kind", "build"),
                    "status": self.results.get(job_id, {}).get("status", "running" if job_id in self.claimed else "queued"),
                    "result": self.results.get(job_id)}

    def request_view(self, name: str, step_url: str) -> dict:
        """Ask the add-in to open a STEP (a designer part) in a new Fusion document for viewing."""
        if not self.status()["connected"]:
            raise ValueError("Fusion add-in is not connected. In Fusion: Utilities > Add-Ins > CoForm > Run.")
        if not self._can_view_step():
            raise ValueError(f"Fusion is running CoForm {self.addin.get('addin_version', 'unknown')}, which cannot open STEP jobs. "
                             "In Fusion: Utilities > Add-Ins > Scripts and Add-Ins > CoForm > Stop, then Run to load the updated add-in.")
        with self.lock:
            for job in self.store.list("fusion", "fusion_job"):
                if job.get("kind") == "view_step" and job.get("step_url") == step_url and job["id"] not in self.results:
                    return job  # repeated clicks follow the same import instead of opening many documents
            return self.store.put("fusion_job", "fusion", {"id": identifier("fusion_job"), "kind": "view_step", "run_id": "designer",
                                                        "name": name[:80], "step_url": step_url, "created_at": utc_now()})

    def request_build(self, run_id: str, candidate_id: str) -> dict:
        candidate = next((c for c in self.store.list(run_id, "candidate") if c["id"] == candidate_id), None)
        if candidate is None:
            raise ValueError("Candidate does not belong to this study")
        geometry = Geometry(**candidate["geometry"])
        return self.store.put("fusion_job", "fusion", {
            "id": identifier("fusion_job"), "kind": "build", "run_id": run_id, "candidate_id": candidate_id,
            "family": geometry.section_family, "created_at": utc_now(),
            "parameters": {name: f"{getattr(geometry, key) * 1000:g} mm" for name, key in VARIABLES.items()}})

    def build_and_wait(self, run_id: str, candidate_id: str, timeout_s: float = 90):
        """Queue a build and wait for the add-in. The add-in must be running in Fusion."""
        prior = [r for r in self.store.list(run_id, "cad_revision")
                 if r["candidate_id"] == candidate_id and r["source"] == "fusion"
                 and r["fusion"]["origin"] == "agent_build" and r["check"]["status"] == "pass"]
        if prior:
            return prior[-1]
        if not self.status()["connected"]:
            raise ValueError("Fusion add-in is not connected. In Fusion: Utilities > Add-Ins > CoForm > Run, with the design open.")
        job = self.request_build(run_id, candidate_id)
        with self.lock:
            event = self.done.setdefault(job["id"], threading.Event())
        if not event.wait(timeout_s):
            raise ValueError(f"Fusion did not finish within {timeout_s:g} s; check the add-in and the open design")
        result = self.results[job["id"]]
        if result["status"] != "done":
            raise ValueError(result["error"])
        return self.store.get(result["cad_revision_id"], "cad_revision")

    def _geometry(self, base: Geometry, payload: dict) -> Geometry:
        values = payload.get("values_mm") or {}
        missing = [n for n in VARIABLES if not isinstance(values.get(n), (int, float))]
        if missing:
            raise ValueError(f"Fusion design lacks user parameters {', '.join(missing)}; run 'CoForm: set up' in Fusion")
        try:
            return Geometry(**{**base.model_dump(), **{key: round(values[name] / 1000, 9) for name, key in VARIABLES.items()}})
        except ValueError as exc:
            raise ValueError(f"Fusion parameters do not form a valid section: {exc}") from None

    def _decode(self, payload: dict, key: str, limit: int) -> bytes | None:
        text = payload.get(key)
        if not text:
            return None
        raw = base64.b64decode(text, validate=True)
        if len(raw) > limit:
            raise ValueError(f"{key} exceeds {limit // (1024 * 1024)} MiB")
        return raw

    def _revision(self, run_id: str, candidate_id: str, payload: dict, *, origin: str):
        candidate = next(c for c in self.store.list(run_id, "candidate") if c["id"] == candidate_id)
        geometry = Geometry(**candidate["geometry"])
        stl = self._decode(payload, "stl_b64", 25 * 1024 * 1024)
        if stl is None:
            raise ValueError("Fusion sent no STL export")
        stl, units = normalize_units(stl, geometry)
        metrics = mesh_metrics(stl)
        check = check_mesh(geometry, metrics)
        reported = payload.get("volume_mm3")
        if isinstance(reported, (int, float)) and reported > 0:
            expected = mechanics.section_properties(geometry)["area_m2"] * geometry.length_m * 1e9
            check["checks"].append({"name": "fusion_physical_properties_volume", "passed": abs(reported - expected) <= 0.01 * expected,
                                    "expected_mm3": expected, "actual_mm3": reported, "relative_tolerance": 0.01})
            check["status"] = "pass" if all(c["passed"] for c in check["checks"]) else "fail"
        step = self._decode(payload, "step_b64", 25 * 1024 * 1024)
        step_record = None
        if step:
            sha = hashlib.sha256(step).hexdigest()
            (self.directory / f"{sha}.step").write_bytes(step)
            step_record = {"filename": f"{sha}.step", "sha256": sha}
        asset = self.manufacturing.add_asset(f"{candidate_id}-fusion.stl", stl, run_id=run_id,
                                             candidate_id=candidate_id, source="fusion_export")
        meta = {k: str(payload.get(k))[:200] for k in ("document", "version", "fusion_version") if payload.get(k) is not None}
        return self.store.put("cad_revision", run_id, {
            "id": identifier("cad"), "run_id": run_id, "candidate_id": candidate_id, "source": "fusion",
            "onshape": None, "variables": {k: str(v)[:60] for k, v in (payload.get("parameters") or {}).items()},
            "fusion": {"origin": origin, "units": units, "step": step_record, "bodies": payload.get("bodies"), **meta},
            "asset_id": asset["id"], "stl_sha256": asset["sha256"], "mesh": metrics, "onshape_volume_m3": None,
            "check": check, "created_at": utc_now()})

    def step_file(self, run_id: str, cad_id: str):
        revision = self.store.get(cad_id, "cad_revision")
        if revision["run_id"] != run_id or revision["source"] != "fusion" or not revision["fusion"]["step"]:
            raise ValueError("No Fusion STEP for this revision")
        record = revision["fusion"]["step"]
        path = (self.directory / record["filename"]).resolve()
        if path.parent != self.directory.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("Fusion STEP missing or modified")
        return path
