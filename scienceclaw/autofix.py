"""One button: the agents fix whatever on a design is fixable, and say what needs a person.

Order matters: a design is not repaired from a model that measurements contradict
(the agents run the diagnostic test instead), and CAD that differs from the analyzed
part is rebuilt from the design's own parameters before anything is printed.
"""
from .agents import GEOMETRY_ISSUES

HUMAN_ONLY = {"thin_wall", "invalid_measurement", "permanent_set", "preform_issue", "preform_warning",
              "small_deflection_screen", "measured_over_limit"}


def _build(run_id, candidate_id, *, cad_link, fusion, freecad):
    """Fusion when connected, else FreeCAD, else ScienceClaw's exact mesh."""
    if fusion is not None and fusion.status()["connected"]:
        return fusion.build_and_wait(run_id, candidate_id)
    try:
        return freecad.sync_candidate(run_id, candidate_id)
    except (ValueError, AttributeError):
        return cad_link.fixture_revision(run_id, candidate_id)


def fix_candidate(service, run_id, candidate_id, *, live=False, cad_link=None, fusion=None, freecad=None):
    actions, needs_you = [], []
    diagnosis = service.diagnose(run_id, candidate_id)
    codes = {i["code"] for i in diagnosis["issues"]}
    target, repair = candidate_id, None

    if "cad_mismatch" in codes:
        # Rebuild from parameters in FreeCAD (not Fusion: Fusion keeps the extra features that caused it).
        try:
            try:
                cad = freecad.sync_candidate(run_id, candidate_id)
            except (ValueError, AttributeError):
                cad = cad_link.fixture_revision(run_id, candidate_id)
            actions.append({"kind": "cad_rebuilt", "summary": f"Rebuilt the CAD from this design's parameters ({cad['source']}): "
                            f"{'now matches the analyzed part' if cad['check']['status'] == 'pass' else 'still differs'}. "
                            "Features added only in Fusion (fillets, holes) are not in the beam model, so the printable "
                            "part follows the analyzed section.", "cad_revision_id": cad["id"]})
        except ValueError as exc:
            needs_you.append({"title": "CAD rebuild failed", "action": str(exc)})

    diagnose_first = bool(codes & {"model_discrepancy", "model_unresolved"}) and diagnosis["model"]["basis"] != "fitted"
    if codes & GEOMETRY_ISSUES:
        try:
            result = service.repair(run_id, candidate_id, live=live)
            repair = result["repair"]
            if result["candidate"]:
                target = result["candidate"]["id"]
                selected = repair["selected"]
                summary = (f"The specialists agreed on a repaired design ({selected['agent']}'s fix: base "
                           f"{selected['base_width_m'] * 1000:g} mm, wall {selected['wall_height_m'] * 1000:g} mm, predicted "
                           f"{selected['predicted_m'] * 1000:.3g} mm).")
                try:
                    cad = _build(run_id, target, cad_link=cad_link, fusion=fusion, freecad=freecad)
                    summary += f" Built its CAD in {cad['source']}."
                except ValueError as exc:
                    needs_you.append({"title": "Build the repaired design's CAD", "action": str(exc)})
                actions.append({"kind": "repaired", "summary": summary, "candidate_id": target})
            elif repair["status"] == "diagnose_first":
                diagnose_first = True
            else:
                objections = "; ".join(r["reason"] for r in repair["reviews"] if not r["support"])[:400]
                actions.append({"kind": "repair_not_agreed", "summary": f"No agreed repair ({repair['status'].replace('_', ' ')})."
                                + (f" Objections: {objections}" if objections else "")})
                if repair["status"] == "no_valid_proposals":
                    note = (diagnosis.get("geometry_fixes") or {}).get("note")
                    needs_you.append({"title": "Requirements may conflict", "action": note or "Relax a requirement in a new study."})
        except ValueError as exc:
            needs_you.append({"title": "Repair not possible", "action": str(exc)})

    if diagnose_first or (not codes & GEOMETRY_ISSUES and any(f["kind"] == "experiment" for i in diagnosis["issues"] for f in i["fixes"])):
        if service.pending(run_id):
            needs_you.append({"title": "Record the pending measurement",
                              "action": "The agents already chose a test; run it and record the load cycle in step 4."})
        else:
            try:
                step = service.step(run_id, live=live)
                status = step["status"]
                experiment = step.get("experiment")
                where = f" at {experiment['load_position_m'] * 1000:g} mm" if experiment else ""
                actions.append({"kind": "diagnostic_test", "summary": {
                    "executed": f"The agents ran the diagnostic test{where} and refit the model.",
                    "awaiting_measurement": f"The agents chose the diagnostic test{where}; run it and record it in step 4.",
                }.get(status, f"Agent round ended: {status.replace('_', ' ')}.")})
            except ValueError as exc:
                needs_you.append({"title": "Diagnostic test not possible", "action": str(exc)})

    for issue in diagnosis["issues"]:
        if issue["code"] in HUMAN_ONLY:
            hint = issue["fixes"][0]["description"] if issue["fixes"] else issue["detail"]
            needs_you.append({"title": issue["title"], "action": hint})
    if not actions and not needs_you:
        actions.append({"kind": "nothing", "summary": "Nothing to fix with the current evidence."})
    return {"candidate_id": target, "actions": actions, "needs_you": needs_you, "repair": repair,
            "diagnosis_after": service.diagnose(run_id, target)}
