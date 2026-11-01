"""Onshape connection: candidate dimensions drive one parametric Part Studio.

CoForm writes only the named ``sc_*`` length variables, snapshots the document
as an Onshape version, then exports a millimeter STL from that immutable version.
The exported mesh is checked against the section the mechanics model analyzed;
Onshape geometry that differs from the analyzed section cannot reach a printer.

Endpoints follow https://onshape-public.github.io/docs/ (checked 2026-10-31).
No network activity occurs on import or construction.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import base64
import os
from pathlib import Path
import re
import struct
import threading
from urllib.parse import urljoin, urlparse

import httpx
import numpy as np

from .domain import Geometry, identifier, utc_now
from . import mechanics


# Onshape variable name -> Geometry field. All are LENGTH variables.
VARIABLES = {"sc_length": "length_m", "sc_base_width": "base_width_m",
             "sc_wall_height": "wall_height_m", "sc_thickness": "thickness_m"}
VOLUME_TOLERANCE = 0.01   # relative; planar prismatic faces tessellate exactly
BBOX_TOLERANCE_MM = 0.05


class OnshapeError(ValueError):
    pass


def parse_part_studio_url(url: str) -> dict:
    match = re.search(r"/documents/([0-9a-f]{24})/w/([0-9a-f]{24})/e/([0-9a-f]{24})", url or "")
    if not match:
        raise OnshapeError("Set ONSHAPE_PART_STUDIO_URL to a Part Studio tab URL: .../documents/<did>/w/<wid>/e/<eid>")
    return dict(zip(("did", "wid", "eid"), match.groups()))


def expression_mm(value_m: float) -> str:
    return f"{value_m * 1000:.6g} mm"


@dataclass
class OnshapeClient:
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    base_url: str = "https://cad.onshape.com"
    timeout_s: float = 60.0
    client: httpx.Client | None = field(default=None, repr=False)

    def __post_init__(self):
        if not self.access_key or not self.secret_key:
            raise OnshapeError("Set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY in .env (Onshape developer portal API keys)")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith("onshape.com"):
            raise OnshapeError("ONSHAPE_BASE_URL must be an https://*.onshape.com stack")
        self.base_url = self.base_url.rstrip("/")

    def _headers(self, accept: str) -> dict:
        token = base64.b64encode(f"{self.access_key}:{self.secret_key}".encode()).decode()
        return {"Authorization": f"Basic {token}", "Accept": accept, "Content-Type": "application/json"}

    def _send(self, method, url, **kwargs):
        try:
            if self.client is None:
                with httpx.Client(timeout=self.timeout_s, follow_redirects=False) as client:
                    return client.request(method, url, **kwargs)
            return self.client.request(method, url, timeout=self.timeout_s, **kwargs)
        except httpx.HTTPError as exc:
            raise OnshapeError(f"Onshape request failed ({type(exc).__name__}); no retry was made") from None

    def request(self, method: str, path: str, *, params=None, body=None, accept="application/json", raw=False):
        url = self.base_url + path
        response = self._send(method, url, headers=self._headers(accept), params=params, json=body)
        # Exports answer 307; credentials are re-sent only to the configured Onshape stack.
        for _ in range(3):
            if response.status_code not in (302, 303, 307, 308):
                break
            location = urljoin(url, response.headers.get("location", ""))
            host = urlparse(location).hostname or ""
            if urlparse(location).scheme != "https" or not (host == urlparse(self.base_url).hostname or host.endswith(".onshape.com")):
                raise OnshapeError("Onshape redirected outside its own domain; credentials were not forwarded")
            url = location
            response = self._send("GET", url, headers=self._headers(accept))
        if not 200 <= response.status_code < 300:
            detail = ""
            try:
                message = response.json().get("message", "")
                detail = ": " + re.sub(r"\s+", " ", str(message))[:200] if message else ""
            except (ValueError, AttributeError):
                pass
            raise OnshapeError(f"Onshape returned HTTP {response.status_code} for {method} {path.split('?')[0]}{detail}")
        if raw:
            return response.content
        try:
            return response.json()
        except ValueError:
            raise OnshapeError(f"Onshape returned non-JSON for {path}") from None

    def _ps(self, doc, wvm="w", wvmid=None):
        return f"/api/v9/partstudios/d/{doc['did']}/{wvm}/{wvmid or doc['wid']}/e/{doc['eid']}"

    def features(self, doc):
        return self.request("GET", self._ps(doc) + "/features")

    def add_feature(self, doc, feature, serialization_version=None, source_microversion=None):
        body = {"btType": "BTFeatureDefinitionCall-1406", "feature": feature}
        if serialization_version:
            body["serializationVersion"] = serialization_version
        if source_microversion:
            body["sourceMicroversion"] = source_microversion
        return self.request("POST", self._ps(doc) + "/features", body=body)

    def update_feature(self, doc, feature, serialization_version=None, source_microversion=None):
        body = {"btType": "BTFeatureDefinitionCall-1406", "feature": feature}
        if serialization_version:
            body["serializationVersion"] = serialization_version
        if source_microversion:
            body["sourceMicroversion"] = source_microversion
        return self.request("POST", self._ps(doc) + f"/features/featureid/{feature['featureId']}", body=body)

    def create_version(self, doc, name, description):
        return self.request("POST", f"/api/v9/documents/d/{doc['did']}/versions",
                            body={"documentId": doc["did"], "name": name, "description": description})

    def export_stl(self, doc, version_id):
        return self.request("GET", self._ps(doc, "v", version_id) + "/stl", accept="*/*", raw=True,
                            params={"units": "millimeter", "mode": "binary", "grouping": "true", "scale": "1"})

    def mass_properties(self, doc, version_id):
        return self.request("GET", self._ps(doc, "v", version_id) + "/massproperties", params={"massAsGroup": "true"})


def client_from_environment() -> tuple[OnshapeClient, dict]:
    doc = parse_part_studio_url(os.getenv("ONSHAPE_PART_STUDIO_URL", ""))
    client = OnshapeClient(os.getenv("ONSHAPE_ACCESS_KEY", ""), os.getenv("ONSHAPE_SECRET_KEY", ""),
                           os.getenv("ONSHAPE_BASE_URL", "https://cad.onshape.com"))
    return client, doc


def _parameter(feature, parameter_id):
    return next((p for p in feature.get("parameters", []) if p.get("parameterId") == parameter_id), None)


def variable_features(features_response: dict) -> dict:
    """Map sc_* variable names to their Part Studio Variable features."""
    found = {}
    for feature in features_response.get("features", []):
        if feature.get("featureType") != "assignVariable":
            continue
        name = (_parameter(feature, "name") or {}).get("value")
        if name in VARIABLES:
            found[name] = feature
    return found


def with_expression(feature: dict, expression: str) -> dict:
    """Return a copy of a Length variable feature with a new value expression."""
    kind = (_parameter(feature, "variableType") or {}).get("value", "ANY")
    target = {"LENGTH": "lengthValue", "ANY": "anyValue"}.get(kind)
    updated = deepcopy(feature)
    parameter = _parameter(updated, target) if target else None
    if parameter is None or "expression" not in parameter:
        name = (_parameter(feature, "name") or {}).get("value", "?")
        raise OnshapeError(f"Onshape variable {name} must be a Length (or Any) variable")
    parameter["expression"] = expression
    parameter.pop("value", None)  # Onshape re-evaluates the expression.
    return updated


def new_variable_feature(name: str, expression: str) -> dict:
    """Standard Variable feature. Structure mirrors Onshape's assignVariable parameters."""
    return {"btType": "BTMFeature-134", "featureType": "assignVariable", "name": f"Variable {name}",
            "parameters": [
                {"btType": "BTMParameterEnum-145", "parameterId": "variableType", "enumName": "VariableType", "value": "LENGTH"},
                {"btType": "BTMParameterString-149", "parameterId": "name", "value": name},
                {"btType": "BTMParameterQuantity-147", "parameterId": "lengthValue", "expression": expression, "isInteger": False},
            ]}


