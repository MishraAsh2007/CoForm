from contextlib import contextmanager
from copy import deepcopy
import json
import struct

import httpx
import pytest

from scienceclaw.cad import (CADLink, OnshapeClient, OnshapeError, VARIABLES, check_mesh, mesh_metrics,
                             new_variable_feature, parse_part_studio_url, print_pipeline, with_expression)
from scienceclaw.domain import Geometry, Protocol
from scienceclaw.mechanics import generate_stl
from scienceclaw.service import ScienceClaw

pytest.importorskip("formlabs_sdk")
from scienceclaw.formlabs import Manufacturing, PrintProfile  # noqa: E402
from tests.test_formlabs import FakePreForm  # noqa: E402


DOC = {"did": "a" * 24, "wid": "b" * 24, "eid": "c" * 24}
PROFILE = PrintProfile(machine_type="FORM-4-0", material_code="FLGPGR05", layer_thickness_mm=.1, minimum_wall_mm=.5)


def binary_stl(geometry):
    import re
    text = generate_stl(geometry)
    values = [float(v) for v in re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text) for v in v]
    count = len(values) // 9
    raw = bytearray(b"onshape-export".ljust(80, b" ") + struct.pack("<I", count))
    for i in range(count):
        raw += struct.pack("<12fH", 0, 0, 0, *values[9 * i:9 * i + 9], 0)
    return bytes(raw)


class FakeOnshape:
    """Regenerates the part from the sc_* variables, like a configured Part Studio."""

    def __init__(self, base=None, variables=True):
        self.base = base or Geometry()
        self.features = [{**new_variable_feature(name, f"{getattr(self.base, key) * 1000:g} mm"), "featureId": f"F{name}"}
                         for name, key in VARIABLES.items()] if variables else []
        self.features.append({"btType": "BTMFeature-134", "featureType": "scienceClawSupportArm", "featureId": "Fgeom",
                              "name": "ScienceClaw support arm", "parameters": []})
        self.versions, self.requests, self.extra_volume = {}, [], 0.0
        self.redirect_host = "cad.onshape.com"

    def geometry(self, features):
        values = {}
        for f in features:
            params = {p["parameterId"]: p for p in f["parameters"]}
            if f["featureType"] == "assignVariable":
                values[VARIABLES[params["name"]["value"]]] = float(params["lengthValue"]["expression"].split()[0]) / 1000
        return Geometry(**{**self.base.model_dump(), **values})

    def handler(self, request):
        self.requests.append(request)
        assert request.headers["authorization"].startswith("Basic ")
        path = request.url.path
        if path.endswith("/features") and request.method == "GET":
            return httpx.Response(200, json={"features": deepcopy(self.features), "serializationVersion": "1.2.4",
                                             "sourceMicroversion": "mv1", "featureStates": {f["featureId"]: {"featureStatus": "OK"} for f in self.features}})
        if "/features/featureid/" in path:
            feature = json.loads(request.content)["feature"]
            index = next(i for i, f in enumerate(self.features) if f["featureId"] == feature["featureId"])
            self.features[index] = feature
            return httpx.Response(200, json={"feature": feature, "featureState": {"featureStatus": "OK"}, "sourceMicroversion": "mv2"})
        if path.endswith("/features") and request.method == "POST":
            feature = {**json.loads(request.content)["feature"], "featureId": f"Fnew{len(self.features)}"}
            self.features.insert(0, feature)
            return httpx.Response(200, json={"feature": feature, "featureState": {"featureStatus": "OK"}})
        if path.endswith("/versions"):
            vid = f"{len(self.versions) + 1:024x}"
            self.versions[vid] = self.geometry(self.features)
            return httpx.Response(200, json={"id": vid, "name": json.loads(request.content)["name"], "microversion": "mv3"})
        if path.endswith("/stl"):
            vid = path.split("/v/")[1].split("/")[0]
            assert request.url.params["units"] == "millimeter"
            return httpx.Response(307, headers={"location": f"https://{self.redirect_host}/download/{vid}.stl"})
        if path.startswith("/download/"):
            return httpx.Response(200, content=binary_stl(self.versions[path.split("/")[-1][:-4]]))
        if path.endswith("/massproperties"):
            from scienceclaw.mechanics import section_properties
            g = self.versions[path.split("/v/")[1].split("/")[0]]
            v = section_properties(g)["area_m2"] * g.length_m + self.extra_volume
            return httpx.Response(200, json={"bodies": {"-all-": {"volume": [v, v * .999, v * 1.001]}}})
        return httpx.Response(404, json={"message": "unknown"})


