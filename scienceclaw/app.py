"""Local review API. Start with: python -m scienceclaw.cli serve."""
import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from . import agents
from .domain import Geometry, Protocol, Record, SimulationScenario, TeamMember
from .measurement import analyze_trace
from .service import ScienceClaw
from .formlabs import Manufacturing, PrintProfile, MAX_STL_BYTES
from .cad import CADLink, FreeCADLink, build_cad, print_pipeline
from .fusion import FusionLink


class RunRequest(Record):
    name: str = Field(default="Resin support study", min_length=1, max_length=120)
    mode: Literal["simulated", "physical"] = "simulated"
    method: Literal["collective", "adaptive", "single_agent", "independent"] = "collective"
    seed: int = Field(default=7, ge=0, le=1000000)
    geometry: Geometry = Geometry()
    protocol: Protocol = Protocol()
    scenario: SimulationScenario | None = None
    team: dict[Literal["mechanics", "fabrication", "measurement", "single"], TeamMember] | None = None


class StepRequest(Record):
    live: bool = False


class CandidateRequest(Record):
    geometry: Geometry
    parent_id: str | None = None


class FabricationRequest(Record):
    candidate_id: str
    specimen_id: str = Field(min_length=1, max_length=200)
    actual_geometry: Geometry | None = None
    notes: str = Field(min_length=1, max_length=10000)
    duration_s: float = Field(default=0, ge=0)


class MeasurementRequest(Record):
    actual_force_n: float = Field(gt=0)
    actual_load_position_m: float = Field(gt=0)
    displacement_m: float
    uncertainty_m: float = Field(gt=0)
    residual_m: float
    fixture_motion_m: float
    dwell_s: float = Field(gt=0)
    recovery_s: float = Field(gt=0)
    quality_flags: tuple[str, ...] = ()
    raw_csv: str | None = Field(default=None, max_length=2_000_000)
    human_notes: str = Field(default="", max_length=10000)
    mass_kg: float | None = Field(default=None, gt=0)


class TraceRequest(Record):
    csv_text: str = Field(max_length=2_000_000)
    pixels_per_m: float = Field(gt=0)
    calibration_uncertainty_fraction: float = Field(default=0.01, ge=0)


class NominationRequest(Record):
    candidate_id: str | None = None


class PreparePrintRequest(Record):
    asset_id: str
    profile: PrintProfile


class QueuePrintRequest(Record):
    printer: str = Field(min_length=1, max_length=200)
    review_notes: str = Field(min_length=3, max_length=4000)


class CandidatePrintRequest(Record):
    candidate_id: str


class DesignRequest(Record):
    prompt: str = Field(min_length=10, max_length=4000)
    max_displacement_mm: float | None = Field(default=None, gt=0, le=50)
    max_mass_g: float | None = Field(default=None, gt=0, le=2000)
    safety_factor: float | None = Field(default=None, ge=1, le=10)
    load_n: float | None = Field(default=None, gt=0, le=2000)
    live: bool = False


class DesignRunRequest(Record):
    live: bool = False
    rounds: int = Field(default=6, ge=1, le=12)


class DesignPrintRequest(Record):
    test_id: str
    profile: PrintProfile
    live: bool = False


class DesignQueueRequest(Record):
    review_id: str
    printer: str = Field(min_length=1, max_length=200)
    review_notes: str = Field(min_length=3, max_length=2000)


class DesignMeasurementRequest(Record):
    test_id: str
    measured_mm: float = Field(gt=0, le=100)
    uncertainty_mm: float = Field(gt=0, le=10)
    notes: str = Field(default="", max_length=2000)


class PrintPipelineRequest(Record):
    candidate_id: str
    profile: PrintProfile
    cad_source: Literal["auto", "onshape", "freecad", "fusion", "local_fixture", "uploaded"] = "auto"
    live: bool = False


class PrintReviewRequest(Record):
    project_id: str
    live: bool = False


class RepairRequest(Record):
    live: bool = False


class OnshapeBootstrapRequest(Record):
    run_id: str | None = None


