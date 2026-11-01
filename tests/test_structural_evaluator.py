import json
import math

import pytest

import config
import structural_evaluator as se
from coordination import Budget, BudgetExceeded
from schemas import GeometryParams, Quantity, StructuralResult

P = GeometryParams(plate_thickness=4, rib_height=30, rib_thickness=5)
DEFL_LIMIT = config.MAX_TIP_DEFLECTION_MM
STRESS_LIMIT = config.TENSILE_STRENGTH_MPA / config.SAFETY_FACTOR
def flat_fit(factor=1.0, rel=0.05):
    """A constant-factor fit with no features and a fixed relative uncertainty."""
    return {"features": [], "coef": [math.log(factor)], "xtx_inv": [[0.0]], "scatter": rel,
            "numerical": 0.0, "ranges": {}, "n": 3}


def fea_row(params, deflection, stress, converged=True):
    q = dict(source="fea", status="completed", converged=converged)
    l2 = StructuralResult(level=2, load_case={}, material={},
                          tip_deflection=Quantity(value=deflection, unit="mm",
                                                  uncertainty=deflection * 0.005, **q),
                          max_stress=Quantity(value=stress, unit="MPa",
                                              uncertainty=stress * 0.005, **q))
    return se._row(params, l2, "reference"), l2


DESIGNS = [GeometryParams(plate_thickness=t, rib_height=h, rib_thickness=rt)
           for t, h, rt in [(2.5, 10, 2.5), (3, 20, 3), (4, 30, 5), (5, 38, 4), (6, 45, 8)]]


CALIBRATED = {"fits": {"tip_deflection": flat_fit(), "max_stress": flat_fit()}}


def l1_result(deflection, stress, rel=0.05):
    def q(value, unit):
        return Quantity(value=value, unit=unit, source="analytical", status="completed",
                        uncertainty=value * rel)
    return StructuralResult(level=1, tip_deflection=q(deflection, "mm"), max_stress=q(stress, "MPa"),
                            load_case={}, material={})


def fake_l2(params, step_path):
    q = dict(source="fea", status="completed", converged=True)
    return StructuralResult(level=2, load_case={}, material={},
                            tip_deflection=Quantity(value=0.4, unit="mm", uncertainty=0.001, **q),
                            max_stress=Quantity(value=20.0, unit="MPa", uncertainty=1.0, **q))


@pytest.fixture
def patched(monkeypatch):
    """Control L1's output and replace FEA with an instant fake that counts calls."""
    calls = []
    state = {"l1": l1_result(0.3, 10.0)}
    monkeypatch.setattr(se, "evaluate_l1_calibrated", lambda params, cal: state["l1"])
    monkeypatch.setattr(se.fea, "evaluate_l2", lambda p, s: calls.append(p) or fake_l2(p, s))
    return state, calls


# --- budget ---------------------------------------------------------------------

def test_budget_counts_and_refuses_overspend():
    b = Budget({"l2_fea": 1})
    assert b.can_spend("l2_fea") and not b.can_spend("unknown")
    b.spend("l2_fea")
    assert not b.can_spend("l2_fea") and b.remaining() == {"l2_fea": 0}
    with pytest.raises(BudgetExceeded):
        b.spend("l2_fea")


# --- helpers --------------------------------------------------------------------

def test_probability_within_limit():
    q = Quantity(value=1.0, unit="mm", source="analytical", status="completed", uncertainty=0.1)
    assert se.probability_within(q, 1.0) == pytest.approx(0.5)
    assert se.probability_within(q, 1.2) == pytest.approx(0.977, abs=1e-3)
    assert se.probability_within(Quantity(unit="mm", source="fea", status="failed"), 1.0) is None


def _rows_with_trend(a, b, features=("depth_ratio",)):
    """FEA rows whose L2/L1 ratio is exactly exp(a + b * feature)."""
    rows = []
    for p in DESIGNS:
        l1 = se._l1_values(p)
        ratio = math.exp(a + b * se.FEATURES[features[0]](p))
        rows.append(fea_row(p, l1["tip_deflection"] * ratio, l1["max_stress"] * ratio)[0])
    return rows


def test_fit_recovers_known_trend_exactly():
    fit = se.fit_quantity(_rows_with_trend(0.1, 0.5), "tip_deflection", ["depth_ratio"])
    assert fit["coef"] == pytest.approx([0.1, 0.5])
    assert fit["scatter"] == pytest.approx(0.0, abs=1e-9) and fit["loo_rmse"] < 1e-9
    p = DESIGNS[2]
    assert se.correction(fit, p)[0] == pytest.approx(math.exp(0.1 + 0.5 * se.analytical.depth_ratio(p)))


