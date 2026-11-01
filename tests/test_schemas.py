import pytest
from pydantic import ValidationError

from schemas import CandidateRecord, CheckResult, GeometryParams, Message, PrintabilityResult, \
    PrintSettings, Quantity


def test_failed_analysis_cannot_carry_a_value():
    with pytest.raises(ValidationError):
        Quantity(value=1.0, unit="mm", source="fea", status="failed")
    with pytest.raises(ValidationError):
        Quantity(unit="mm", source="analytical", status="completed")


def test_convergence_flag_is_fea_only():
    with pytest.raises(ValidationError):
        Quantity(value=1.0, unit="mm", source="surrogate", status="completed", converged=True)
    with pytest.raises(ValidationError):
        Quantity(value=1.0, unit="mm", source="fea", status="completed")  # must state convergence


def test_only_strong_evidence_counts_for_selection():
    def q(**kw):
        return Quantity(value=1.0, unit="mm", status="completed", **kw)
    assert not q(source="analytical").counts_for_selection
    assert not q(source="surrogate").counts_for_selection
    assert not q(source="synthetic").counts_for_selection
    assert not q(source="fea", converged=False).counts_for_selection
    assert not q(source="calibrated").counts_for_selection
    assert q(source="fea", converged=True).counts_for_selection
    assert q(source="calibrated", validated=True).counts_for_selection
    assert q(source="measured").counts_for_selection


def test_failed_printability_check_is_never_a_pass():
    with pytest.raises(ValidationError):
        CheckResult(name="thin-features", status="failed", passed=False)
    failed = CheckResult(name="thin-features", status="failed")
    ok = CheckResult(name="cups", status="completed", passed=True)
    result = PrintabilityResult(print_settings=PrintSettings(), checks={"a": ok, "b": failed})
    assert not result.all_checks_passed
    assert not PrintabilityResult(print_settings=PrintSettings(), checks={}).all_checks_passed
    assert PrintabilityResult(print_settings=PrintSettings(), checks={"a": ok}).all_checks_passed


def test_geometry_bounds_and_stable_ids():
    with pytest.raises(ValidationError):
        GeometryParams(plate_thickness=0.5, rib_height=30, rib_thickness=5)
    with pytest.raises(ValidationError):
        GeometryParams(plate_thickness=4, rib_height=57, rib_thickness=5)
    a = GeometryParams(plate_thickness=4, rib_height=30, rib_thickness=5)
    b = GeometryParams(plate_thickness=4.0, rib_height=30.0, rib_thickness=5.0)
    c = GeometryParams(plate_thickness=4, rib_height=31, rib_thickness=5)
    assert a.candidate_id() == b.candidate_id() != c.candidate_id()


def test_store_round_trip(tmp_path, monkeypatch):
    import store
    monkeypatch.setattr(store, "RUNS_DIR", tmp_path)
    p = GeometryParams(plate_thickness=4, rib_height=30, rib_thickness=5)
    record = CandidateRecord(candidate_id=p.candidate_id(), params=p, proposed_by="human")
    store.save_record("r1", record)
    assert store.load_record("r1", p.candidate_id()) == record
    assert store.list_records("r1") == [record]
    store.append_message("r1", Message(agent="mechanic", round=0, text="hi"))
    assert store.read_messages("r1")[0].text == "hi"