@pytest.fixture
def lab(tmp_path):
    service = ScienceClaw(tmp_path)
    fake_pf = FakePreForm()

    @contextmanager
    def connect():
        yield fake_pf
    manufacturing = Manufacturing(service.store, connect)
    onshape = FakeOnshape()
    client = OnshapeClient("access", "secret", client=httpx.Client(transport=httpx.MockTransport(onshape.handler)))
    cad = CADLink(service.store, manufacturing, lambda: (client, DOC))
    yield service, manufacturing, cad, onshape, fake_pf
    service.store.close()


@pytest.mark.parametrize("family,height", [("u_channel", .010), ("solid_bar", .005), ("rectangular_tube", .010)])
def test_mesh_metrics_match_section_ascii_and_binary(family, height):
    geometry = Geometry(section_family=family, wall_height_m=height)
    for raw in (generate_stl(geometry).encode(), binary_stl(geometry)):
        metrics = mesh_metrics(raw)
        assert metrics["closed"] and metrics["orientation"] == "outward"
        assert check_mesh(geometry, metrics)["status"] == "pass"


def test_mesh_check_rejects_cad_that_differs_from_analysis():
    metrics = mesh_metrics(generate_stl(Geometry(base_width_m=.030)).encode())
    report = check_mesh(Geometry(), metrics)
    assert report["status"] == "fail"
    assert not next(c for c in report["checks"] if c["name"] == "extents_match_section")["passed"]
    open_mesh = binary_stl(Geometry())[:-50]
    open_mesh = open_mesh[:80] + struct.pack("<I", (len(open_mesh) - 84) // 50) + open_mesh[84:]
    assert not mesh_metrics(open_mesh)["closed"]


def test_url_and_variable_editing():
    assert parse_part_studio_url(f"https://cad.onshape.com/documents/{'a'*24}/w/{'b'*24}/e/{'c'*24}") == DOC
    with pytest.raises(OnshapeError):
        parse_part_studio_url("https://cad.onshape.com/documents/abc")
    feature = new_variable_feature("sc_length", "160 mm")
    updated = with_expression(feature, "170 mm")
    assert feature["parameters"][2]["expression"] == "160 mm" and updated["parameters"][2]["expression"] == "170 mm"
    number = deepcopy(feature)
    number["parameters"][0]["value"] = "NUMBER"
    with pytest.raises(OnshapeError, match="Length"):
        with_expression(number, "1 mm")
    with pytest.raises(OnshapeError):
        OnshapeClient("a", "b", base_url="https://evil.example")


def test_sync_writes_variables_versions_and_checks_export(lab):
    service, _, cad, onshape, _ = lab
    run = service.create_run(mode="physical", method="collective")
    candidate = service.add_candidate(run["run"]["id"], Geometry(base_width_m=.020, wall_height_m=.008))
    revision = cad.sync_candidate(run["run"]["id"], candidate["id"])
    assert revision["check"]["status"] == "pass" and revision["source"] == "onshape"
    assert revision["variables"]["sc_base_width"] == "20 mm" and revision["variables"]["sc_wall_height"] == "8 mm"
    assert revision["onshape"]["version_id"] in onshape.versions
    assert any(c["name"] == "onshape_mass_properties_volume" and c["passed"] for c in revision["check"]["checks"])
    asset = service.store.get(revision["asset_id"], "print_asset")
    assert asset["source"] == "onshape_export" and asset["candidate_id"] == candidate["id"]
    # Unchanged variables are not rewritten, and a passing revision is reused.
    writes = sum("/featureid/" in r.url.path for r in onshape.requests)
    assert writes == 2
    assert cad.sync_candidate(run["run"]["id"], candidate["id"])["id"] == revision["id"]
    assert len(onshape.versions) == 1
    assert service.store.verify()


def test_sync_detects_cad_that_differs_and_refuses_foreign_redirect(lab):
    service, _, cad, onshape, _ = lab
    run = service.create_run(mode="physical")
    reference = run["candidates"][0]["id"]
    # A local fixture check must never stand in for the Onshape export.
    assert cad.fixture_revision(run["run"]["id"], reference)["source"] == "local_fixture"
    onshape.extra_volume = 1e-6  # e.g. a teammate added a fillet/boss in Onshape
    assert cad.sync_candidate(run["run"]["id"], reference)["check"]["status"] == "fail"
    onshape.redirect_host = "attacker.example"
    candidate = service.add_candidate(run["run"]["id"], Geometry(base_width_m=.021))
    with pytest.raises(OnshapeError, match="credentials were not forwarded"):
        cad.sync_candidate(run["run"]["id"], candidate["id"])


def test_missing_variables_and_bootstrap(tmp_path):
    service = ScienceClaw(tmp_path)
    onshape = FakeOnshape(variables=False)
    client = OnshapeClient("access", "secret", client=httpx.Client(transport=httpx.MockTransport(onshape.handler)))
    cad = CADLink(service.store, Manufacturing(service.store), lambda: (client, DOC))
    run = service.create_run(mode="physical")
    assert cad.status()["missing_variables"] == list(VARIABLES)
    with pytest.raises(OnshapeError, match="lacks variables"):
        cad.sync_candidate(run["run"]["id"], run["candidates"][0]["id"])
    result = cad.bootstrap(Geometry())
    assert result["created"] == list(VARIABLES) and result["ready"]
    assert result["variables"]["sc_thickness"] == "1.2 mm"
    service.store.close()


def test_pipeline_reaches_queue_only_after_specialist_approval(lab):
    service, manufacturing, cad, _, fake_pf = lab
    run = service.create_run(mode="physical")
    run_id, reference = run["run"]["id"], run["candidates"][0]["id"]
    # A candidate project prepared without a review cannot be queued.
    unreviewed = manufacturing.prepare(manufacturing.candidate_asset(run_id, reference)["id"], PROFILE)
    with pytest.raises(ValueError, match="approved specialist print review"):
        manufacturing.queue(unreviewed["id"], "test-printer", "Looked fine")
    result = print_pipeline(service, cad, manufacturing, run_id, reference, PROFILE)
    assert result["status"] == "ready_to_queue", result
    review = result["print_review"]
    assert review["supporters"] == ["fabrication", "measurement", "mechanics"] and not review["gate_errors"]
    receipt = manufacturing.queue(result["print_project"]["id"], "test-printer", "Checked supports and orientation")
    assert receipt["status"] == "queued"
    assert [kw["print_now"] for n, kw in fake_pf.calls if n == "print_scene"] == [False]
    state = service.state(run_id)
    assert state["cad_revisions"] and state["print_reviews"] and state["log_verified"]


def test_review_rejects_thin_walls_and_predicted_failure(lab):
    service, manufacturing, cad, _, _ = lab
    run = service.create_run(mode="physical", protocol=Protocol(max_deflection_m=.0005))
    run_id = run["run"]["id"]
    thick_wall = PrintProfile(**{**PROFILE.model_dump(), "minimum_wall_mm": 2.0})
    result = print_pipeline(service, cad, manufacturing, run_id, run["candidates"][0]["id"], thick_wall, cad_source="local_fixture")
    assert result["status"] == "review_rejected"
    assert "Section thickness is below the selected minimum wall" in result["print_review"]["gate_errors"]
    # A flimsy non-reference design draws a mechanics objection, but two others can still approve it.
    flimsy = service.add_candidate(run_id, Geometry(base_width_m=.015, wall_height_m=.005))
    result = print_pipeline(service, cad, manufacturing, run_id, flimsy["id"], PROFILE, cad_source="local_fixture")
    votes = {v["agent"]: v for v in result["print_review"]["votes"]}
    assert votes["mechanics"]["support"] is False and votes["mechanics"]["concerns"]
    assert result["status"] == "ready_to_queue"


def test_live_print_review_routes_each_specialist_and_meters_tokens(lab, monkeypatch):
    from scienceclaw.providers import ProviderResponse
    service, manufacturing, cad, _, _ = lab

    class Fake:
        def __init__(self, name, support):
            self.name, self.model, self.support, self.payloads = name, f"{name}-model", support, []

        def complete_json(self, system, payload, max_output_tokens):
            self.payloads.append((system, payload))
            ids = payload["context"]["evidence_ids"][:2]
            return ProviderResponse({"support": self.support, "reason": f"{self.name} checked", "evidence_ids": ids}, 500, 40)

    fakes = {"mechanics": Fake("openai", True), "fabrication": Fake("anthropic", False), "measurement": Fake("gemini", True)}
    monkeypatch.setattr(ScienceClaw, "_live_providers", staticmethod(lambda team=None: fakes))
    run = service.create_run(mode="physical", protocol=Protocol(token_budget=20000))
    result = print_pipeline(service, cad, manufacturing, run["run"]["id"], run["candidates"][0]["id"], PROFILE,
                            cad_source="local_fixture", live=True)
    review = result["print_review"]
    assert review["status"] == "approved" and review["supporters"] == ["measurement", "mechanics"]
    assert review["usage"]["calls"] == 3 and review["usage"]["total_tokens"] == 1620
    assert "fabrication specialist" in fakes["fabrication"].payloads[0][0]
    assert service.state(run["run"]["id"])["budget"]["tokens_used"] == 1620