def test_fit_needs_more_rows_than_coefficients():
    rows = _rows_with_trend(0.1, 0.5)[:2]
    assert se.fit_quantity(rows, "tip_deflection", ["depth_ratio"]) is None
    assert se.fit_quantity(rows, "tip_deflection", []) is not None


def test_prediction_uncertainty_grows_away_from_the_data():
    rows = _rows_with_trend(0.1, 0.5)
    rows[1]["l2"]["tip_deflection"]["value"] *= 1.03   # some scatter
    fit = se.fit_quantity(rows, "tip_deflection", ["depth_ratio"])
    inside = se.correction(fit, GeometryParams(plate_thickness=4, rib_height=28, rib_thickness=5))
    outside = se.correction(fit, GeometryParams(plate_thickness=8, rib_height=50, rib_thickness=5))
    assert inside[2] is None and "extrapolating" in outside[2]
    assert outside[1] > inside[1]


def test_record_fea_adds_converged_results_and_refits(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "L1_CALIBRATION_FEATURES",
                        {"tip_deflection": ["depth_ratio"], "max_stress": ["depth_ratio"]})
    path = tmp_path / "cal.json"
    _, unconverged = fea_row(DESIGNS[0], 1.0, 10.0, converged=False)
    assert not se.record_fea(DESIGNS[0], unconverged, path=path) and not path.exists()
    for p in DESIGNS[:3]:
        l1 = se._l1_values(p)
        assert se.record_fea(p, fea_row(p, l1["tip_deflection"] * 1.2, l1["max_stress"] * 1.8)[1],
                             path=path)
    data = se.load_calibration(path)
    assert len(data["rows"]) == 3 and data["fits"]["tip_deflection"]["n"] == 3
    assert math.exp(data["fits"]["max_stress"]["coef"][0]) == pytest.approx(1.8, rel=1e-6)
    # Re-recording the same design replaces it rather than duplicating it.
    se.record_fea(DESIGNS[0], fea_row(DESIGNS[0], 1.0, 10.0)[1], path=path)
    assert len(se.load_calibration(path)["rows"]) == 3


def test_calibration_ignored_when_geometry_changes(tmp_path, monkeypatch):
    path = tmp_path / "cal.json"
    path.write_text(json.dumps({"fingerprint": se._fingerprint(), "rows": []}))
    assert se.load_calibration(path) is not None
    monkeypatch.setattr(config, "RIB_FILLET_RADIUS", 2.0)
    assert se.load_calibration(path) is None


def test_calibrated_l1_scales_values_and_says_so():
    cal = {"fits": {"tip_deflection": flat_fit(1.25, 0.1), "max_stress": None}}
    raw = se.analytical.evaluate_l1(P)
    corrected = se.evaluate_l1_calibrated(P, cal)
    assert corrected.tip_deflection.value == pytest.approx(raw.tip_deflection.value * 1.25)
    assert corrected.tip_deflection.uncertainty == pytest.approx(corrected.tip_deflection.value * 0.1)
    assert corrected.max_stress == raw.max_stress   # no stress fit: unchanged
    assert corrected.tip_deflection.source == "analytical"
    assert any("fitted to converged L2" in a for a in corrected.assumptions)


# --- L1 verdict -------------------------------------------------------------------

def test_l1_verdicts_with_calibrated_stress():
    assert se.l1_verdict(l1_result(DEFL_LIMIT * 2, 1.0), True) == "infeasible"
    assert se.l1_verdict(l1_result(DEFL_LIMIT * 0.5, STRESS_LIMIT * 0.5), True) == "feasible"
    assert se.l1_verdict(l1_result(DEFL_LIMIT * 0.99, STRESS_LIMIT * 0.5), True) == "ambiguous"


def test_uncalibrated_l1_stress_can_only_prove_violation():
    # Far below the limit, but nominal stress is a lower bound: not proof of feasibility.
    assert se.l1_verdict(l1_result(DEFL_LIMIT * 0.1, STRESS_LIMIT * 0.1), False) == "ambiguous"
    assert se.l1_verdict(l1_result(DEFL_LIMIT * 0.1, STRESS_LIMIT * 1.01), False) == "infeasible"


# --- level selection --------------------------------------------------------------

def test_clearly_infeasible_candidate_skips_fea(patched):
    state, calls = patched
    state["l1"] = l1_result(DEFL_LIMIT * 3, STRESS_LIMIT * 0.5)
    ev = se.evaluate(P, "b.step", accuracy="selection", calibration=CALIBRATED)
    assert ev.l1_verdict == "infeasible" and not calls and len(ev.results) == 1


