# Fusion add-in: watches runs/<run_id>/<candidate_id>/*.step and imports each new
# candidate for viewing. Runs inside Fusion's Python, NOT the project .venv;
# it must not import project modules.
import json
import os
import threading
import traceback
from pathlib import Path

import adsk.core
import adsk.fusion

# Repo layout: ScienceClaw_Project/fusion_addin/BracketViewer/BracketViewer.py
RUNS_DIR = Path(os.environ.get("BRACKET_RUNS_DIR",
                               Path(__file__).resolve().parents[2] / "runs"))
POLL_SECONDS = 2.0
EVENT_ID = "BracketViewer_StepReady"

app = adsk.core.Application.get()
ui = app.userInterface

_handlers = []        # keep event handlers alive
_stop = threading.Event()
_run_documents = {}   # run_id -> adsk.core.Document


def _log(message):
    app.log(f"[BracketViewer] {message}")


def _step_files():
    """All finished STEP files; freecad_bracket.py writes *.partial.step then renames."""
    if not RUNS_DIR.is_dir():
        return {}
    files = {}
    for path in RUNS_DIR.glob("*/*/*.step"):
        if path.stem.endswith(".partial"):
            continue
        try:
            files[path] = path.stat().st_mtime
        except OSError:
            pass  # removed between glob and stat
    return files


def _watch():
    # Only show candidates produced after the add-in starts.
    seen = _step_files()
    while not _stop.wait(POLL_SECONDS):
        for path, mtime in _step_files().items():
            if seen.get(path) != mtime:
                seen[path] = mtime
                app.fireCustomEvent(EVENT_ID, json.dumps({"path": str(path)}))


def _run_document(run_id):
    """One Fusion document per run; each candidate becomes a component in it."""
    doc = _run_documents.get(run_id)
    if doc is None or not doc.isValid:
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
        design.designType = adsk.fusion.DesignTypes.DirectDesignType
        _run_documents[run_id] = doc
    doc.activate()
    return doc


def _import_candidate(step_path):
    run_id, candidate_id = step_path.parent.parent.name, step_path.parent.name
    doc = _run_document(run_id)
    design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    root = design.rootComponent

    # Hide earlier candidates so the newest is visible; toggle them in the browser.
    for occurrence in root.occurrences:
        occurrence.isLightBulbOn = False

    occurrence = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occurrence.component.name = f"{run_id} - {candidate_id}"
    options = app.importManager.createSTEPImportOptions(str(step_path))
    app.importManager.importToTarget(options, occurrence.component)

    app.activeViewport.fit()
    _log(f"imported {step_path}")


class StepReadyHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        try:
            _import_candidate(Path(json.loads(args.additionalInfo)["path"]))
        except Exception:
            _log(f"import failed:\n{traceback.format_exc()}")


def run(context):
    try:
        event = app.registerCustomEvent(EVENT_ID)
        handler = StepReadyHandler()
        event.add(handler)
        _handlers.append(handler)

        _stop.clear()
        threading.Thread(target=_watch, daemon=True).start()
        _log(f"watching {RUNS_DIR}")
    except Exception:
        ui.messageBox(f"BracketViewer failed to start:\n{traceback.format_exc()}")


def stop(context):
    try:
        _stop.set()
        app.unregisterCustomEvent(EVENT_ID)
        _handlers.clear()
    except Exception:
        ui.messageBox(f"BracketViewer failed to stop:\n{traceback.format_exc()}")
