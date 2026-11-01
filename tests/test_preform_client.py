import os
import socket

import pytest

import config
import preform_client
from schemas import PrintSettings

MODEL = "m1"


class FakePreForm:
    """Minimal stand-in for the SDK handle; methods return response dicts."""
    version = "fake"

    def __init__(self, **overrides):
        self.overrides = overrides
        self.calls = []
        self.deleted = []

    def _r(self, name, default):
        self.calls.append(name)
        value = self.overrides.get(name, default)
        if isinstance(value, Exception):
            raise value
        return value

    def create_scene(self, **kw):
        return self._r("create_scene", {"scene_id": "s1"})

    def import_models(self, **kw):
        return self._r("import_models", {"new_models": [{"model_id": MODEL}]})

    def auto_orient_models(self, **kw):
        return self._r("auto_orient_models", {})

    def orient_models(self, **kw):
        return self._r("orient_models", {})

    def auto_support_models(self, **kw):
        return self._r("auto_support_models", {})

    def auto_layout_models(self, **kw):
        return self._r("auto_layout_models", {})

    def validate_minima(self, **kw):
        return self._r("minima", {"per_model_results": {MODEL: {"minima_count": 0}}})

    def validate_supportedness(self, **kw):
        return self._r("supportedness", {"per_model_results": {MODEL: {"percent_unsupported": 0.0}}})

    def validate_thin_features(self, **kw):
        return self._r("thin_features", {"per_model_results": {MODEL: {
            "status": "ANALYZED", "has_thin_walls": False, "thin_volume_mm3": 0,
            "thin_region_count": 0, "thin_regions": []}}})

    def validate_cups(self, **kw):
        return self._r("cups", {"per_model_results": {MODEL: {"cup_count": 0}}})

    def validate_interferences(self, **kw):
        return self._r("interferences", {"model_ids": []})

    def validate_manifoldness(self, **kw):
        return self._r("manifoldness", {"per_model_results": {MODEL: {
            "is_manifold": True, "status": "MANIFOLD"}}})

    def validate_in_bounds(self, **kw):
        return self._r("in_bounds", {"per_model_results": {MODEL: {"in_bounds": True}}})

    def calculate_print_time(self, **kw):
        return self._r("print_time", {"total_print_time_s": 3600})

    def get_scene(self, **kw):
        return self._r("get_scene", {"material_usage": {"volume_ml": 12.5}})

    def save_form_file(self, **kw):
        return self._r("save_form_file", None)

    def delete_scene(self, scene_id):
        self.deleted.append(scene_id)


def test_clean_preparation_passes_and_cleans_up():
    fake = FakePreForm()
    result = preform_client.prepare_print(fake, "b.stl", PrintSettings())
    assert result.all_checks_passed and result.error is None
    assert result.print_time.value == 3600 and result.resin_volume.value == 12.5
    assert "auto_orient_models" in fake.calls and fake.deleted == ["s1"]


def test_unanalyzed_thin_wall_result_is_a_failure_not_a_pass():
    fake = FakePreForm(thin_features={"per_model_results": {MODEL: {
        "status": "MESH_ERROR", "has_thin_walls": False, "thin_volume_mm3": 0,
        "thin_region_count": 0, "thin_regions": []}}})
    result = preform_client.prepare_print(fake, "b.stl", PrintSettings())
    thin = result.checks["thin_features"]
    assert thin.status == "failed" and thin.passed is None
    assert thin.details["status"] == "MESH_ERROR"
    assert not result.all_checks_passed


def test_check_error_and_missing_model_are_failures():
    fake = FakePreForm(cups=RuntimeError("server hiccup"),
                       minima={"per_model_results": {}})
    result = preform_client.prepare_print(fake, "b.stl", PrintSettings())
    assert result.checks["cups"].status == "failed"
    assert "server hiccup" in result.checks["cups"].details["error"]
    assert result.checks["minima"].status == "failed"
    assert not result.all_checks_passed


def test_detected_problems_fail_their_checks():
    fake = FakePreForm(
        cups={"per_model_results": {MODEL: {"cup_count": 2}}},
        supportedness={"per_model_results": {MODEL: {
            "percent_unsupported": config.MAX_UNSUPPORTED_PERCENT + 1}}})
    result = preform_client.prepare_print(fake, "b.stl", PrintSettings())
    assert result.checks["cups"].passed is False
    assert result.checks["supportedness"].passed is False
    assert result.checks["minima"].passed is True


def test_preparation_failure_leaves_checks_not_evaluated():
    fake = FakePreForm(import_models=RuntimeError("bad mesh"))
    result = preform_client.prepare_print(fake, "b.stl", PrintSettings())
    assert "bad mesh" in result.error
    assert all(c.status == "not_evaluated" for c in result.checks.values())
    assert result.print_time is None and fake.deleted == ["s1"]


def test_explicit_orientation_skips_auto_orient():
    fake = FakePreForm()
    preform_client.prepare_print(fake, "b.stl", PrintSettings(orientation_deg=(10, 0, 0)))
    assert "orient_models" in fake.calls and "auto_orient_models" not in fake.calls


def _preform_reachable():
    try:
        with socket.create_connection(("localhost", config.PREFORM_PORT), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.preform
@pytest.mark.skipif(os.getenv("RUN_PREFORM_TESTS") != "1" or not _preform_reachable(),
                    reason="set RUN_PREFORM_TESTS=1 with PreForm Server running (takes ~75 s)")
def test_live_preparation(tmp_path):
    import bracket
    stl, step = tmp_path / "b.stl", tmp_path / "b.step"
    bracket.build_bracket(4, 30, 5, stl, step)
    with preform_client.connect() as preform:
        result = preform_client.prepare_print(preform, stl, PrintSettings(),
                                              form_path=tmp_path / "b.form")
    assert result.error is None
    assert all(c.status == "completed" for c in result.checks.values())
    assert result.print_time.status == "completed" and (tmp_path / "b.form").exists()
