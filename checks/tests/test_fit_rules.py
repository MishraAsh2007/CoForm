"""fit_rules: turning hand results into rules, and checking a design against them."""
import pytest

from checks.fit_rules import check, rules

FULL = {
    "peg_holes": {"5.0": "no_fit", "5.1": "press", "5.2": "snug", "5.3": "free", "5.4": "free", "5.5": "free"},
    "grooves": {"2.0": "no_fit", "2.1": "press", "2.2": "snug", "2.3": "free", "2.4": "free", "2.5": "free"},
    "m25_holes": {"2.5": "no_fit", "2.6": "press", "2.7": "snug", "2.8": "free", "2.9": "free", "3.0": "free"},
    "nut_pockets": {"5.0": "no_fit", "5.1": "press", "5.2": "snug", "5.3": "free", "5.4": "free"},
    "screw_posts": {"5": "cracked", "6": "snug", "7": "snug"},
    "thin_walls": {"0.4": "failed_to_print", "0.6": "cracked", "0.8": "free", "1.0": "free", "1.2": "free"},
}


def test_generic_rules(cfg):
    r = rules(cfg, "generic")
    assert r["values"]["part_gap"] == 0.5 and r["fallbacks"] == []


def test_empty_measured_falls_back_and_says_so(cfg):
    r = rules(cfg, "measured", results={})
    assert len(r["fallbacks"]) == 8
    assert r["values"] == rules(cfg, "generic")["values"]
    assert "every value is GENERIC" in r["notes"][0]


def test_full_measured(cfg):
    r = rules(cfg, "measured", results=FULL)
    v = r["values"]
    assert v["part_gap"] == 0.15 and v["lid_groove_extra"] == 0.3     # 2.3 groove free
    assert v["m25_clearance_hole"] == 2.8
    assert v["nut_pocket_af"] == 5.2
    assert v["fastener_style"] == "pilot" and v["min_post_od_for_pilot"] == 6.0
    assert v["min_wall"] == 0.8
    assert r["fallbacks"] == []


def test_all_posts_cracked_means_through_nut(cfg):
    r = rules(cfg, "measured", results={"screw_posts": {"5": "cracked", "6": "cracked", "7": "cracked"}})
    assert r["values"]["fastener_style"] == "through_nut"
    assert any("use through-hole + nut" in n for n in r["notes"])


def test_bad_word_rejected(cfg):
    with pytest.raises(ValueError):
        rules(cfg, "measured", results={"grooves": {"2.0": "tight"}})


def test_check_good_generic(spec, cfg):
    assert check(spec, rules(cfg, "generic"))["passed"]


def test_check_tight_lid_against_rule(spec, cfg):
    spec["clearances"]["lid_fit"] = 0.1
    r = check(spec, rules(cfg, "generic"))
    assert any("lid fit" in f["msg"] and abs(f["amount_mm"] - 0.4) < 1e-6 for f in r["failures"])


def test_check_cracking_posts(spec, cfg):
    r = check(spec, rules(cfg, "measured", results=FULL))   # 5.5 mm standoffs, 6 mm needed
    assert any("standoff OD" in f["msg"] for f in r["failures"])


def test_check_spinning_nut(spec, cfg):
    spec["fasteners"]["board"] = "through_nut"             # generic 6.0 AF pocket > 5.77 corners
    r = check(spec, rules(cfg, "generic"))
    assert any(f["code"] == "nut" for f in r["failures"])
