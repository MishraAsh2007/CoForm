import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from coform.fusion import FusionLink
from coform.store import Store


@pytest.fixture
def link(tmp_path):
    store = Store(tmp_path)
    link = FusionLink(SimpleNamespace(store=store), None)
    yield link
    store.close()


def test_old_addin_is_rejected_before_view_job_is_queued(link):
    link.next_job({"addin_version": "0.1.0"})
    assert link.status()["connected"] and not link.status()["supports_view_step"]
    with pytest.raises(ValueError, match="Stop, then Run"):
        link.request_view("A generated hook", "/api/designer/projects/p/tests/t/step")
    assert not link.store.list("fusion", "fusion_job")


def test_view_job_reports_failures_deduplicates_and_survives_restart(link):
    link.next_job({"addin_version": "0.3.0", "capabilities": "build,view_step"})
    job = link.request_view("A generated hook", "/api/designer/projects/p/tests/t/step")
    assert link.request_view("Same hook", job["step_url"])["id"] == job["id"]
    assert link.job_status(job["id"])["status"] == "queued"
    assert link.next_job()["id"] == job["id"]
    assert link.job_status(job["id"])["status"] == "running"
    link.submit_result(job["id"], {"error": "STEP import failed"})
    assert link.job_status(job["id"])["result"]["error"] == "Fusion reported: STEP import failed"
    reopened = FusionLink(SimpleNamespace(store=link.store), None)
    assert reopened.next_job({"addin_version": "0.3.0"}) is None
    assert reopened.job_status(job["id"])["status"] == "failed"


def test_empty_import_confirmation_is_not_success(link):
    link.next_job({"addin_version": "0.3.0"})
    job = link.request_view("Hook", "/api/designer/projects/p/tests/t/step")
    assert link.submit_result(job["id"], {})["status"] == "failed"


def test_view_status_api_reports_actual_import_completion(tmp_path):
    from fastapi.testclient import TestClient
    from coform.app import create_app
    app = create_app(tmp_path)
    try:
        with TestClient(app) as client:
            client.get('/api/fusion/jobs/next?addin_version=0.3.0&capabilities=build,view_step')
            job = app.state.fusion.request_view('Generated hook', '/api/designer/projects/p/tests/t/step')
            assert client.get('/api/fusion/jobs/' + job['id']).json()['status'] == 'queued'
            assert client.get('/api/fusion/jobs/next?addin_version=0.3.0').json()['job']['kind'] == 'view_step'
            client.post('/api/fusion/jobs/' + job['id'] + '/result', json={'document': 'Generated hook'})
            state = client.get('/api/fusion/jobs/' + job['id']).json()
            assert state['status'] == 'done' and state['result']['document'] == 'Generated hook'
    finally:
        app.state.service.store.close()


@pytest.fixture
def addin(monkeypatch):
    # Stub Autodesk's host module. No Fusion process is opened or controlled.
    adsk, core, fusion = ModuleType('adsk'), ModuleType('adsk.core'), ModuleType('adsk.fusion')
    for name in ('CustomEventHandler', 'CommandCreatedEventHandler', 'CommandEventHandler'):
        setattr(core, name, type(name, (), {}))
    adsk.core, adsk.fusion = core, fusion
    for name, module in [('adsk', adsk), ('adsk.core', core), ('adsk.fusion', fusion)]:
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location('test_coform_addin', Path('fusion/CoForm/CoForm.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app = SimpleNamespace(activeDocument=None)
    return module


def test_view_dispatch_never_builds_a_predefined_part(addin, monkeypatch):
    sent, opened = [], []
    monkeypatch.setattr(addin, '_open_step', lambda job: opened.append(job['step_url']) or {'document': 'Generated hook'})
    monkeypatch.setattr(addin, '_design', lambda: pytest.fail('STEP view must never enter template-building code'))
    monkeypatch.setattr(addin, '_request', lambda method, path, payload, **kw: sent.append(payload))
    addin.JobHandler().notify(SimpleNamespace(additionalInfo=json.dumps({'id': 'j', 'kind': 'view_step', 'step_url': '/part.step'})))
    assert opened == ['/part.step'] and sent == [{'document': 'Generated hook'}]


@pytest.mark.parametrize('success', [False, True])
def test_step_import_requires_an_actual_document(addin, monkeypatch, tmp_path, success):
    monkeypatch.setattr(addin.tempfile, 'mkdtemp', lambda **kw: str(tmp_path))
    monkeypatch.setattr(addin.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(b'ISO-10303-21;'))
    addin.app.importManager = SimpleNamespace(createSTEPImportOptions=lambda p: p,
        importToNewDocument=lambda o: SimpleNamespace(name='Generated hook') if success else None)
    job = {'name': 'Generated hook', 'step_url': '/part.step'}
    if success:
        assert addin._open_step(job) == {'document': 'Generated hook'}
        assert (tmp_path / 'Generated hook.step').read_bytes() == b'ISO-10303-21;'
    else:
        with pytest.raises(RuntimeError, match='did not create a document'):
            addin._open_step(job)
