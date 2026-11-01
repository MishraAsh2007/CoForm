import json

import compare
import main
import store
import structural_evaluator as se
from test_baselines import fake_pipeline  # noqa: F401


def test_main_run_and_report(fake_pipeline):  # noqa: F811
    try:
        main.main(["--run-id", "t-optimizer-s1", "--mode", "optimizer", "--no-preform",
                   "--calibration-start", "reference", "--budget-l1", "300", "--budget-l2-fea", "2",
                   "--budget-preform-prep", "1", "--budget-llm-calls", "0"])
    finally:
        se.use_shared_calibration()
    summary = json.loads((store.RUNS_DIR / "t-optimizer-s1" / "run.json").read_text())
    assert summary["seed"] == 0 and summary["elapsed_s"] >= 0 and summary["budget_used"]["l2_fea"] >= 1
    compare.report("t")
    data = json.loads((store.RUNS_DIR / "t-report.json").read_text())
    (row,) = data["runs"]
    assert row["mode"] == "optimizer" and row["selected_mass_g"] is None   # no PreForm, no selection
    assert row["lightest_structurally_qualified_g"] is not None
    assert "| optimizer | 1 | 0/1 |" in (store.RUNS_DIR / "t-report.md").read_text()
