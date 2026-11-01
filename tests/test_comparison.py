import hashlib
import json
import zipfile

import pytest

from coform.cli import main
from coform.comparison import export_run, run_comparison
from coform.service import CoForm


def test_paired_comparison_uses_identical_initial_evidence_and_budget(tmp_path):
    report = run_comparison([7], tmp_path / "comparison")
    assert report["mode"] == "simulated"
    assert report["pairs"][0]["shared_initial_cycles"] == 4
    assert len(report["results"]) == 2
    for result in report["results"]:
        assert result["mode"] == "simulated"
        assert result["model_calls"] == 0
        assert result["reported_tokens"] == 0
        assert result["budget"]["cycles_used"] <= report["protocol"]["search_cycles"]
        assert result["budget"]["designs_used"] <= report["protocol"]["max_designs"]
        assert result["acceptance_status"] != "physical_pass"
        assert result["independent_specimens_tested"] == report["protocol"]["final_copies"]
        assert result["measured_mass_kg"] is None
        assert result["reserved_mean_absolute_error_m"] is not None
    assert (tmp_path / "comparison" / "comparison.csv").is_file()
    with pytest.raises(FileExistsError):
        run_comparison([7], tmp_path / "comparison")


def test_export_contains_reconstructable_records_and_verified_hashes(tmp_path):
    service = CoForm(tmp_path / "data")
    try:
        state = service.create_run(seed=10)
        run_id = state["run"]["id"]
        archive = export_run(service, run_id, tmp_path / "exports")
        with zipfile.ZipFile(archive) as exported:
            names = set(exported.namelist())
            assert {"state.json", "records.json", "events.json", "metrics.json", "manifest.json"} <= names
            manifest = json.loads(exported.read("manifest.json"))
            assert manifest["mode"] == "simulated"
            assert {entry["path"] for entry in manifest["files"]} == names - {"manifest.json"}
            for entry in manifest["files"]:
                raw = exported.read(entry["path"])
                assert len(raw) == entry["bytes"]
                assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
            records = json.loads(exported.read("records.json"))
            assert sum(record["kind"] == "observation" for record in records) == 4
        with pytest.raises(FileExistsError):
            export_run(service, run_id, tmp_path / "exports")
    finally:
        service.store.close()


def test_export_refuses_corrupted_artifacts(tmp_path):
    service = CoForm(tmp_path / "data")
    try:
        state = service.create_run(seed=10)
        name = state["candidates"][0]["artifacts"]["stl"]["filename"]
        (service.store.artifacts / name).write_text("corrupted", encoding="utf-8")
        with pytest.raises(ValueError, match="hash mismatch"):
            export_run(service, state["run"]["id"], tmp_path / "exports")
    finally:
        service.store.close()


def test_comparison_rejects_invalid_or_duplicate_seeds(tmp_path):
    for seeds in ([], [-1], [7, 7], [True]):
        with pytest.raises(ValueError):
            run_comparison(seeds, tmp_path)


def test_cli_errors_are_readable(tmp_path, capsys):
    assert main(["export", "unknown", "--data-dir", str(tmp_path / "data")]) == 2
    assert "Unknown run" in capsys.readouterr().err
