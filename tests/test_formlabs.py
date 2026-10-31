from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import inspect
import struct

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("formlabs_sdk")
from formlabs_sdk import FormlabsClient
from scienceclaw.app import create_app
from scienceclaw.domain import Geometry
from scienceclaw.formlabs import Manufacturing, PrintProfile, FormlabsError, inspect_scene, validate_stl
from scienceclaw.mechanics import generate_stl as stl
from scienceclaw.store import Store


PROFILE = PrintProfile(machine_type="FORM-4-0", material_code="FLGPGR05", layer_thickness_mm=.1, minimum_wall_mm=.5)


class FakePreForm:
    """In-memory device double. Every method call is bound to the installed SDK signature."""
    version = "test-double-not-a-real-printer"

    def __init__(self):
        self.calls = []
        self.settings = PROFILE.scene_settings()
        self.fail_upload = False
        self.bad_setting = False
        self.check_overrides = {}
        self.device = {"id": "test-printer", "product_name": "Form 4", "is_connected": True,
                       "tank_material_code": "FLGPGR05", "ready_to_print_now": True}
        with FormlabsClient() as client:
            self.signatures = {name: inspect.signature(getattr(client.preform, name)) for name in (
                "list_devices", "list_print_settings", "validate_print_setting", "create_scene", "import_models",
                "auto_orient_models", "auto_support_models", "auto_layout_models", "get_scene", "calculate_print_time",
                "save_form_file", "open_form_file", "print_scene", "validate_manifoldness", "validate_in_bounds",
                "validate_thin_features", "validate_cups", "validate_minima", "validate_supportedness", "validate_interferences")}

    def __getattr__(self, name):
        def call(**kwargs):
            self.signatures[name].bind(**kwargs)
            self.calls.append((name, kwargs))
            if name in self.check_overrides:
                return deepcopy(self.check_overrides[name])
            if name == "list_devices":
                return {"devices": [self.device]}
            if name == "list_print_settings":
                return {"printer_types": [{"label": "Form 4", "supported_machine_type_ids": ["FORM-4-0"]}]}
            if name == "validate_print_setting":
                return {"valid": not self.bad_setting}
            if name == "create_scene":
                self.settings = kwargs
                return {"scene_id": "original-scene"}
            if name == "import_models":
                assert str(kwargs["units"]) == "MILLIMETERS"
                assert str(kwargs["repair_behavior"]) == "ERROR"
                return {"new_models": [{"model_id": "model-one"}]}
            rows = {"manifoldness": {"is_manifold": True, "status": "MANIFOLD"}, "in_bounds": {"in_bounds": True},
                    "thin_features": {"status": "ANALYZED", "has_thin_walls": False}, "cups": {"cup_count": 0},
                    "minima": {"minima_count": 0}, "supportedness": {"percent_unsupported": 0}}
            if name.removeprefix("validate_") in rows:
                return {"per_model_results": {"model-one": rows[name.removeprefix("validate_")]}}
            if name == "validate_interferences":
                return {"model_ids": []}
            if name == "get_scene":
                return {"scene_settings": self.settings, "material_usage": {"volume_ml": 8}}
            if name == "calculate_print_time":
                return {"total_print_time_s": 3600}
            if name == "save_form_file":
                Path(kwargs["file"]).write_bytes(b"saved-project-test-double")
            if name == "open_form_file":
                return {"scenes": [{"scene_id": "reopened-scene", "new_model_ids": ["model-one"], "scene_settings": self.settings}]}
            if name == "print_scene":
                if self.fail_upload:
                    raise TimeoutError("Ambiguous remote upload")
                return {"job_id": "fake-job-not-printed"}
        return call


@pytest.fixture
def manufacturing(tmp_path):
    fake = FakePreForm()
    @contextmanager
    def connect():
        yield fake
    store = Store(tmp_path)
    manager = Manufacturing(store, connect)
    yield manager, fake
    store.close()


def prepared(manager):
    asset = manager.add_asset("Onshape-export.stl", stl(Geometry()).encode())
    return manager.prepare(asset["id"], PROFILE)


def test_full_prepare_review_queue_preserves_provenance_and_never_starts(manufacturing):
    manager, fake = manufacturing
    project = prepared(manager)
    assert project["validation"]["grade"] == "PASS"
    assert not any(name == "print_scene" for name, _ in fake.calls)
    receipt = manager.queue(project["id"], "test-printer", "Reviewed supports, settings, dimensions and orientation")
    assert receipt["status"] == "queued" and receipt["physical_result"] is None
    calls = [(n, kw) for n, kw in fake.calls if n == "print_scene"]
    assert calls[0][1]["print_now"] is False
    assert calls[0][1]["scene_id"] == "reopened-scene"
    assert manager.store.verify()
    with pytest.raises(ValueError, match="already has"):
        manager.queue(project["id"], "test-printer", "Reviewed")
    assert len([n for n, _ in fake.calls if n == "print_scene"]) == 1


