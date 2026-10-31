"""What is wrong with a candidate, the physics behind it, and concrete fixes.

Deterministic: every number comes from the beam model, fitted parameters, recorded
observations, CAD checks or PreForm reports. Agents read this; they cannot alter it.
Reserved and acceptance observations are excluded to keep held-out evidence held out.
"""
from __future__ import annotations

import math

from .domain import Geometry, ModelParameters, Observation, Protocol
from . import mechanics, measurement


def physics(geometry: Geometry, parameters: ModelParameters, protocol: Protocol, fit: dict | None = None) -> dict:
    """Proof-load deflection broken into bending and mount rotation, plus root stress."""
    props = mechanics.section_properties(geometry)
    force, a = protocol.proof_force_n, protocol.proof_position_m
    e, c_theta, inertia = parameters.youngs_modulus_pa, parameters.rotational_compliance_rad_per_nm, props["second_moment_m4"]
    bending = force * a**3 / (3 * e * inertia)
    mount = force * c_theta * a**2
    total = bending + mount
    margin = 2 * protocol.max_uncertainty_m
    height = max(y for _, y in mechanics._section_polygon_mm(geometry)) / 1000
    fiber = max(props["neutral_axis_m"], height - props["neutral_axis_m"])
    stress = mechanics.root_stress(geometry, force, a)
    uncertainty = (fit or {}).get("uncertainty") or {}
    model_sd = None
    if uncertainty:
        # Local 1-sigma propagation, ignoring E/C covariance: an indication, not an interval.
        model_sd = math.hypot(bending * uncertainty["youngs_modulus_pa_std"] / e,
                              force * a**2 * uncertainty["rotational_compliance_rad_per_nm_std"])
    return {
        "equation": "delta = F*a^3/(3*E*I) + F*C_theta*a^2",
        "force_n": force, "load_position_m": a, "youngs_modulus_pa": e, "rotational_compliance_rad_per_nm": c_theta,
        "area_m2": props["area_m2"], "second_moment_m4": inertia, "neutral_axis_m": props["neutral_axis_m"],
        "bending_m": bending, "mount_rotation_m": mount, "predicted_m": total,
        "bending_fraction": bending / total if total else None,
        "model_sd_m": model_sd, "measurement_margin_m": margin, "limit_m": protocol.max_deflection_m,
        "utilization": (total + margin) / protocol.max_deflection_m,
        "small_deflection_ratio": total / a, "small_deflection_limit": protocol.max_deflection_ratio,
        "root_moment_nm": force * a, "extreme_fiber_m": fiber, "root_stress_pa": stress, "allowable_stress_pa": protocol.max_root_stress_pa,
        "stress_note": ("Elastic bending stress at the clamp line, checked against your allowable stress requirement."
                        if protocol.max_root_stress_pa else
                        "Elastic bending stress at the clamp line; set an allowable stress requirement to check it."),
        "volume_m3": props["area_m2"] * geometry.length_m,
        "sensitivity": _sensitivity(geometry, parameters, protocol, total),
    }


def _sensitivity(geometry, parameters, protocol, total):
    """Effect of one fabrication-grid step in each free dimension."""
    rows = {}
    base_volume = mechanics.section_properties(geometry)["area_m2"] * geometry.length_m
    for key, high in (("base_width_m", protocol.base_width_max_m), ("wall_height_m", protocol.wall_height_max_m)):
        value = getattr(geometry, key) + protocol.geometry_step_m
        if value > high + 1e-12:
            rows[key] = None
            continue
        try:
            changed = Geometry(**{**geometry.model_dump(), key: round(value, 12)})
        except ValueError:
            rows[key] = None
            continue
        delta = mechanics.predict_deflection(changed, parameters, protocol.proof_force_n, protocol.proof_position_m)["displacement_m"]
        added = mechanics.section_properties(changed)["area_m2"] * changed.length_m - base_volume
        rows[key] = {"step_m": protocol.geometry_step_m, "deflection_change_m": delta - total, "volume_change_m3": added,
                     "deflection_change_per_m3": (delta - total) / added if added else None}
    return rows