def regeneration_errors(features_response: dict) -> list[str]:
    states = features_response.get("featureStates") or {}
    names = {f.get("featureId"): f.get("name", f.get("featureId")) for f in features_response.get("features", [])}
    return [f"{names.get(fid, fid)}: {state.get('featureStatus')}" for fid, state in states.items()
            if isinstance(state, dict) and state.get("featureStatus") not in (None, "OK")]


def _triangles(raw: bytes) -> np.ndarray:
    count = struct.unpack_from("<I", raw, 80)[0] if len(raw) >= 84 else 0
    if count > 0 and len(raw) == 84 + count * 50:
        dtype = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
        return np.frombuffer(raw, dtype=dtype, count=count, offset=84)["v"].astype(float)
    vertices = re.findall(r"(?m)^\s*vertex\s+(\S+)\s+(\S+)\s+(\S+)\s*$", raw.decode("ascii"))
    return np.asarray(vertices, dtype=float).reshape(-1, 3, 3)


def mesh_metrics(raw: bytes) -> dict:
    """Volume (divergence theorem), axis-aligned extents and edge closure of an STL in mm."""
    from .formlabs import validate_stl
    validate_stl(raw)
    tris = _triangles(raw)
    if not len(tris):
        raise ValueError("STL contains no triangles")
    volume = float(np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2])).sum() / 6)
    points = tris.reshape(-1, 3)
    extents = (points.max(axis=0) - points.min(axis=0)).tolist()
    keys = np.round(tris / 1e-4).astype(np.int64)
    edges = {}
    for tri in keys:
        corners = [tuple(c) for c in tri]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edge = tuple(sorted((corners[a], corners[b])))
            edges[edge] = edges.get(edge, 0) + 1
    return {"triangles": int(len(tris)), "volume_mm3": abs(volume), "extents_mm": extents,
            "closed": all(count == 2 for count in edges.values()), "orientation": "outward" if volume > 0 else "inward"}


