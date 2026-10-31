"""Part families: enclosure behind the interface, and the EXPERIMENTAL free-form family."""
import pytest
import yaml

import families
from cad.common import ROOT
from checks.fit_rules import rules

GOOD = '''
import cadquery as cq
block = cq.Workplane("XY").box(40, 30, 10).faces(">Z").workplane().hole(11.0)
peg = cq.Workplane("XY").circle(5.0).extrude(20).translate((0, 0, -10))
parts = {"block": block, "peg": peg}
'''
TIGHT = GOOD.replace("hole(11.0)", "hole(10.2)")          # 0.1 mm per side: below the 0.5 mm generic rule
CLASH = GOOD.replace("hole(11.0)", "hole(9.0)")           # peg overlaps the block


@pytest.fixture
def brief():
    return yaml.safe_load((ROOT / "benchmark/briefs/e_freeform_peg_block.yaml").read_text(encoding="utf-8"))


def _run(fam, code, brief, cfg, tmp_path):
    spec = fam.validate({**fam.default_spec(brief, rules(cfg, "generic"), cfg), "code": code})
    built = fam.build(spec, brief, cfg, tmp_path)
    return fam.checks(spec, brief, cfg, rules(cfg, "generic"), built)


def test_enclosure_family_matches_old_path(cfg, brief, tmp_path):
    fam = families.get("enclosure")
    a = yaml.safe_load((ROOT / "benchmark/briefs/a_pi5_standard.yaml").read_text(encoding="utf-8"))
    spec = fam.default_spec(a, rules(cfg, "generic"), cfg, start="reference")
    built = fam.build(spec, a, cfg, tmp_path)
    res = fam.checks(spec, a, cfg, rules(cfg, "generic"), built)
    assert set(res) == set(fam.check_owners) and set(built["parts"]) == {"base", "lid"}


def test_freeform_good(cfg, brief, tmp_path):
    res = _run(families.get("freeform"), GOOD, brief, cfg, tmp_path)
    assert res["interference"]["passed"] and res["fit"]["passed"], res
    assert res["fit"]["metrics"]["gaps_mm"]["block|peg"] == pytest.approx(0.5, abs=0.01)
    assert res["fit"]["metrics"]["experimental"] is True


def test_freeform_tight_fit_fails(cfg, brief, tmp_path):
    res = _run(families.get("freeform"), TIGHT, brief, cfg, tmp_path)
    assert not res["fit"]["passed"] and res["interference"]["passed"]


def test_freeform_interference(cfg, brief, tmp_path):
    res = _run(families.get("freeform"), CLASH, brief, cfg, tmp_path)
    assert not res["interference"]["passed"]


def test_freeform_rejects_unsafe_code(cfg, brief, tmp_path):
    with pytest.raises(ValueError, match="not allowed"):
        _run(families.get("freeform"), "import os\nparts = {}", brief, cfg, tmp_path)