def test_unknown_upload_outcome_persists_and_blocks_retry_after_restart(manufacturing):
    manager, fake = manufacturing
    project = prepared(manager)
    fake.fail_upload = True
    with pytest.raises(FormlabsError, match="outcome unknown"):
        manager.queue(project["id"], "test-printer", "Reviewed")
    restarted = Manufacturing(manager.store, manager.connect)
    with pytest.raises(ValueError, match="already has"):
        restarted.queue(project["id"], "test-printer", "Reviewed")
    assert manager.store.list(None, "print_receipt")[0]["status"] == "upload_outcome_unknown"
    assert len([n for n, _ in fake.calls if n == "print_scene"]) == 1


@pytest.mark.parametrize("method,result", [
    ("validate_manifoldness", {"per_model_results": {}}),
    ("validate_in_bounds", {"per_model_results": {"model-one": {"in_bounds": False}}}),
    ("validate_thin_features", {"per_model_results": {"model-one": {"status": "MESH_ERROR", "has_thin_walls": False}}}),
    ("validate_cups", {"per_model_results": {"model-one": {"cup_count": -1}}}),
    ("validate_interferences", {"model_ids": [["model-one", "model-two"]]}),
    ("validate_supportedness", {}),
])
def test_incomplete_or_failed_analysis_cannot_queue(manufacturing, method, result):
    manager, fake = manufacturing
    fake.check_overrides[method] = result
    project = prepared(manager)
    assert project["validation"]["grade"] == "FAIL"
    with pytest.raises(ValueError, match="Resolve failed"):
        manager.queue(project["id"], "test-printer", "Reviewed")
    assert not any(n == "print_scene" for n, _ in fake.calls)


def test_warnings_are_visible_and_review_is_required(manufacturing):
    manager, fake = manufacturing
    fake.check_overrides["validate_cups"] = {"per_model_results": {"model-one": {"cup_count": 1}}}
    project = prepared(manager)
    assert project["validation"]["grade"] == "WARN"
    with pytest.raises(ValueError, match="review"):
        manager.queue(project["id"], "test-printer", " ")


@pytest.mark.parametrize("failure", ["modified_file", "wrong_resin", "disconnected", "changed_checks"])
def test_queue_rechecks_saved_file_and_hardware(manufacturing, failure):
    manager, fake = manufacturing
    project = prepared(manager)
    if failure == "modified_file":
        (manager.directory / project["filename"]).write_bytes(b"modified")
    elif failure == "wrong_resin":
        fake.device["tank_material_code"] = "OTHER"
    elif failure == "disconnected":
        fake.device["is_connected"] = False
    else:
        fake.check_overrides["validate_minima"] = {"per_model_results": {}}
    with pytest.raises(ValueError):
        manager.queue(project["id"], "test-printer", "Reviewed")
    assert not any(n == "print_scene" for n, _ in fake.calls)


def test_bad_material_preset_never_creates_scene(manufacturing):
    manager, fake = manufacturing
    fake.bad_setting = True
    with pytest.raises(ValueError, match="not a valid"):
        prepared(manager)
    assert not any(n == "create_scene" for n, _ in fake.calls)


def test_stl_binary_ascii_and_nonfinite():
    validate_stl(stl(Geometry()).encode())
    raw = b"x" * 80 + struct.pack("<I", 1) + struct.pack("<12fH", *([0.] * 12), 0)
    validate_stl(raw)
    for invalid in (b"not an stl", raw[:-1], raw[:84] + struct.pack("<12fH", float("nan"), *([0.] * 11), 0)):
        with pytest.raises(ValueError):
            validate_stl(invalid)


def test_api_upload_prepare_download_and_candidate_ownership(tmp_path):
    app = create_app(tmp_path)
    fake = FakePreForm()
    @contextmanager
    def connect():
        yield fake
    app.state.manufacturing.connect = connect
    with TestClient(app) as client:
        assert client.get('/api/formlabs/status').json()['connected']
        asset = client.post('/api/formlabs/assets?filename=from-onshape.stl', content=stl(Geometry()).encode()).json()
        response = client.post('/api/formlabs/prepare', json={"asset_id": asset['id'], "profile": PROFILE.model_dump()})
        assert response.status_code == 200
        project = response.json()
        assert client.get(f"/api/formlabs/projects/{project['id']}/file").content == b"saved-project-test-double"
        assert client.post('/api/formlabs/assets?filename=bad.exe', content=b"anything").status_code == 400
        assert client.post('/api/formlabs/assets?filename=part.stl', content=b"x", headers={"Origin":"https://untrusted.example"}).status_code == 403
        one = client.post('/api/runs', json={}).json()
        two = client.post('/api/runs', json={}).json()
        route = f"/api/runs/{one['run']['id']}/formlabs/assets"
        assert client.post(route, json={"candidate_id":two['candidates'][0]['id']}).status_code == 400
        candidate = client.post(route, json={"candidate_id":one['candidates'][0]['id']}).json()
        assert candidate['source'] == 'simulation_fixture'
    app.state.service.store.close()

