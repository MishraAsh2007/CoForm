"""Coordination rules: ownership, acceptance, discussion, budget, plus one mock run end to end."""
import json

import pytest

from core import protocol


def R(n, amt=1.0):
    return {"passed": n == 0, "failures": [{"code": "x", "msg": "m", "amount_mm": amt}] * n}


def test_ownership():
    assert protocol.can_edit("fit", [{"op": "replace", "path": "/clearances/lid_fit", "value": 0.3}])
    assert not protocol.can_edit("fit", [{"op": "replace", "path": "/walls/wall", "value": 3}])
    assert protocol.forbidden_keys("assembly", [{"op": "replace", "path": "/fasteners/lid", "value": "pilot"}]) == ["fasteners"]
    assert not protocol.can_edit("design", [{"op": "replace", "path": "/walls/wall", "value": 3}])
    assert protocol.can_edit("generalist", [{"op": "replace", "path": "/walls/wall", "value": 3}])
    assert protocol.owners_needed("assembly", [{"op": "replace", "path": "/standoffs/height", "value": 3}]) == ["fit"]


def test_improve_phase_needs_quality_gain():
    ok = {"a": R(0), "b": R(0)}
    assert protocol.decide(ok, ok, ["x"], [], 50.0, 51.0)[0] == "accepted"
    assert protocol.decide(ok, ok, ["x"], [], 50.0, 49.0)[0] == "rejected"
    assert protocol.decide(ok, {"a": R(1), "b": R(0)}, ["x"], [], 50.0, 60.0)[0] == "rejected"


def test_failure_diff():
    old = {"assembly": {"passed": False, "failures": [{"code": "lid", "msg": "lid gap", "amount_mm": 0.3, "target": "lid_gap"},
                                                       {"code": "tabs", "msg": "0 tabs", "amount_mm": 4, "kind": "count"}]}}
    new = {"assembly": {"passed": False, "failures": [{"code": "tabs", "msg": "1 tabs", "amount_mm": 3, "kind": "count"},
                                                       {"code": "plug", "msg": "hdmi0 zone", "amount_mm": 1, "target": "hdmi0"}]}}
    d = protocol.failure_diff(old, new)
    assert d["fixed"] == ["assembly: lid gap"] and d["new"] == ["assembly: hdmi0 zone"] and len(d["better"]) == 1


def test_open_needs_biggest_first():
    needs = [{"to": "fit", "status": "open", "amount_mm": 1}, {"to": "fit", "status": "open", "amount_mm": 5},
             {"to": "fit", "status": "done", "amount_mm": 9}, {"to": "assembly", "status": "open", "amount_mm": 7}]
    assert [n["amount_mm"] for n in protocol.open_needs(needs, "fit")] == [5, 1]


def test_accept_when_nothing_worse():
    old = {"assembly": R(3), "fit": R(0)}
    new = {"assembly": R(2), "fit": R(0)}
    assert protocol.decide(old, new, ["assembly"], [])[0] == "accepted"


def test_worse_check_goes_to_discussion():
    old = {"assembly": R(3), "fit": R(0)}
    new = {"assembly": R(1), "fit": R(1)}             # net better, but fit got worse
    objectors = protocol.auto_objectors(old, new, "collective")
    assert objectors[0][0] == "fit" and "failures 0->1" in objectors[0][2]
    assert protocol.decide(old, new, ["assembly", "design"], ["fit"])[0] == "accepted"
    assert protocol.decide(old, new, ["assembly"], ["fit", "manufacturing"])[0] == "rejected"


def test_no_net_gain_is_rejected():
    old = {"assembly": R(2), "fit": R(0)}
    new = {"assembly": R(1), "fit": R(1)}
    assert protocol.decide(old, new, ["a", "b", "c"], [])[0] == "rejected"


def test_same_count_bigger_amount_is_worse():
    assert protocol.worsened({"a": R(1, 0.5)}, {"a": R(1, 2.0)}) == ["a"]


def test_visibility():
    res = {"assembly": R(2), "fit": R(1)}
    seen = protocol.visible_checks("fit", res, "collective")
    assert "failures" in seen["fit"] and "failures" not in seen["assembly"]
    assert seen["assembly"]["failure_count"] == 2


def test_budget():
    b = protocol.Budget(1, 1.0)
    b.spend()
    assert b.stop_reason({"a": R(1)}, 0.0, 0) == "evaluation budget exhausted"
    assert protocol.Budget(3, 1.0).stop_reason({"a": R(0)}, 0.0, 0) is None    # passing does not stop a run
    with pytest.raises(RuntimeError):
        b.spend()
    assert "cost cap" in protocol.Budget(5, 1.0).stop_reason({"a": R(1)}, 1.5, 0)


def test_mock_run_end_to_end(tmp_path):
    import run as runner
    from cad.common import ROOT
    s = runner.run("collective", str(ROOT / "benchmark/briefs/d_rpi_pico.yaml"), budget=4, seed=1, mock=True,
                   out_root=tmp_path, quiet=True)
    d = tmp_path / s["run"]
    assert (d / "base.stl").exists() and (d / "log.md").exists() and (d / "PRINT_SETTINGS.json").exists()
    assert s["evaluations_used"] <= 4 and s["mocked_llm"]
    log = (d / "log.md").read_text(encoding="utf-8")
    assert "MOCK" in log and "Alpha Fast" in log