def geometry_fixes(geometry: Geometry, parameters: ModelParameters, protocol: Protocol, existing: list[dict]) -> dict:
    """Grid geometries meeting every requirement under the current model, chosen by three priorities."""
    taken = [Geometry(**g) for g in existing]
    margin = 2 * protocol.max_uncertainty_m
    step = protocol.geometry_step_m
    rows, failing = [], []
    for width in mechanics._grid(protocol.base_width_min_m, protocol.base_width_max_m, step):
        for height in mechanics._grid(protocol.wall_height_min_m, protocol.wall_height_max_m, step):
            try:
                option = Geometry(**{**geometry.model_dump(), "base_width_m": width, "wall_height_m": height})
                mechanics.section_properties(option)
            except ValueError:
                continue
            if option in taken:
                continue
            predicted = mechanics.predict_deflection(option, parameters, protocol.proof_force_n, protocol.proof_position_m)["displacement_m"]
            failed = [r["name"] for r in mechanics.requirement_checks(option, parameters, protocol) if not r["passed"]]
            if failed:
                failing.append(failed)
                continue
            volume = mechanics.section_properties(option)["area_m2"] * option.length_m
            change = abs(width - geometry.base_width_m) / step + abs(height - geometry.wall_height_m) / step
            rows.append({"geometry": option.model_dump(), "predicted_m": predicted, "volume_m3": volume,
                         "utilization": (predicted + margin) / protocol.max_deflection_m, "grid_steps_changed": round(change)})
    if not rows:
        # Name the binding requirement: which single relaxation would open up designs.
        alone = {}
        for failed in failing:
            if len(failed) == 1:
                alone[failed[0]] = alone.get(failed[0], 0) + 1
        hint = "; ".join(f"relaxing {name.replace('_', ' ')} alone would allow {count} designs" for name, count in sorted(alone.items(), key=lambda kv: -kv[1]))
        return {"options": {}, "binding_requirements": alone,
                "note": "No geometry in the allowed ranges meets every requirement under the current model" + (f": {hint}." if hint else "; several requirements conflict at once.")}
    current_volume = mechanics.section_properties(geometry)["area_m2"] * geometry.length_m
    robust = [r for r in rows if r["utilization"] <= 0.75] or rows
    picks = {
        "least_material": min(rows, key=lambda r: (r["volume_m3"], r["grid_steps_changed"])),
        "smallest_change": min(rows, key=lambda r: (r["grid_steps_changed"], r["volume_m3"])),
        "most_margin": min(robust, key=lambda r: (r["volume_m3"], r["utilization"])) if robust is not rows else min(rows, key=lambda r: r["utilization"]),
    }
    for row in picks.values():
        row["volume_change_fraction"] = row["volume_m3"] / current_volume - 1
    return {"options": picks, "evaluated_passing": len(rows),
            "basis": "Current model parameters plus a two-sigma measurement margin; predictions, not measurements."}


def _mesh_differences(check: dict) -> list[str]:
    row = next((c for c in check["checks"] if c["name"] == "extents_match_section"), None)
    if row is None or row["passed"]:
        return []
    return [f"{e:g} mm expected, {a:.3g} mm exported" for e, a in zip(row["expected_mm"], row["actual_mm"]) if abs(e - a) > row["tolerance_mm"]]