def test_ambiguous_candidate_escalates_to_fea(patched):
    state, calls = patched
    state["l1"] = l1_result(DEFL_LIMIT * 0.98, STRESS_LIMIT * 0.5)
    budget = Budget({"l1": 5, "l2_fea": 5})
    ev = se.evaluate(P, "b.step", budget=budget, calibration=CALIBRATED)
    assert len(calls) == 1 and [r.level for r in ev.results] == [1, 2]
    assert budget.used == {"l1": 1, "l2_fea": 1}


def test_clear_pass_is_enough_for_screening_but_not_selection(patched):
    state, calls = patched
    state["l1"] = l1_result(DEFL_LIMIT * 0.3, STRESS_LIMIT * 0.3)
    assert len(se.evaluate(P, "b.step", accuracy="screening", calibration=CALIBRATED).results) == 1
    ev = se.evaluate(P, "b.step", accuracy="selection", calibration=CALIBRATED)
    assert [r.level for r in ev.results] == [1, 2]
    assert any("L3 not available" in d for d in ev.decisions)


def test_exhausted_budget_or_missing_geometry_blocks_fea(patched):
    state, calls = patched
    state["l1"] = l1_result(DEFL_LIMIT * 0.98, STRESS_LIMIT * 0.5)
    ev = se.evaluate(P, "b.step", budget=Budget({"l1": 5, "l2_fea": 0}), calibration=CALIBRATED)
    assert not calls and any("budget is exhausted" in d for d in ev.decisions)
    ev = se.evaluate(P, None, calibration=CALIBRATED)
    assert not calls and any("no STEP geometry" in d for d in ev.decisions)


def test_explicit_request_forces_fea(patched):
    state, calls = patched
    state["l1"] = l1_result(DEFL_LIMIT * 0.1, STRESS_LIMIT * 0.1)
    ev = se.evaluate(P, "b.step", request_level=2, calibration=CALIBRATED)
    assert len(calls) == 1 and any("explicitly requested" in d for d in ev.decisions)


def test_runs_get_isolated_calibration_copies(tmp_path, monkeypatch):
    import store
    monkeypatch.setattr(store, "RUNS_DIR", tmp_path / "runs")
    shared = tmp_path / "shared.json"
    monkeypatch.setattr(config, "L1_CALIBRATION_PATH", str(shared))
    rows = [fea_row(p, 1.0, 10.0)[0] for p in DESIGNS[:3]]
    rows[2]["source"] = "experiment"
    shared.write_text(json.dumps({"fingerprint": se._fingerprint(), "rows": rows}))
    try:
        copy = se.use_run_calibration("r1", sources=("reference",))
        assert [r["source"] for r in se.load_calibration()["rows"]] == ["reference", "reference"]
        # Learning during the run goes to the run's copy only.
        assert se.record_fea(DESIGNS[3], fea_row(DESIGNS[3], 1.0, 10.0)[1])
        assert len(json.loads(copy.read_text())["rows"]) == 3
        assert len(json.loads(shared.read_text())["rows"]) == 3
        se.use_shared_calibration()
        assert se.promote_run_calibration("r1") == 1   # only DESIGNS[3] is new to the shared file
        assert len(json.loads(shared.read_text())["rows"]) == 4
    finally:
        se.use_shared_calibration()


MORE_DESIGNS = DESIGNS + [GeometryParams(plate_thickness=t, rib_height=h, rib_thickness=rt)
                         for t, h, rt in [(3.7, 49, 3.2), (4, 30, 2), (3, 15, 8), (2, 25, 7),
                                          (7, 20, 2.5), (3.2, 50, 2.5)]]


def _rows_for(designs, a, b, noise_row=1, noise=1.01):
    rows = []
    for i, p in enumerate(designs):
        l1 = se._l1_values(p)
        ratio = math.exp(a + b * se.analytical.depth_ratio(p)) * (noise if i == noise_row else 1.0)
        rows.append(fea_row(p, l1["tip_deflection"] * ratio, l1["max_stress"] * ratio)[0])
    return rows


def test_automatic_feature_selection_applies_the_parsimony_rule():
    features, table = se.select_features(_rows_for(MORE_DESIGNS, 0.1, 0.5), "tip_deflection")
    assert features == ["depth_ratio"]
    assert table[0]["loo_rmse"] <= table[-1]["loo_rmse"]
    # A constant ratio: the empty set is simplest and as good as anything.
    assert se.select_features(_rows_for(MORE_DESIGNS, 0.2, 0.0), "max_stress")[0] == []


def test_feature_selection_needs_enough_rows_per_coefficient():
    # Five designs cannot support even one feature (needs 6 rows): only the constant is allowed.
    features, table = se.select_features(_rows_for(DESIGNS, 0.1, 0.5), "tip_deflection")
    assert features == [] and all(r["features"] == [] for r in table)
