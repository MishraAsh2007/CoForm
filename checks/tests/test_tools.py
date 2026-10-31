"""The two Manufacturing-agent tools (not used in the benchmark): thickness heat map, orientation compare."""
import pytest
import trimesh

from checks import orientations, thickness_map


def test_heatmap_finds_thin_plate(cfg, tmp_path):
    p = tmp_path / "thin.stl"
    trimesh.creation.box((20, 20, 0.3)).export(p)          # 0.3 mm plate: below the 0.6 rule
    r = thickness_map.heatmap(p, tmp_path / "thin.png", cfg)
    assert r["p1_mm"] < 0.4 and r["thin_fraction"] > 0.5 and (tmp_path / "thin.png").exists()


def test_heatmap_thick_block_is_clean(cfg, tmp_path):
    p = tmp_path / "block.stl"
    trimesh.creation.box((20, 20, 20)).export(p)
    r = thickness_map.heatmap(p, tmp_path / "block.png", cfg)
    assert r["min_mm"] > 5 and r["thin_fraction"] == 0


def test_orientations_mock_when_server_down(cfg, tmp_path):
    p = tmp_path / "cube.stl"
    trimesh.creation.box((10, 10, 10)).export(p)
    down = {**cfg, "preform": {**cfg["preform"], "base_url": "http://127.0.0.1:9"}}
    rows = orientations.compare(p, down)
    assert len(rows) == 4 and all(r["mocked"] and r["warnings"][0].startswith("MOCK") for r in rows)
    flat = next(r for r in rows if r["name"] == "flat")
    tilt = next(r for r in rows if r["name"] == "tilt_30_x")
    assert flat["support_ml"] > tilt["support_ml"]          # a flat bottom face needs the most support


def test_orientations_real_preform(cfg, tmp_path):
    from checks.preform_check import preform_settings
    from checks.preform_client import PreFormClient
    if not PreFormClient(preform_settings(cfg)).is_up():
        pytest.skip("PreFormServer not running")
    p = tmp_path / "cube.stl"
    trimesh.creation.box((10, 10, 10)).export(p)
    rows = orientations.compare(p, cfg, candidates=[("flat", (0, 0)), ("tilt_45_y", (0, 45))])
    assert all(not r["mocked"] for r in rows)
    assert all(r["total_ml"] >= r["part_ml"] for r in rows)
    assert orientations.best(rows)["name"] in ("flat", "tilt_45_y")


def test_print_job_refuses_without_server(cfg, tmp_path):
    from cad.print_job import make
    p = tmp_path / "cube.stl"
    trimesh.creation.box((10, 10, 10)).export(p)
    down = {**cfg, "preform": {**cfg["preform"], "base_url": "http://127.0.0.1:9"}}
    with pytest.raises(RuntimeError, match="not running"):
        make(tmp_path / "x.form", [p], cfg=down)          # a real .form is never faked