def _mass_volume_m3(mass_properties: dict | None) -> float | None:
    try:
        values = mass_properties["bodies"]["-all-"]["volume"]
        values = values if isinstance(values, list) else [values]
        return float(sorted(values)[len(values) // 2])
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def check_mesh(geometry: Geometry, metrics: dict, onshape_volume_m3: float | None = None) -> dict:
    """Compare the exact bytes going to the printer with the analyzed section."""
    points = mechanics._section_polygon_mm(geometry)
    height = max(y for _, y in points)
    expected_extents = sorted([geometry.length_m * 1000, geometry.base_width_m * 1000, height])
    expected_volume = mechanics.section_properties(geometry)["area_m2"] * geometry.length_m * 1e9
    actual_extents = sorted(metrics["extents_mm"])
    checks = [
        {"name": "closed_mesh", "passed": bool(metrics["closed"]), "detail": "every edge shared by exactly two triangles"},
        {"name": "extents_match_section", "passed": all(abs(a - e) <= BBOX_TOLERANCE_MM for a, e in zip(actual_extents, expected_extents)),
         "expected_mm": expected_extents, "actual_mm": actual_extents, "tolerance_mm": BBOX_TOLERANCE_MM},
        {"name": "volume_matches_section", "passed": abs(metrics["volume_mm3"] - expected_volume) <= VOLUME_TOLERANCE * expected_volume,
         "expected_mm3": expected_volume, "actual_mm3": metrics["volume_mm3"], "relative_tolerance": VOLUME_TOLERANCE},
    ]
    if onshape_volume_m3 is not None:
        checks.append({"name": "onshape_mass_properties_volume", "passed": abs(onshape_volume_m3 * 1e9 - expected_volume) <= VOLUME_TOLERANCE * expected_volume,
                       "expected_mm3": expected_volume, "actual_mm3": onshape_volume_m3 * 1e9, "relative_tolerance": VOLUME_TOLERANCE})
    return {"status": "pass" if all(c["passed"] for c in checks) else "fail", "checks": checks,
            "scope": "Geometry agreement with the analyzed section only; not printability or mechanical validation"}


class CADLink:
    """Writes candidate dimensions to Onshape and records immutable CAD revisions."""

    def __init__(self, store, manufacturing, client_factory=client_from_environment):
        self.store = store
        self.manufacturing = manufacturing
        self.client_factory = client_factory
        self.lock = threading.RLock()

    def status(self):
        try:
            client, doc = self.client_factory()
        except OnshapeError as exc:
            return {"configured": False, "detail": str(exc)}
        try:
            features = client.features(doc)
        except OnshapeError as exc:
            return {"configured": True, "connected": False, "detail": str(exc), "document": doc}
        found = variable_features(features)
        expressions = {name: (_parameter(f, "lengthValue") or _parameter(f, "anyValue") or {}).get("expression") for name, f in found.items()}
        geometry_features = [f.get("name") for f in features.get("features", []) if f.get("featureType") != "assignVariable"]
        return {"configured": True, "connected": True, "document": doc, "variables": expressions,
                "missing_variables": [v for v in VARIABLES if v not in found], "geometry_features": geometry_features,
                "regeneration_errors": regeneration_errors(features),
                "ready": not [v for v in VARIABLES if v not in found] and bool(geometry_features) and not regeneration_errors(features)}

    def bootstrap(self, geometry: Geometry):
        """Create any missing sc_* variables. Existing variables are left untouched."""
        with self.lock:
            client, doc = self.client_factory()
            features = client.features(doc)
            found = variable_features(features)
            created = []
            for name, key in VARIABLES.items():
                if name in found:
                    continue
                client.add_feature(doc, new_variable_feature(name, expression_mm(getattr(geometry, key))),
                                   features.get("serializationVersion"))
                created.append(name)
            self.store.event("onshape", "onshape_variables_bootstrapped", {"document": doc, "created": created})
            return {"created": created, **self.status()}

    def sync_candidate(self, run_id, candidate_id):
        """Write variables, version the document, export and check the STL. Idempotent per candidate."""
        with self.lock:
            candidates = self.store.list(run_id, "candidate")
            candidate = next((c for c in candidates if c["id"] == candidate_id), None)
            if candidate is None:
                raise ValueError("Candidate does not belong to this study")
            prior = [r for r in self.store.list(run_id, "cad_revision")
                     if r["candidate_id"] == candidate_id and r["source"] == "onshape" and r["check"]["status"] == "pass"]
            if prior:
                return prior[-1]
            geometry = Geometry(**candidate["geometry"])
            client, doc = self.client_factory()
            features = client.features(doc)
            found = variable_features(features)
            missing = [v for v in VARIABLES if v not in found]
            if missing:
                raise OnshapeError(f"Onshape Part Studio lacks variables {', '.join(missing)}; run Onshape setup first")
            serialization, microversion = features.get("serializationVersion"), features.get("sourceMicroversion")
            written = {}
            for name, key in VARIABLES.items():
                expression = expression_mm(getattr(geometry, key))
                written[name] = expression
                current = (_parameter(found[name], "lengthValue") or _parameter(found[name], "anyValue") or {}).get("expression")
                if current == expression:
                    continue
                response = client.update_feature(doc, with_expression(found[name], expression), serialization, microversion)
                microversion = response.get("sourceMicroversion") if isinstance(response, dict) else None
                status = ((response or {}).get("featureState") or {}).get("featureStatus", "OK")
                if status != "OK":
                    raise OnshapeError(f"Onshape rejected {name}={expression} ({status})")
            regenerated = client.features(doc)
            errors = regeneration_errors(regenerated)
            if errors:
                raise OnshapeError("Onshape features failed to regenerate: " + "; ".join(errors[:5]))
            self.store.event(run_id, "onshape_variables_written", {"candidate_id": candidate_id, "document": doc, "variables": written})
            version = client.create_version(doc, f"CoForm {candidate_id}",
                                            f"Run {run_id}; {', '.join(f'{k}={v}' for k, v in written.items())}")
            raw = client.export_stl(doc, version["id"])
            metrics = mesh_metrics(raw)
            try:
                onshape_volume = _mass_volume_m3(client.mass_properties(doc, version["id"]))
            except OnshapeError:
                onshape_volume = None  # Cross-check only; the mesh check is authoritative.
            check = check_mesh(geometry, metrics, onshape_volume)
            asset = self.manufacturing.add_asset(f"{candidate_id}-onshape.stl", raw, run_id=run_id,
                                                 candidate_id=candidate_id, source="onshape_export")
            return self.store.put("cad_revision", run_id, {
                "id": identifier("cad"), "run_id": run_id, "candidate_id": candidate_id, "source": "onshape",
                "onshape": {**doc, "version_id": version["id"], "version_name": version.get("name"),
                            "microversion": version.get("microversion"), "base_url": client.base_url},
                "variables": written, "asset_id": asset["id"], "stl_sha256": asset["sha256"], "mesh": metrics,
                "onshape_volume_m3": onshape_volume, "check": check, "created_at": utc_now()})

    def upload_revision(self, run_id, candidate_id, filename, raw):
        """Manual Onshape export (no API key): check the uploaded STL against the candidate."""
        with self.lock:
            candidate = next((c for c in self.store.list(run_id, "candidate") if c["id"] == candidate_id), None)
            if candidate is None:
                raise ValueError("Candidate does not belong to this study")
            metrics = mesh_metrics(raw)
            asset = self.manufacturing.add_asset(filename, raw, run_id=run_id, candidate_id=candidate_id, source="manual_cad_export")
            return self.store.put("cad_revision", run_id, {
                "id": identifier("cad"), "run_id": run_id, "candidate_id": candidate_id, "source": "manual_export",
                "onshape": None, "variables": None, "asset_id": asset["id"], "stl_sha256": asset["sha256"], "label": asset["label"],
                "mesh": metrics, "onshape_volume_m3": None,
                "check": check_mesh(Geometry(**candidate["geometry"]), metrics), "created_at": utc_now()})

    def fixture_revision(self, run_id, candidate_id):
        """Offline fallback: check CoForm's own fixture STL, labeled as such."""
        with self.lock:
            prior = [r for r in self.store.list(run_id, "cad_revision") if r["candidate_id"] == candidate_id and r["source"] == "local_fixture"]
            if prior:
                return prior[-1]
            asset = self.manufacturing.candidate_asset(run_id, candidate_id)
            candidate = next(c for c in self.store.list(run_id, "candidate") if c["id"] == candidate_id)
            raw = self.manufacturing._file(asset["filename"], asset["sha256"]).read_bytes()
            metrics = mesh_metrics(raw)
            return self.store.put("cad_revision", run_id, {
                "id": identifier("cad"), "run_id": run_id, "candidate_id": candidate_id, "source": "local_fixture",
                "onshape": None, "variables": None, "asset_id": asset["id"], "stl_sha256": asset["sha256"],
                "mesh": metrics, "onshape_volume_m3": None,
                "check": check_mesh(Geometry(**candidate["geometry"]), metrics), "created_at": utc_now()})


def print_pipeline(service, cad_link, manufacturing, run_id, candidate_id, profile, *, cad_source="onshape", live=False,
                   freecad=None, fusion=None):
    """CAD export -> geometry check -> PreForm preparation -> specialist review.

    Stops before the printer queue: uploading still requires a person to select the
    printer and record review notes, and never sends print_now=True.
    """
    if cad_source not in ("auto", "onshape", "freecad", "fusion", "local_fixture", "uploaded"):
        raise ValueError("cad_source must be auto, onshape, freecad, fusion, local_fixture or uploaded")
    result, stage = {}, "cad"
    try:
        if cad_source == "auto":
            cad = build_cad(run_id, candidate_id, fusion=fusion, freecad=freecad)
        elif cad_source == "onshape":
            cad = cad_link.sync_candidate(run_id, candidate_id)
        elif cad_source == "fusion":
            if fusion is None:
                raise ValueError("Fusion link is not configured")
            cad = fusion.build_and_wait(run_id, candidate_id)
        elif cad_source == "freecad":
            if freecad is None:
                raise ValueError("FreeCAD backend is not configured")
            cad = freecad.sync_candidate(run_id, candidate_id)
        elif cad_source == "local_fixture":
            cad = cad_link.fixture_revision(run_id, candidate_id)
        else:
            cad = next((r for r in reversed(service.store.list(run_id, "cad_revision"))
                        if r["candidate_id"] == candidate_id and r["source"] == "manual_export"), None)
            if cad is None:
                raise ValueError("Upload this candidate's Onshape STL export first")
        result["cad_revision"] = cad
        if cad["check"]["status"] != "pass":
            return {"status": "stopped", "stage": stage, "detail": "Exported geometry does not match the analyzed section",
                    "diagnosis": service.diagnose(run_id, candidate_id), **result}
        stage = "preform"
        result["print_project"] = manufacturing.prepare(cad["asset_id"], profile)
        stage = "review"
        result["print_review"] = service.print_review(run_id, result["print_project"]["id"], live=live)
    except Exception as exc:
        detail = str(exc)[:500] if isinstance(exc, (ValueError, KeyError)) else f"{type(exc).__name__} during {stage}"
        service.store.event(run_id, "print_pipeline_stopped", {"candidate_id": candidate_id, "stage": stage, "detail": detail})
        return {"status": "stopped", "stage": stage, "detail": detail, **result}
    review = result["print_review"]["status"]
    return {"status": "ready_to_queue" if review == "approved" else f"review_{review}", "stage": "queue", **result}


def find_freecad_cmd() -> str | None:
    """FREECAD_CMD, then common Windows install locations, then PATH."""
    import glob
    import shutil
    configured = os.getenv("FREECAD_CMD")
    if configured:
        return configured if os.path.isfile(configured) else None
    roots = [os.getenv("LOCALAPPDATA", ""), os.getenv("ProgramFiles", ""), os.getenv("ProgramFiles(x86)", "")]
    patterns = [os.path.join(root, *parts) for root in roots if root
                for parts in (("Programs", "FreeCAD*", "bin", "FreeCADCmd.exe"), ("FreeCAD*", "bin", "FreeCADCmd.exe"))]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern), reverse=True)
        if matches:
            return matches[0]
    return shutil.which("FreeCADCmd") or shutil.which("freecadcmd")


