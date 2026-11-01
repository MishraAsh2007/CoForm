import pytest

import analytical
import baselines
import store
import structural_evaluator as se
from coordination import Budget
from schemas import Quantity, StructuralResult
from test_preform_client import FakePreForm


@pytest.fixture
def fake_pipeline(tmp_path, monkeypatch):
    """Real pipeline code with fake geometry, FEA (= calibrated L1, converged) and PreForm."""
    import bracket
    import config
    monkeypatch.setattr(store, "RUNS_DIR", tmp_path)
    # The pipeline feeds FEA results into the calibration file; keep tests off the real one.
    monkeypatch.setattr(config, "L1_CALIBRATION_PATH", str(tmp_path / "cal.json"))
    fea_calls = []

    def fake_build(t, h, rt, stl, step, timeout=120):
        for path in (stl, step):
            path.write_text("fake")
        from schemas import GeometryParams
        p = GeometryParams(plate_thickness=t, rib_height=h, rib_thickness=rt)
        return {"volume_mm3": analytical.volume_mm3(p), "freecad_version": "fake"}

    def fake_l2(params, step_path):
        fea_calls.append(params)
        l1 = se.evaluate_l1_calibrated(params, se.load_calibration())
        def q(src):
            return Quantity(value=src.value, unit=src.unit, source="fea", status="completed",
                            converged=True, uncertainty=src.value * 0.01)
        return StructuralResult(level=2, tip_deflection=q(l1.tip_deflection),
                                max_stress=q(l1.max_stress), load_case={}, material={})

    monkeypatch.setattr(bracket, "build_bracket", fake_build)
    monkeypatch.setattr(se.fea, "evaluate_l2", fake_l2)
    return fea_calls


def limits():
    return {"l1": 400, "l2_fea": 3, "preform_prep": 2, "llm_calls": 0}


@pytest.mark.parametrize("method", [baselines.random_search, baselines.optimizer_baseline])
def test_baseline_selects_within_identical_budget(fake_pipeline, method):
    budget = Budget(limits())
    report, model = method("run", budget, seed=0, preform=FakePreForm())
    assert report.selected is not None
    assert all(budget.used[k] <= limits()[k] for k in limits())
    assert budget.used["l2_fea"] == len(fake_pipeline) >= 1
    chosen = next(a for a in report.candidates if a.status == "selected")
    feasible_l1 = [e for e in model.history if e["feasible"]]
    assert chosen.mass_g == pytest.approx(min(e["mass"] for e in feasible_l1), rel=1e-6)


def test_without_preform_nothing_is_selected(fake_pipeline):
    report, _ = baselines.optimizer_baseline("run", Budget(limits()), seed=0, preform=None)
    assert report.selected is None
    assert any(a.status == "print_not_evaluated" for a in report.candidates)
