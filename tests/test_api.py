import io
import json
import zipfile

from fastapi.testclient import TestClient

from coform.app import create_app


def test_api_create_generate_run_export_and_secret_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-appear")
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        config = client.get("/api/config")
        assert config.json()["providers"]["openai"]["key_configured"]
        assert "must-not-appear" not in config.text
        initial = client.post("/api/runs", json={}).json()
        run_id = initial["run"]["id"]
        path = initial["candidates"][0]["artifacts"]["stl"]["filename"]
        assert client.get("/api/artifacts/" + path).text.startswith("solid")
        result = client.post(f"/api/runs/{run_id}/demo", json={})
        assert result.status_code == 200
        assert result.json()["acceptance"]["status"] == "simulated_pass"
        exported = client.get(f"/api/runs/{run_id}/export")
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            assert json.loads(archive.read("run.json"))["run"]["id"] == run_id
            assert any(name.endswith(".stl") for name in archive.namelist())
        assert client.post("/api/runs", json={}, headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert client.post("/api/runs", json={"geometry": {"base_width_m": -1}}).status_code == 422
    app.state.service.store.close()
