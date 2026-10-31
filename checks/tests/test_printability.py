"""Known-good and known-bad designs for the printability check."""
from cad.common import export_stl
from cad.enclosure import build
from checks.fit_rules import rules
from checks.printability import check
from checks.tests.conftest import codes


def _rules(cfg):
    return rules(cfg, "generic")


def test_good_design_passes(spec, cfg):
    r = check(spec, cfg, _rules(cfg))
    assert r["passed"], r["failures"]


def test_thin_wall(spec, cfg):
    spec["walls"]["wall"] = 0.4
    spec["walls"]["outer_fillet"] = 0.2
    r = check(spec, cfg, _rules(cfg))
    assert any(f["code"] == "wall" and "wall thickness 0.40" in f["msg"] for f in r["failures"])


def test_thin_vent_rib(spec, cfg):
    spec["vents"][0]["pitch"] = 2.3
    r = check(spec, cfg, _rules(cfg))
    assert any(f["code"] == "vent" and abs(f["amount_mm"] - 0.3) < 0.01 for f in r["failures"])


def test_sliver_between_openings(spec, cfg):
    for p in spec["ports"]:
        if p["name"] == "hdmi0":
            p["extra"] = 1.0                    # leaves 0.4 mm to hdmi1's opening
    r = check(spec, cfg, _rules(cfg))
    assert any(f["code"] == "sliver" and "hdmi1" in f["msg"] for f in r["failures"])


def test_tab_hole_too_big(spec, cfg):
    spec["mount_tabs"][0]["hole"] = 9.5
    assert "wall" in codes(check(spec, cfg, _rules(cfg)))


def test_lid_nut_pocket_breaks_out(spec, cfg):
    spec["fasteners"]["lid"] = "through_nut"
    r = check(spec, cfg, _rules(cfg))
    assert "nut_pocket" in codes(r)


def test_measured_min_wall_is_used(spec, cfg):
    r = rules(cfg, "measured", results={"thin_walls": {"0.4": "failed_to_print", "0.6": "cracked",
                                                       "0.8": "cracked", "1.0": "free", "1.2": "free"}})
    spec["lid"]["lip_width"] = 0.8
    out = check(spec, cfg, r)
    assert any("lid lip width 0.80 mm < 1.0" in f["msg"] for f in out["failures"])


def test_mesh_scan_finds_thin_lid(spec, cfg, tmp_path):
    spec["walls"]["lid"] = 0.3
    built = build(spec)
    lid = export_stl(built["lid"], tmp_path / "lid.stl", cfg)
    r = check(spec, cfg, _rules(cfg), built=built, mesh_paths={"lid": lid})
    assert "mesh_thin" in codes(r)


def test_mesh_scan_good(spec, cfg, tmp_path):
    built = build(spec)
    base = export_stl(built["base"], tmp_path / "base.stl", cfg)
    r = check(spec, cfg, _rules(cfg), built=built, mesh_paths={"base": base})
    assert r["passed"]
