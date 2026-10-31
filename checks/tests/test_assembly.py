"""Known-good and known-bad designs for every assembly sub-check."""
from checks.assembly import check
from checks.tests.conftest import codes


def test_good_design_passes(spec, brief, cfg):
    r = check(spec, brief, cfg)
    assert r["passed"], r["failures"]
    assert r["metrics"]["plugs_clear"] == "8/8"


def test_short_standoffs_collide_with_underside_parts(spec, brief, cfg):
    spec["standoffs"]["height"] = 1.0          # floor top at z -1.0, microSD socket reaches -1.42
    r = check(spec, brief, cfg)
    assert "collision" in codes(r)
    assert any("microsd" in f["msg"] for f in r["failures"])


def test_wall_against_board_edge(spec, brief, cfg):
    spec["box"]["gap"]["-x"] = 0.1
    r = check(spec, brief, cfg)
    assert any("-x wall gap" in f["msg"] for f in r["failures"])


def test_closed_port_blocks_drop_in(spec, brief, cfg):
    for p in spec["ports"]:
        if p["name"] == "usb_a_lower":
            p["open_top"] = False
    r = check(spec, brief, cfg)
    assert "drop_in" in codes(r)


def test_small_opening_blocks_plug(spec, brief, cfg):
    for p in spec["ports"]:
        if p["name"] == "hdmi0":
            p["extra"] = -2.0
    r = check(spec, brief, cfg)
    assert any("hdmi0 plug zone" in f["msg"] and f["amount_mm"] > 0 for f in r["failures"])


def test_disabled_required_port(spec, brief, cfg):
    spec["ports"] = [p for p in spec["ports"] if p["name"] != "usb_c_power"]
    r = check(spec, brief, cfg)
    assert any("usb_c_power has no opening" in f["msg"] for f in r["failures"])


def test_low_lid_fails(spec, brief, cfg):
    spec["box"]["inner_top"] = 17.0            # USB stack top is 17.53
    r = check(spec, brief, cfg)
    assert "lid" in codes(r) and "collision" in codes(r)


def test_lid_through_nut_has_no_stock_screw(spec, brief, cfg):
    spec["fasteners"]["lid"] = "through_nut"
    r = check(spec, brief, cfg)
    assert any(f["code"] == "screwdriver" and "no stock length" in f["msg"] for f in r["failures"])


def test_tab_under_plug_zone(spec, brief, cfg):
    spec["mount_tabs"][0]["offset"] = 25.8     # under the HDMI0 cable plug
    r = check(spec, brief, cfg)
    assert any(f["code"] == "tabs" and "hdmi0" in f["msg"] for f in r["failures"])


def test_too_few_tabs(spec, brief, cfg):
    spec["mount_tabs"] = spec["mount_tabs"][:2]
    assert "tabs" in codes(check(spec, brief, cfg))


def test_box_limit(spec, brief, cfg):
    b = dict(brief, max_box=[80, 60, 20])
    r = check(spec, b, cfg)
    assert sum(f["code"] == "box" for f in r["failures"]) == 3


def test_opening_side_limit(spec, brief, cfg):
    b = dict(brief, allowed_opening_sides=["+x"])
    r = check(spec, b, cfg)
    assert any(f["code"] == "sides" and "hdmi0" in f["msg"] for f in r["failures"])


def test_no_vents_fails_airflow(spec, brief, cfg):
    spec["vents"] = []
    assert "airflow" in codes(check(spec, brief, cfg))


def test_missing_cable_slot(spec, brief, cfg):
    for s in spec["cable_slots"]:
        s["enabled"] = False
    r = check(spec, brief, cfg)
    assert any("gpio_ribbon has no slot" in f["msg"] for f in r["failures"])