def diagnose(*, candidate: dict, model: dict, protocol: Protocol, observations: list[Observation],
             candidates: list[dict], adopted_model: dict | None = None, cad: dict | None = None,
             project: dict | None = None) -> dict:
    geometry = Geometry(**candidate["geometry"])
    parameters = ModelParameters(**model["parameters"])
    # Latest records may carry fitted parameters forward; the adopted fit supplies uncertainty.
    fitted = adopted_model is not None and adopted_model["parameters"] == model["parameters"]
    fit = (adopted_model if fitted else model).get("fit") or {}
    report = physics(geometry, parameters, protocol, fit if fitted else None)
    issues = []

    def issue(code, severity, owner, title, detail, evidence=None, fixes=()):
        issues.append({"code": code, "severity": severity, "owner": owner, "title": title, "detail": detail,
                       "evidence": evidence or {}, "fixes": list(fixes)})

    own = [o for o in observations if o.candidate_id == candidate["id"] and o.purpose not in ("reserved", "acceptance")]
    assessed = [(o, measurement.classify_observation(o, protocol)) for o in own]
    if not fitted:
        position = mechanics.choose_diagnostic_position([o for o, _ in assessed], geometry, protocol)
        issue("model_unresolved", "warning", "mechanics", "Physics uses initial assumptions",
              f"E = {parameters.youngs_modulus_pa / 1e9:.3g} GPa and C_theta = {parameters.rotational_compliance_rad_per_nm:g} rad/N·m "
              f"are assumptions; fit status is '{fit.get('status', 'initial_assumption')}'. One station cannot separate stiffness from mount rotation.",
              {"fit_status": fit.get("status"), "warnings": fit.get("warnings", [])},
              [{"kind": "experiment", "description": f"Load the mounted reference at {position * 1000:g} mm without reclamping, then refit.",
                "load_position_m": position}])

    # Measurements that contradict the model point to which physics is missing.
    valid = [(o, a) for o, a in assessed if a["status"] != "invalid"]
    if valid:
        rows = []
        for obs, _ in valid:
            predicted = mechanics.predict_deflection(obs.geometry, parameters, obs.force_n, obs.load_position_m)["displacement_m"]
            rows.append({"observation_id": obs.id, "load_position_m": obs.load_position_m, "observed_m": obs.displacement_m,
                         "predicted_m": predicted, "z": (obs.displacement_m - predicted) / obs.uncertainty_m,
                         "ratio": obs.displacement_m / predicted if predicted else None})
        worst = max(rows, key=lambda r: abs(r["z"]))
        if abs(worst["z"]) > 3:
            stations = sorted({round(r["load_position_m"], 6) for r in rows})
            if len(stations) >= 2:
                near = [r["ratio"] for r in rows if round(r["load_position_m"], 6) == stations[0]]
                far = [r["ratio"] for r in rows if round(r["load_position_m"], 6) == stations[-1]]
                near_mean, far_mean = sum(near) / len(near), sum(far) / len(far)
                pattern = ("The error is larger near the clamp: missing mount rotation (grows with a^2) explains this better than lower stiffness."
                           if near_mean > 1.15 * far_mean else
                           "The error is similar at every station: lower effective stiffness (all bending scales with 1/E) explains this better than mount rotation."
                           if abs(near_mean / far_mean - 1) <= 0.15 else
                           "The error grows with distance: check for slip, nonlinearity or a load-position error.")
            else:
                pattern = ("Only one station measured: lower stiffness and mount rotation both explain this. "
                           "A second, well-separated station distinguishes them.")
            issue("model_discrepancy", "error" if worst["z"] > 0 else "warning", "mechanics",
                  "Measured bending disagrees with the model",
                  f"Observed {worst['observed_m'] * 1000:.3g} mm vs predicted {worst['predicted_m'] * 1000:.3g} mm at "
                  f"{worst['load_position_m'] * 1000:g} mm ({worst['z']:+.1f} sigma). {pattern}",
                  {"comparisons": rows},
                  [{"kind": "experiment", "description": "Run the next diagnostic station, then refit before redesigning."}])

    for obs, a in assessed:
        if a["status"] == "invalid":
            advice = {"fixture_motion_exceeds_limit": "Tighten or rebuild the clamp; track the fixture marker separately.",
                      "outside_small_deflection_model": "Use a lighter load or nearer station; the beam model needs small deflection.",
                      "dwell_too_short": "Wait the frozen dwell time before reading.",
                      "recovery_too_short": "Wait the frozen recovery time before the residual reading."}
            issue("invalid_measurement", "warning", "measurement", "A load cycle cannot be used",
                  f"Cycle {obs.id}: {', '.join(a['reasons'])}.", {"observation_id": obs.id, "reasons": a["reasons"]},
                  [{"kind": "process", "description": advice.get(r, f"Repeat the cycle after resolving {r}.")} for r in a["reasons"]])
        elif a["status"] == "fail" and "permanent_set_exceeds_limit" in a["reasons"]:
            issue("permanent_set", "error", "measurement", "Specimen did not spring back",
                  f"Residual {obs.residual_m * 1000:.3g} mm exceeds {protocol.max_residual_m * 1000:g} mm: plastic set, creep or damage. Retire it from elastic fitting.",
                  {"observation_id": obs.id}, [{"kind": "process", "description": "Print a fresh copy; check post-cure and load magnitude."}])
        elif a["status"] == "fail":
            issue("measured_over_limit", "error", "mechanics", "Measured proof deflection exceeds the limit",
                  f"{obs.displacement_m * 1000:.3g} ± {2 * obs.uncertainty_m * 1000:.2g} mm vs {protocol.max_deflection_m * 1000:g} mm limit.",
                  {"observation_id": obs.id})

    if report["predicted_m"] + report["measurement_margin_m"] > protocol.max_deflection_m:
        bending_share = report["bending_fraction"] or 0
        cause = ("Most of it is beam bending, so a deeper section (wall height raises I roughly with h^3) is the efficient fix."
                 if bending_share >= 0.5 else
                 "Most of it is mount rotation, which geometry barely changes; stiffen the clamp before adding material.")
        issue("predicted_over_limit", "error", "mechanics", "Predicted to bend too much",
              f"Predicted {report['predicted_m'] * 1000:.3g} mm + {report['measurement_margin_m'] * 1000:.2g} mm margin vs "
              f"{protocol.max_deflection_m * 1000:g} mm limit (utilization {report['utilization']:.0%}). {cause}",
              {"bending_m": report["bending_m"], "mount_rotation_m": report["mount_rotation_m"]})
    requirements = mechanics.requirement_checks(geometry, parameters, protocol)
    for row in requirements:
        if row["passed"] or row["name"] not in ("material", "root_stress"):
            continue
        if row["name"] == "material":
            issue("over_material_budget", "error", "fabrication", "Uses more material than the budget",
                  f"{row['value'] * 1e6:.3g} cm³ vs {row['limit'] * 1e6:.3g} cm³ allowed.")
        else:
            issue("stress_over_limit", "error", "mechanics", "Root bending stress exceeds the allowable",
                  f"{row['value'] / 1e6:.3g} MPa vs {row['limit'] / 1e6:.3g} MPa allowed. Stress is M·c/I: a deeper section raises I faster than c.")
    if report["small_deflection_ratio"] > protocol.max_deflection_ratio:
        issue("small_deflection_screen", "warning", "mechanics", "Outside the small-deflection model",
              f"Predicted deflection is {report['small_deflection_ratio']:.1%} of the load distance (screen {protocol.max_deflection_ratio:.0%}).")

    if cad is not None and cad["check"]["status"] != "pass":
        failed = [c["name"] for c in cad["check"]["checks"] if not c["passed"]]
        differences = _mesh_differences(cad["check"])
        variables = ", ".join(f"{k} = {v}" for k, v in {"sc_length": f"{geometry.length_m * 1000:g} mm", "sc_base_width": f"{geometry.base_width_m * 1000:g} mm",
                                                        "sc_wall_height": f"{geometry.wall_height_m * 1000:g} mm", "sc_thickness": f"{geometry.thickness_m * 1000:g} mm"}.items())
        issue("cad_mismatch", "error", "fabrication", "The CAD export is not the analyzed part",
              f"Failed: {', '.join(failed)}." + (f" Dimensions: {'; '.join(differences)}." if differences else "") +
              " Printing it would test a different part than the physics predicts.",
              {"cad_revision_id": cad["id"], "source": cad["source"], "mesh": cad["mesh"]},
              [{"kind": "cad", "description": f"Open the FreeCAD master model and check its Params sheet ({variables}) and that no solid was edited by hand."}
               if cad["source"] == "freecad" else
               {"kind": "cad", "description": f"Re-export from Onshape with {variables}, in millimeters, without fillets or holes."},
               {"kind": "cad", "description": "Or use ScienceClaw's parametric STL, which is exact for this section.", "cad_source": "local_fixture"}])
    if project is not None:
        validation, profile = project["validation"], project["profile"]
        if geometry.thickness_m * 1000 < profile["minimum_wall_mm"] - 1e-9:
            issue("thin_wall", "error", "fabrication", "Walls are thinner than this resin allows",
                  f"{geometry.thickness_m * 1000:g} mm section vs {profile['minimum_wall_mm']:g} mm minimum wall.",
                  {"project_id": project["id"]},
                  [{"kind": "not_fixable_in_study", "description": "Thickness is frozen for this study; choose a resin/preset with a smaller minimum wall, or start a new study at the thicker wall."}])
        for text in validation["issues"]:
            issue("preform_issue", "error", "fabrication", "PreForm printability failure", text, {"project_id": project["id"]},
                  [{"kind": "process", "description": "Inspect orientation and supports in PreForm, then prepare a new project."}])
        for text in validation["warnings"]:
            issue("preform_warning", "warning", "fabrication", "PreForm printability warning", text, {"project_id": project["id"]})

    fixes = None
    fixable = ("predicted_over_limit", "measured_over_limit", "stress_over_limit", "over_material_budget")
    if any(i["code"] in fixable for i in issues):
        fixes = geometry_fixes(geometry, parameters, protocol, [c["geometry"] for c in candidates])
        target = next(i for i in issues if i["code"] in fixable)
        target["fixes"].extend({"kind": "geometry", "priority": name, "description":
            f"{name.replace('_', ' ')}: base {o['geometry']['base_width_m'] * 1000:g} mm, wall {o['geometry']['wall_height_m'] * 1000:g} mm "
            f"-> {o['predicted_m'] * 1000:.3g} mm predicted, material {o['volume_change_fraction']:+.0%}", "geometry": o["geometry"]}
            for name, o in fixes["options"].items())
        if not fixes["options"]:
            target["fixes"].append({"kind": "not_fixable_in_study", "description": fixes["note"]})
    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: order[i["severity"]])
    return {"candidate_id": candidate["id"], "status": "problems" if any(i["severity"] == "error" for i in issues) else "warnings" if issues else "ok",
            "model": {"id": model["id"], "basis": "fitted" if fitted else "initial_assumption", "fit_status": fit.get("status")},
            "physics": report, "requirements": requirements, "issues": issues, "geometry_fixes": fixes}
