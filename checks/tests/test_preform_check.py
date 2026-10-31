"""PreForm check: the mock must be obvious, and must still catch broken meshes and oversize parts."""
import trimesh

from checks.preform_check import check, check_parts


def test_mock_is_labelled(cfg, tmp_path):
    p = tmp_path / "cube.stl"
    trimesh.creation.box((20, 20, 20)).export(p)
    r = check(p, cfg, force_mock=True)
    assert r["mocked"] is True and r["passed"]
    assert all(w["description"].startswith("MOCK") for w in r["warnings"])
    assert 8.0 < r["volume_ml"] < 10.0          # 8 ml x 1.15 support factor


def test_mock_when_server_down(cfg, tmp_path):
    p = tmp_path / "cube.stl"
    trimesh.creation.box((10, 10, 10)).export(p)
    down = {**cfg, "preform": {**cfg["preform"], "base_url": "http://127.0.0.1:9"}}   # nothing listens there
    r = check(p, down)
    assert r["mocked"] and "not reachable" in r["mock_reason"]
    assert r["print_settings"]["material"] == cfg["print"]["material"]


def test_mock_flags_open_mesh(cfg, tmp_path):
    m = trimesh.creation.box((20, 20, 20))
    m.update_faces(list(range(len(m.faces) - 2)))  # remove two faces -> hole
    p = tmp_path / "open.stl"
    m.export(p)
    r = check(p, cfg, force_mock=True)
    assert not r["passed"] and r["failures"][0]["code"] == "not_manifold"


def test_mock_flags_too_big(cfg, tmp_path):
    p = tmp_path / "big.stl"
    trimesh.creation.box((300, 20, 20)).export(p)
    r = check_parts({"big": p}, cfg, force_mock=True)
    assert not r["passed"] and r["mocked"] and "out_of_bounds" in r["failures"][0]["code"]