def _sha(raw: bytes) -> str:
    import hashlib
    return hashlib.sha256(raw).hexdigest()


class FreeCADLink:
    """Drives a parametric FreeCAD master model headless; snapshots each candidate."""

    TIMEOUT_S = 240

    def __init__(self, store, manufacturing, command: list[str] | None = None):
        self.store = store
        self.manufacturing = manufacturing
        self.command = command  # Test override: [python, fake_worker]
        self.directory = store.directory / "freecad"
        self.directory.mkdir(exist_ok=True)
        self.lock = threading.RLock()

    def _command(self):
        if self.command:
            return self.command
        executable = find_freecad_cmd()
        if not executable:
            raise ValueError("FreeCAD not found. Install FreeCAD 1.x or set FREECAD_CMD to FreeCADCmd.exe")
        return [executable, str(Path(__file__).with_name("freecad_worker.py"))]

    def _run(self, job: dict) -> dict:
        import json
        import subprocess
        jobs = self.directory / "jobs"
        jobs.mkdir(exist_ok=True)
        name = identifier("job")
        job_path, result_path = jobs / f"{name}.json", jobs / f"{name}.result.json"
        job_path.write_text(json.dumps({**job, "result": str(result_path)}), encoding="utf-8")
        try:
            completed = subprocess.run(self._command(), env={**os.environ, "COFORM_FREECAD_JOB": str(job_path)},
                                       capture_output=True, text=True, timeout=self.TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise ValueError(f"FreeCAD did not finish within {self.TIMEOUT_S} s") from None
        if not result_path.is_file():
            tail = (completed.stderr or completed.stdout or "")[-300:].strip()
            raise ValueError(f"FreeCAD exited ({completed.returncode}) without a result. {tail}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        job_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)
        return result

    def status(self):
        try:
            result = self._run({"action": "version"})
        except ValueError as exc:
            return {"available": False, "detail": str(exc)}
        masters = sorted(p.name for p in self.directory.glob("master_*.FCStd"))
        return {"available": result["ok"], "freecad_version": result.get("freecad_version"),
                "executable": self._command()[0], "masters": masters, "directory": str(self.directory)}

    def _file(self, name, expected_hash=None):
        path = (self.directory / name).resolve()
        if path.parent != self.directory.resolve() or not path.is_file():
            raise ValueError("FreeCAD artifact missing")
        if expected_hash and _sha(path.read_bytes()) != expected_hash:
            raise ValueError("FreeCAD artifact modified since it was recorded")
        return path

    def sync_candidate(self, run_id, candidate_id):
        """Set the master's parameters, rebuild, snapshot, export, and check against the physics."""
        with self.lock:
            candidate = next((c for c in self.store.list(run_id, "candidate") if c["id"] == candidate_id), None)
            if candidate is None:
                raise ValueError("Candidate does not belong to this study")
            prior = [r for r in self.store.list(run_id, "cad_revision")
                     if r["candidate_id"] == candidate_id and r["source"] == "freecad" and r["check"]["status"] == "pass"]
            if prior:
                return prior[-1]
            geometry = Geometry(**candidate["geometry"])
            values = {name: round(getattr(geometry, key) * 1000, 6) for name, key in VARIABLES.items()}
            files = {"fcstd": candidate_id + ".FCStd", "step": candidate_id + ".step", "stl": candidate_id + ".stl"}
            master = self.directory / f"master_{geometry.section_family}.FCStd"
            result = self._run({"action": "build", "family": geometry.section_family, "values_mm": values,
                                "master": str(master), **{k: str(self.directory / v) for k, v in files.items()}})
            if not result.get("ok"):
                raise ValueError(result.get("error") or "FreeCAD rebuild failed")
            raw = self._file(files["stl"]).read_bytes()
            metrics = mesh_metrics(raw)
            check = check_mesh(geometry, metrics)
            section = mechanics.section_properties(geometry)
            # The kernel's solid gives an independent check of the hand-derived section formulas.
            for name, actual, expected in (
                    ("freecad_kernel_volume", result["volume_mm3"], section["area_m2"] * geometry.length_m * 1e9),
                    ("freecad_kernel_second_moment", result["second_moment_mm4"], section["second_moment_m4"] * 1e12)):
                check["checks"].append({"name": name, "passed": abs(actual - expected) <= VOLUME_TOLERANCE * expected,
                                        "expected": expected, "actual": actual, "relative_tolerance": VOLUME_TOLERANCE})
            check["status"] = "pass" if all(c["passed"] for c in check["checks"]) else "fail"
            asset = self.manufacturing.add_asset(f"{candidate_id}-freecad.stl", raw, run_id=run_id,
                                                 candidate_id=candidate_id, source="freecad_export")
            return self.store.put("cad_revision", run_id, {
                "id": identifier("cad"), "run_id": run_id, "candidate_id": candidate_id, "source": "freecad",
                "onshape": None, "variables": {k: f"{v:g} mm" for k, v in values.items()},
                "freecad": {"version": result["freecad_version"], "master": master.name,
                            "master_sha256": _sha(master.read_bytes()) if master.is_file() else None,
                            "fcstd": files["fcstd"], "fcstd_sha256": _sha(self._file(files["fcstd"]).read_bytes()),
                            "step": files["step"], "step_sha256": _sha(self._file(files["step"]).read_bytes())},
                "kernel": {k: result[k] for k in ("volume_mm3", "area_mm2", "second_moment_mm4", "extents_mm", "solids", "is_valid")},
                "asset_id": asset["id"], "stl_sha256": asset["sha256"], "mesh": metrics, "onshape_volume_m3": None,
                "check": check, "created_at": utc_now()})

    def open_in_gui(self, run_id, cad_id):
        """Open a candidate snapshot in the FreeCAD desktop app on this computer."""
        import subprocess
        revision = self.store.get(cad_id, "cad_revision")
        if revision["run_id"] != run_id or revision["source"] != "freecad":
            raise ValueError("Not a FreeCAD revision of this study")
        path = self._file(revision["freecad"]["fcstd"], revision["freecad"]["fcstd_sha256"])
        gui = Path(self._command()[0]).with_name("FreeCAD.exe")
        if not gui.is_file():
            raise ValueError("FreeCAD desktop app not found next to FreeCADCmd")
        subprocess.Popen([str(gui), str(path)], close_fds=True)
        return {"opened": path.name}


def build_cad(run_id, candidate_id, *, fusion=None, freecad=None):
    """Fusion when its add-in is connected (people can edit there); otherwise FreeCAD."""
    if fusion is not None and fusion.status()["connected"]:
        return fusion.build_and_wait(run_id, candidate_id)
    if freecad is None:
        raise ValueError("No CAD tool available: open Fusion with the CoForm add-in, or install FreeCAD")
    return freecad.sync_candidate(run_id, candidate_id)
