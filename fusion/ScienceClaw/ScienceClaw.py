"""ScienceClaw add-in for Autodesk Fusion.

- Polls the local ScienceClaw server (http://127.0.0.1:8000 by default) for build jobs.
  A job sets the design's sc_* user parameters; Fusion recomputes the parametric
  timeline (your own features stay), then STL/STEP exports are posted back.
- "ScienceClaw: set up" creates the sc_* parameters and, if the design is empty, a
  parametric support arm driven by them.
- "Send to ScienceClaw" sends your own edits for checking against the study.

Fusion API calls happen only on Fusion's main thread (via a custom event); the
background thread only does HTTP. Nothing leaves this computer.
"""
import base64
import json
import os
import tempfile
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request

import adsk.core
import adsk.fusion

SERVER = os.environ.get("SCIENCECLAW_URL", "http://127.0.0.1:8000").rstrip("/")
VERSION = "0.2.0"
EVENT_ID = "ScienceClawJob"
PANEL_ID = "SolidScriptsAddinsPanel"
PARAMS = ("sc_length", "sc_base_width", "sc_wall_height", "sc_thickness")
DEFAULTS = {"sc_length": "160 mm", "sc_base_width": "25 mm", "sc_wall_height": "10 mm", "sc_thickness": "1.2 mm"}

app = None
ui = None
handlers = []
stopping = threading.Event()
busy = threading.Lock()