def _scenario_menu() -> list[dict]:
    from . import fea
    from .validation import RIG_SCENARIOS
    menu = []
    for s in RIG_SCENARIOS:
        menu.append({"name": s.name, "scenario": s.model_dump(mode="json"),
                     "label": f"Virtual test rig (3D FEA): {fea.MATERIALS[s.material]['label'].split(' (')[0]}, "
                              f"{fea.FIXTURES[s.fixture]['label'].split(',')[0].split(' (')[0].lower()}"})
    menu.append({"name": "beam_formula", "scenario": SimulationScenario(name="beam_formula").model_dump(mode="json"),
                 "label": "Beam formula with built-in values (instant, least realistic)"})
    return menu


def create_app(data_dir=None):
    load_dotenv(override=False)
    app = FastAPI(title="ScienceClaw", version="0.1.0", description="Printable support design with an evidence-led agent collective")
    service = ScienceClaw(data_dir or os.getenv("SCIENCECLAW_DATA_DIR", "data"))
    app.state.service = service
    manufacturing = Manufacturing(service.store)
    app.state.manufacturing = manufacturing
    cad_link = CADLink(service.store, manufacturing)
    app.state.cad = cad_link
    freecad = FreeCADLink(service.store, manufacturing)
    app.state.freecad = freecad
    fusion = FusionLink(service, manufacturing)
    app.state.fusion = fusion

    def cad_step(run_id: str, candidate_id: str):
        """Newest checked Fusion/FreeCAD STEP for a candidate, for the virtual test rig."""
        revisions = [r for r in service.store.list(run_id, "cad_revision")
                     if r["candidate_id"] == candidate_id and r["check"]["status"] == "pass"]
        for revision in reversed(revisions):
            try:
                if revision["source"] == "fusion" and revision["fusion"].get("step"):
                    return fusion.step_file(run_id, revision["id"])
                if revision["source"] == "freecad":
                    return freecad._file(revision["freecad"]["step"], revision["freecad"]["step_sha256"])
            except ValueError:
                continue  # Missing or modified file: try an older revision, else the generated section.
        return None
    service.cad_step = cad_step
    from .designer import Designer
    designer = Designer(service, manufacturing, fusion)
    app.state.designer = designer
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.middleware("http")
    async def local_mutations(request: Request, call_next):
        # A web page on another origin must not trigger a local paid agent call.
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin mutations are disabled"}, status_code=403)
        length = request.headers.get("content-length")
        path = request.url.path
        limit = (2 * MAX_STL_BYTES + 1_000_000 if path.startswith("/api/fusion/")
                 else MAX_STL_BYTES if path == "/api/formlabs/assets" or path.endswith("/cad-upload") else 3_000_000)
        if length and (not length.isdigit() or int(length) > limit):
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path in ("/docs", "/redoc"):
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/designer")
    def designer_page():
        return FileResponse(static / "designer.html")

    # ---- prompt-to-part designer -------------------------------------------------
    @app.get("/api/designer/projects")
    def designer_projects():
        return designer.projects()

    @app.post("/api/designer/projects")
    def designer_create(body: DesignRequest):
        constraints = body.model_dump(exclude={"prompt", "live"})
        return designer.create(body.prompt, constraints, live=body.live)

    @app.get("/api/designer/projects/{project_id}")
    def designer_state(project_id: str):
        return designer.state(project_id)

    @app.post("/api/designer/projects/{project_id}/run")
    def designer_run(project_id: str, body: DesignRunRequest):
        return designer.run_more(project_id, live=body.live, rounds=body.rounds)

    @app.get("/api/designer/projects/{project_id}/tests/{test_id}/field")
    def designer_field(project_id: str, test_id: str):
        return FileResponse(designer.field(project_id, test_id), media_type="application/json")

    @app.get("/api/designer/projects/{project_id}/tests/{test_id}/{kind}")
    def designer_file(project_id: str, test_id: str, kind: Literal["step", "stl"]):
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", designer.store.list(project_id, "design_spec")[-1]["spec"]["name"])[:60] or "part"
        return FileResponse(designer.artifact(project_id, test_id, kind), filename=f"{name}-{test_id[-6:]}.{kind}")

    @app.post("/api/designer/projects/{project_id}/fusion")
    def designer_fusion(project_id: str, body: dict):
        return designer.view_in_fusion(project_id, str(body.get("test_id", "")))

    @app.post("/api/designer/projects/{project_id}/print")
    def designer_print(project_id: str, body: DesignPrintRequest):
        return designer.prepare_print(project_id, body.test_id, body.profile, live=body.live)

    @app.post("/api/designer/projects/{project_id}/queue")
    def designer_queue(project_id: str, body: DesignQueueRequest):
        return designer.queue(project_id, body.review_id, body.printer, body.review_notes)

    @app.post("/api/designer/projects/{project_id}/measurement")
    def designer_measurement(project_id: str, body: DesignMeasurementRequest):
        return designer.record_measurement(project_id, body.test_id, body.measured_mm, body.uncertainty_mm, body.notes)

    @app.get("/api/config")
    def config():
        roles = {role: os.getenv(f"{role.upper()}_PROVIDER", default) for role, default in
                 (("mechanics", "openai"), ("fabrication", "anthropic"), ("measurement", "gemini"))}
        providers = {}
        for name in ("openai", "anthropic", "gemini", "nvidia"):
            key = os.getenv(f"{name.upper()}_API_KEY") or (os.getenv("GOOGLE_API_KEY") if name == "gemini" else None)
            model = os.getenv(f"{name.upper()}_MODEL", "")
            providers[name] = {"key_configured": bool(key), "model": model, "ready": bool(key and model)}
        onshape = all(os.getenv(k) for k in ("ONSHAPE_ACCESS_KEY", "ONSHAPE_SECRET_KEY", "ONSHAPE_PART_STUDIO_URL"))
        single = os.getenv("SINGLE_AGENT_PROVIDER") or roles["mechanics"]
        return {"roles": roles, "providers": providers, "onshape_configured": onshape, "single_agent_provider": single,
                "live_ready": all(providers.get(p, {}).get("ready", False) for p in roles.values()),
                "scenarios": _scenario_menu(),
                "charters": {role: {**c, "may_propose": list(c["may_propose"])} for role, c in agents.CHARTERS.items()},
                "defaults": {"geometry": Geometry().model_dump(), "protocol": Protocol().model_dump(mode="json")}}

    @app.get("/api/runs")
    def runs():
        return service.store.list(None, "run")

    @app.get("/api/formlabs/status")
    def form_status():
        return manufacturing.status()

    @app.get("/api/formlabs/catalog")
    def form_catalog(printer: str | None = None):
        return manufacturing.catalog(printer)

    @app.get("/api/formlabs/projects")
    def form_projects():
        return {"assets": service.store.list(None, "print_asset"), "projects": service.store.list(None, "print_project"),
                "submissions": service.store.list(None, "print_submission"), "receipts": service.store.list(None, "print_receipt")}

    @app.post("/api/formlabs/assets")
    async def form_upload(request: Request, filename: str):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_STL_BYTES:
                raise HTTPException(413, "STL exceeds 25 MiB")
        return manufacturing.add_asset(filename, bytes(raw))

    @app.post("/api/runs/{run_id}/formlabs/assets")
    def form_candidate(run_id: str, body: CandidatePrintRequest):
        return manufacturing.candidate_asset(run_id, body.candidate_id)

    freecad_cache = {}

    @app.get("/api/cad/status")
    def cad_status():
        # FreeCAD's check starts a process; cache it for the session.
        if "status" not in freecad_cache or not freecad_cache["status"].get("available"):
            freecad_cache["status"] = freecad.status()
        fusion_state = fusion.status()
        active = "fusion" if fusion_state["connected"] else "freecad" if freecad_cache["status"].get("available") else None
        return {"active": active, "fusion": fusion_state, "freecad": freecad_cache["status"]}

    @app.post("/api/runs/{run_id}/candidates/{candidate_id}/cad")
    def cad_build(run_id: str, candidate_id: str):
        return build_cad(run_id, candidate_id, fusion=fusion, freecad=freecad)

    @app.get("/api/fusion/status")
    def fusion_status():
        return fusion.status()

    @app.get("/api/fusion/jobs/next")
    def fusion_next(addin_version: str | None = None, fusion_version: str | None = None, document: str | None = None):
        # Polled by the Fusion add-in; returns {"job": null} when idle.
        return {"job": fusion.next_job({"addin_version": addin_version, "fusion_version": fusion_version, "document": document})}

    @app.post("/api/fusion/jobs/{job_id}/result")
    async def fusion_result(job_id: str, request: Request):
        return fusion.submit_result(job_id, await request.json())

    @app.post("/api/fusion/designs")
    async def fusion_design(request: Request):
        return fusion.user_submission(await request.json())

    @app.post("/api/runs/{run_id}/candidates/{candidate_id}/fusion")
    def fusion_build(run_id: str, candidate_id: str):
        return fusion.build_and_wait(run_id, candidate_id)

    @app.get("/api/runs/{run_id}/cad/{cad_id}/fusion-step")
    def fusion_step(run_id: str, cad_id: str):
        return FileResponse(fusion.step_file(run_id, cad_id), filename=f"{cad_id}.step")

    @app.get("/api/freecad/status")
    def freecad_status():
        return freecad.status()

    @app.post("/api/runs/{run_id}/candidates/{candidate_id}/freecad")
    def freecad_sync(run_id: str, candidate_id: str):
        return freecad.sync_candidate(run_id, candidate_id)

    @app.post("/api/runs/{run_id}/cad/{cad_id}/open")
    def freecad_open(run_id: str, cad_id: str):
        return freecad.open_in_gui(run_id, cad_id)

    @app.get("/api/runs/{run_id}/cad/{cad_id}/{kind}")
    def freecad_file(run_id: str, cad_id: str, kind: Literal["fcstd", "step"]):
        revision = service.store.get(cad_id, "cad_revision")
        if revision["run_id"] != run_id or revision["source"] != "freecad":
            raise HTTPException(404, "Not a FreeCAD revision of this study")
        record = revision["freecad"]
        return FileResponse(freecad._file(record[kind], record[kind + "_sha256"]), filename=record[kind])

    @app.get("/api/onshape/status")
    def onshape_status():
        return cad_link.status()

    @app.post("/api/onshape/bootstrap")
    def onshape_bootstrap(body: OnshapeBootstrapRequest):
        geometry = Geometry(**service.store.get(body.run_id, "run")["reference_geometry"]) if body.run_id else Geometry()
        return cad_link.bootstrap(geometry)

    @app.post("/api/runs/{run_id}/candidates/{candidate_id}/onshape")
    def onshape_sync(run_id: str, candidate_id: str):
        return cad_link.sync_candidate(run_id, candidate_id)

    @app.post("/api/runs/{run_id}/candidates/{candidate_id}/cad-upload")
    async def cad_upload(run_id: str, candidate_id: str, request: Request, filename: str):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_STL_BYTES:
                raise HTTPException(413, "STL exceeds 25 MiB")
        if not filename.lower().endswith(".stl"):
            raise HTTPException(400, "Export the part as an STL in millimeters")
        return cad_link.upload_revision(run_id, candidate_id, filename, bytes(raw))

    @app.get("/api/runs/{run_id}/candidates/{candidate_id}/diagnosis")
    def diagnosis(run_id: str, candidate_id: str):
        return service.diagnose(run_id, candidate_id)

    @app.get("/api/runs/{run_id}/design-space")
    def design_space(run_id: str):
        return service.design_space(run_id)

    @app.post("/api/runs/{run_id}/candidates/{candidate_id}/fix")
    def fix(run_id: str, candidate_id: str, body: RepairRequest):
        from .autofix import fix_candidate
        return fix_candidate(service, run_id, candidate_id, live=body.live, cad_link=cad_link, fusion=fusion, freecad=freecad)

    @app.post("/api/runs/{run_id}/candidates/{candidate_id}/repair")
    def repair(run_id: str, candidate_id: str, body: RepairRequest):
        return service.repair(run_id, candidate_id, live=body.live)

    @app.post("/api/runs/{run_id}/print-pipeline")
    def pipeline(run_id: str, body: PrintPipelineRequest):
        service.store.get(run_id, "run")
        return print_pipeline(service, cad_link, manufacturing, run_id, body.candidate_id, body.profile,
                              cad_source=body.cad_source, live=body.live, freecad=freecad, fusion=fusion)

    @app.post("/api/runs/{run_id}/print-reviews")
    def print_review(run_id: str, body: PrintReviewRequest):
        return service.print_review(run_id, body.project_id, live=body.live)

    @app.post("/api/formlabs/prepare")
    def form_prepare(body: PreparePrintRequest):
        return manufacturing.prepare(body.asset_id, body.profile)

    @app.post("/api/formlabs/projects/{project_id}/queue")
    def form_queue(project_id: str, body: QueuePrintRequest):
        return manufacturing.queue(project_id, body.printer, body.review_notes)

    @app.get("/api/formlabs/projects/{project_id}/file")
    def form_file(project_id: str):
        project = service.store.get(project_id, "print_project")
        path = manufacturing._file(project["filename"], project["sha256"])
        return FileResponse(path, filename=project["filename"])

    @app.post("/api/runs")
    def create_run(body: RunRequest):
        return service.create_run(**body.model_dump(exclude={"geometry", "protocol", "scenario", "team"}), geometry=body.geometry,
                                  protocol=body.protocol, scenario=body.scenario,
                                  team={k: v.model_dump() for k, v in body.team.items()} if body.team else None)

    @app.get("/api/runs/{run_id}")
    def state(run_id: str):
        return service.state(run_id)

    @app.post("/api/runs/{run_id}/step")
    def step(run_id: str, body: StepRequest):
        from .providers import ProviderError
        try:
            return service.step(run_id, live=body.live)
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/runs/{run_id}/demo")
    def demo(run_id: str):
        return service.demo(run_id)

    @app.post("/api/runs/{run_id}/fit")
    def fit(run_id: str):
        return service.fit(run_id)

    @app.post("/api/runs/{run_id}/reserved-check")
    def reserved(run_id: str):
        return service.reserved_check(run_id)

    @app.post("/api/runs/{run_id}/candidates")
    def candidates(run_id: str, body: CandidateRequest):
        return service.add_candidate(run_id, body.geometry, body.parent_id)

    @app.post("/api/runs/{run_id}/fabrications")
    def fabrication(run_id: str, body: FabricationRequest):
        return service.fabricate(run_id, **body.model_dump(exclude={"actual_geometry"}), actual_geometry=body.actual_geometry)

    @app.post("/api/runs/{run_id}/experiments/{experiment_id}/observations")
    def observation(run_id: str, experiment_id: str, body: MeasurementRequest):
        return service.record(run_id, experiment_id, **body.model_dump())

    @app.post("/api/trace/analyze")
    def trace(body: TraceRequest):
        return analyze_trace(**body.model_dump())

    @app.post("/api/runs/{run_id}/nominate")
    def nominate(run_id: str, body: NominationRequest):
        return service.nominate(run_id, body.candidate_id)

    @app.post("/api/runs/{run_id}/acceptance")
    def acceptance(run_id: str):
        return service.acceptance_cycle(run_id)

    @app.get("/api/artifacts/{filename}")
    def artifact(filename: str):
        if not re.fullmatch(r"[a-f0-9]{64}\.(stl|scad|svg|csv)", filename):
            raise HTTPException(404, "Unknown artifact")
        path = service.store.artifacts / filename
        if not path.is_file():
            raise HTTPException(404, "Unknown artifact")
        return FileResponse(path, filename=filename)

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str):
        snapshot = service.state(run_id)
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("run.json", json.dumps(snapshot, indent=2, allow_nan=False))
            files = {a["filename"] for c in snapshot["candidates"] for a in c["artifacts"].values()}
            files.update(o["raw_artifact"] for o in snapshot["observations"] if o["raw_artifact"])
            for filename in sorted(files):
                archive.write(service.store.artifacts / filename, f"artifacts/{filename}")
            print_files = {r["filename"]: r["sha256"] for r in snapshot["print_assets"] + snapshot["print_projects"]}
            for filename, sha in print_files.items():
                archive.write(manufacturing._file(filename, sha), f"formlabs/{filename}")
            archive.writestr("README.txt", "ScienceClaw run export. SI units in JSON; STL/OpenSCAD use mm.\nSource and policy labels distinguish simulation and rule-based demo from live agents and physical evidence.\nSQLite event hashes detect accidental edits; this is not external attestation.\n")
        return Response(raw.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'})

    return app