# ---------------------------------------------------------------- HTTP helpers
def _request(method, path, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(SERVER + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _poll():
    while not stopping.wait(2.0):
        if busy.locked():
            continue
        try:
            document = app.activeDocument.name if app.activeDocument else ""
            query = urllib.parse.urlencode({"addin_version": VERSION, "fusion_version": app.version, "document": document})
            job = _request("GET", "/api/fusion/jobs/next?" + query).get("job")
        except Exception:
            continue  # ScienceClaw not running; try again quietly.
        if job:
            busy.acquire()
            app.fireCustomEvent(EVENT_ID, json.dumps(job))


# ---------------------------------------------------------------- design helpers
def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        raise RuntimeError("Open the ScienceClaw design (a Fusion design document) first")
    return design


def _set_parameters(design, values):
    params = design.userParameters
    missing = [name for name in values if params.itemByName(name) is None]
    if missing:
        raise RuntimeError("Design lacks parameters " + ", ".join(missing) + "; run 'ScienceClaw: set up'")
    for name, expression in values.items():
        params.itemByName(name).expression = expression
    design.computeAll()


def _link(design, run_id, candidate_id):
    attributes = design.attributes
    for key, value in (("run_id", run_id), ("candidate_id", candidate_id)):
        existing = attributes.itemByName("ScienceClaw", key)
        if existing:
            existing.value = value
        else:
            attributes.add("ScienceClaw", key, value)


def _linked(design):
    get = lambda key: (design.attributes.itemByName("ScienceClaw", key) or None)  # noqa: E731
    run, candidate = get("run_id"), get("candidate_id")
    return (run.value if run else None), (candidate.value if candidate else None)


def _export(design):
    root = design.rootComponent
    folder = tempfile.mkdtemp(prefix="scienceclaw_")
    stl, step = os.path.join(folder, "part.stl"), os.path.join(folder, "part.step")
    manager = design.exportManager
    options = manager.createSTLExportOptions(root, stl)
    options.isBinaryFormat = True
    options.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
    manager.execute(options)
    manager.execute(manager.createSTEPExportOptions(step, root))
    params = design.userParameters
    values_mm, expressions = {}, {}
    for name in PARAMS:
        parameter = params.itemByName(name)
        if parameter:
            values_mm[name] = parameter.value * 10.0  # internal length unit is cm
            expressions[name] = parameter.expression
    properties = root.getPhysicalProperties(adsk.fusion.CalculationAccuracy.HighCalculationAccuracy)
    document = app.activeDocument
    try:
        version = document.dataFile.versionNumber if document and document.isSaved and document.dataFile else None
    except Exception:
        version = None  # Unsaved documents have no cloud version yet.
    payload = {"values_mm": values_mm, "parameters": expressions, "volume_mm3": properties.volume * 1000.0,
               "bodies": root.bRepBodies.count, "document": document.name if document else None,
               "version": version, "fusion_version": app.version}
    with open(stl, "rb") as handle:
        payload["stl_b64"] = base64.b64encode(handle.read()).decode("ascii")
    with open(step, "rb") as handle:
        payload["step_b64"] = base64.b64encode(handle.read()).decode("ascii")
    return payload


# ---------------------------------------------------------------- parametric model
def _polygon(sketch, points_cm):
    pts = [adsk.core.Point3D.create(x, y, 0) for x, y in points_cm]
    lines_api = sketch.sketchCurves.sketchLines
    lines = []
    for i in range(len(pts)):
        start = lines[-1].endSketchPoint if lines else pts[i]
        end = lines[0].startSketchPoint if i == len(pts) - 1 else pts[(i + 1) % len(pts)]
        lines.append(lines_api.addByTwoPoints(start, end))
    constraints = sketch.geometricConstraints
    for line in lines:
        a, b = line.startSketchPoint.geometry, line.endSketchPoint.geometry
        if abs(a.y - b.y) < 1e-9:
            constraints.addHorizontal(line)
        else:
            constraints.addVertical(line)
    return lines


def _dimension(sketch, a, b, horizontal, expression):
    orientation = (adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation if horizontal
                   else adsk.fusion.DimensionOrientations.VerticalDimensionOrientation)
    ga, gb = a.geometry, b.geometry
    text = adsk.core.Point3D.create((ga.x + gb.x) / 2 + (0 if horizontal else -0.5), (ga.y + gb.y) / 2 + (-0.5 if horizontal else 0), 0)
    dimension = sketch.sketchDimensions.addDistanceDimension(a, b, orientation, text)
    dimension.parameter.expression = expression


def _build_arm(design, family):
    """Profile sketch on the YZ plane with dimensions bound to sc_*; extruded by sc_length."""
    root = design.rootComponent
    sketch = root.sketches.add(root.yZConstructionPlane)
    sketch.name = "ScienceClaw section"
    p = {name: design.userParameters.itemByName(name).value for name in PARAMS}
    b, h, t = p["sc_base_width"], p["sc_wall_height"], p["sc_thickness"]
    H = h + t
    if family == "u_channel":
        lines = _polygon(sketch, [(0, 0), (b, 0), (b, H), (b - t, H), (b - t, t), (t, t), (t, H), (0, H)])
        P = [line.startSketchPoint for line in lines]
        sketch.geometricConstraints.addCoincident(P[0], sketch.originPoint)
        _dimension(sketch, P[0], P[1], True, "sc_base_width")
        _dimension(sketch, P[0], P[7], False, "sc_wall_height + sc_thickness")
        _dimension(sketch, P[1], P[2], False, "sc_wall_height + sc_thickness")
        _dimension(sketch, P[7], P[6], True, "sc_thickness")
        _dimension(sketch, P[3], P[2], True, "sc_thickness")
        _dimension(sketch, P[0], P[5], False, "sc_thickness")
    else:
        lines = _polygon(sketch, [(0, 0), (b, 0), (b, H), (0, H)])
        P = [line.startSketchPoint for line in lines]
        sketch.geometricConstraints.addCoincident(P[0], sketch.originPoint)
        _dimension(sketch, P[0], P[1], True, "sc_base_width")
        _dimension(sketch, P[0], P[3], False, "sc_wall_height + sc_thickness")
        if family == "rectangular_tube":
            inner = _polygon(sketch, [(t, t), (b - t, t), (b - t, H - t), (t, H - t)])
            Q = [line.startSketchPoint for line in inner]
            _dimension(sketch, P[0], Q[0], True, "sc_thickness")
            _dimension(sketch, P[0], Q[0], False, "sc_thickness")
            _dimension(sketch, Q[0], Q[1], True, "sc_base_width - 2 * sc_thickness")
            _dimension(sketch, Q[0], Q[3], False, "sc_wall_height - sc_thickness")
    profiles = [sketch.profiles.item(i) for i in range(sketch.profiles.count)]
    profile = (next(pr for pr in profiles if pr.profileLoops.count == 2) if family == "rectangular_tube"
               else max(profiles, key=lambda pr: pr.areaProperties().area))
    extrudes = root.features.extrudeFeatures
    extrude_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    extrude_input.setDistanceExtent(False, adsk.core.ValueInput.createByString("sc_length"))
    feature = extrudes.add(extrude_input)
    feature.name = "ScienceClaw support arm"
    design.computeAll()


def _setup(design, values=None, family="u_channel"):
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    params = design.userParameters
    created = []
    for name in PARAMS:
        expression = (values or DEFAULTS).get(name, DEFAULTS[name])
        if params.itemByName(name) is None:
            params.add(name, adsk.core.ValueInput.createByString(expression), "mm", "ScienceClaw design variable")
            created.append(name)
    built = False
    if design.rootComponent.bRepBodies.count == 0:
        _build_arm(design, family)
        built = True
    return created, built


# ---------------------------------------------------------------- event handlers
def _open_step(job):
    """Download a designer part's STEP from ScienceClaw and open it in a new Fusion document."""
    folder = tempfile.mkdtemp(prefix="scienceclaw_view_")
    path = os.path.join(folder, "".join(c if c.isalnum() or c in "-_ " else "_" for c in job.get("name", "part")) + ".step")
    with urllib.request.urlopen(SERVER + job["step_url"], timeout=60) as response, open(path, "wb") as handle:
        handle.write(response.read())
    options = app.importManager.createSTEPImportOptions(path)
    document = app.importManager.importToNewDocument(options)
    return {"document": document.name if document else job.get("name")}


class JobHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        job = None
        try:
            job = json.loads(args.additionalInfo)
            if job.get("kind") == "view_step":
                payload = _open_step(job)  # Designer part: open it for viewing; nothing is exported back.
            else:
                design = _design()
                if design.userParameters.itemByName("sc_length") is None:
                    _setup(design, job["parameters"], job.get("family", "u_channel"))
                _set_parameters(design, job["parameters"])
                _link(design, job["run_id"], job["candidate_id"])
                payload = _export(design)
        except Exception:
            payload = {"error": traceback.format_exc()[-800:]}
        finally:
            if busy.locked():
                busy.release()
        if job:
            try:
                _request("POST", f"/api/fusion/jobs/{job['id']}/result", payload, timeout=60)
            except Exception:
                ui.messageBox("ScienceClaw: could not send the build result.\n" + traceback.format_exc()[-400:])


class SetupCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        handler = SetupExecute()
        args.command.execute.add(handler)
        handlers.append(handler)


class SetupExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            choice = ui.inputBox("Section family: u_channel, solid_bar or rectangular_tube", "ScienceClaw set up", "u_channel")
            if choice[1]:
                return
            family = choice[0].strip() or "u_channel"
            if family not in ("u_channel", "solid_bar", "rectangular_tube"):
                ui.messageBox("Unknown section family: " + family)
                return
            created, built = _setup(_design(), None, family)
            ui.messageBox("ScienceClaw parameters created: " + (", ".join(created) or "already present") +
                          ("\nBuilt a parametric " + family + " driven by them." if built else
                           "\nExisting bodies kept. Make sure your model's dimensions reference the sc_* parameters."))
        except Exception:
            ui.messageBox("ScienceClaw set up failed:\n" + traceback.format_exc()[-800:])


class SendCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        handler = SendExecute()
        args.command.execute.add(handler)
        handlers.append(handler)


class SendExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            design = _design()
            run_id, candidate_id = _linked(design)
            payload = _export(design)
            payload.update(run_id=run_id, candidate_id=candidate_id)
            try:
                result = _request("POST", "/api/fusion/designs", payload, timeout=60)
            except urllib.error.HTTPError as error:
                ui.messageBox("ScienceClaw did not accept this design:\n" + json.loads(error.read().decode("utf-8")).get("detail", str(error)))
                return
            lines = [("NEW candidate " if result["new_candidate"] else "Candidate ") + result["candidate_id"],
                     "CAD matches the analyzed section: " + ("yes" if result["check"] == "pass" else "NO"), "", "Requirements (model predictions):"]
            lines += [("  OK  " if r["passed"] else "  FAIL  ") + r["label"] for r in result["requirements"]]
            if result["issues"]:
                lines += ["", "Problems found:"] + ["  - " + i["title"] + ": " + i["detail"][:160] for i in result["issues"]]
            lines += ["", "Open ScienceClaw to see the physics, or let the agents fix it."]
            if result["new_candidate"]:
                _link(design, run_id, result["candidate_id"])
            ui.messageBox("\n".join(lines), "ScienceClaw")
        except Exception:
            ui.messageBox("Send to ScienceClaw failed:\n" + traceback.format_exc()[-800:])


def _button(command_id, name, tooltip, created_handler):
    definitions = ui.commandDefinitions
    existing = definitions.itemById(command_id)
    if existing:
        existing.deleteMe()
    definition = definitions.addButtonDefinition(command_id, name, tooltip)
    definition.commandCreated.add(created_handler)
    handlers.append(created_handler)
    panel = ui.allToolbarPanels.itemById(PANEL_ID)
    if panel and not panel.controls.itemById(command_id):
        panel.controls.addCommand(definition)


def run(context):
    global app, ui
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        event = app.registerCustomEvent(EVENT_ID)
        job_handler = JobHandler()
        event.add(job_handler)
        handlers.append(job_handler)
        _button("ScienceClawSetup", "ScienceClaw: set up", "Create sc_* parameters and a parametric support arm", SetupCreated())
        _button("ScienceClawSend", "Send to ScienceClaw", "Check your edits against the study's physics and requirements", SendCreated())
        stopping.clear()
        threading.Thread(target=_poll, daemon=True).start()
    except Exception:
        if ui:
            ui.messageBox("ScienceClaw add-in failed to start:\n" + traceback.format_exc())


def stop(context):
    stopping.set()
    try:
        for command_id in ("ScienceClawSetup", "ScienceClawSend"):
            panel = ui.allToolbarPanels.itemById(PANEL_ID)
            control = panel.controls.itemById(command_id) if panel else None
            if control:
                control.deleteMe()
            definition = ui.commandDefinitions.itemById(command_id)
            if definition:
                definition.deleteMe()
        app.unregisterCustomEvent(EVENT_ID)
    except Exception:
        pass
