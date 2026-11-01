"""Application workflow: immutable designs, bounded tests, and explicit evidence sources."""
import os
import time
import math
from pathlib import Path

import numpy as np

from .domain import Geometry, ModelParameters, Observation, Protocol, SimulationScenario, TeamMember, identifier, utc_now
from .store import Store
from . import mechanics, measurement


METHODS = ("collective", "adaptive", "single_agent", "independent")
POLICIES = {"collective": ("three_provider_collective", "rule_based_specialist_demo"),
            "single_agent": ("live_single_agent", "rule_based_single_agent"),
            "independent": ("live_independent_specialists", "rule_based_independent_specialists")}


class CoForm:
    def __init__(self, directory: str | Path = "data"):
        self.store = Store(directory)
        self.root = Path(directory)
        # Set by the app: (run_id, candidate_id) -> checked CAD STEP path or None, for the virtual rig.
        self.cad_step = None

    def create_run(self, *, name="Printable support study", mode="simulated", method="collective",
                   seed=7, geometry: Geometry | None = None, protocol: Protocol | None = None,
                   scenario: SimulationScenario | None = None, team: dict | None = None):
        if mode not in ("simulated", "physical") or method not in METHODS:
            raise ValueError("Unsupported mode or method")
        if team is not None:
            if not team or not set(team) <= {"mechanics", "fabrication", "measurement", "single"}:
                raise ValueError("A team assigns mechanics, fabrication, measurement and optionally single")
            team = {role: TeamMember.model_validate(member).model_dump() for role, member in team.items()}
        geometry, protocol = geometry or Geometry(), protocol or Protocol()
        if scenario is not None and mode != "simulated":
            raise ValueError("Simulation scenarios cannot configure a physical study")
        self._validate_geometry(geometry, geometry, protocol)
        if max(*protocol.diagnostic_positions_m, protocol.reserved_position_m, protocol.proof_position_m) > geometry.free_length_m:
            raise ValueError("All test positions must fit within the unclamped length")
        with self.store.atomic():
            run_id = identifier("run")
            run = self.store.put("run", run_id, {"id": run_id, "name": name, "mode": mode, "method": method,
                "seed": seed, "protocol": protocol.model_dump(mode="json"), "reference_geometry": geometry.model_dump(),
                "created_at": utc_now(), "fabrication": "3d_printed_" + geometry.section_family, "settings_status": "provisional_pilot",
                "simulation_scenario": (scenario or SimulationScenario()).model_dump() if mode == "simulated" else None,
                "team": team})
            model_id = identifier("model")
            model = self.store.put("model", run_id, {"id": model_id, "parameters": ModelParameters().model_dump(),
                "fit": {"status": "initial_assumption", "source_observation_ids": []}, "adopted": False,
                "parameters_model_id": model_id, "parameters_status": "initial_assumption"})
            candidate = self._candidate(run, geometry, model["id"], None, "reference", reference=True)
            if mode == "simulated":
                self.fabricate(run_id, candidate["id"], f"reference-{run_id}", geometry, notes="Simulator reference specimen")
                for force in (protocol.proof_force_n / 2, protocol.proof_force_n / 2, protocol.proof_force_n, protocol.proof_force_n):
                    exp = self._queue(run, candidate, force, protocol.diagnostic_positions_m[-1], "initial", f"reference-{run_id}", "Shared initial load cycle")
                    self._simulate(run, exp)
                self.fit(run_id)
            return self.state(run_id)

    @staticmethod
    def _validate_geometry(geometry, reference, protocol):
        for field in ("section_family", "length_m", "clamp_length_m", "thickness_m", "material_batch"):
            if getattr(geometry, field) != getattr(reference, field):
                raise ValueError(f"Frozen geometry field changed: {field}")
        if not protocol.base_width_min_m <= geometry.base_width_m <= protocol.base_width_max_m:
            raise ValueError("Base width is outside the frozen design bounds")
        if not protocol.wall_height_min_m <= geometry.wall_height_m <= protocol.wall_height_max_m:
            raise ValueError("Wall height is outside the frozen design bounds")
        if geometry.thickness_m < 0.0008 or geometry.base_width_m <= 2 * geometry.thickness_m:
            raise ValueError("Pilot print geometry requires walls >=0.8 mm and base wider than two walls")
        for value, minimum in ((geometry.base_width_m, protocol.base_width_min_m), (geometry.wall_height_m, protocol.wall_height_min_m)):
            step = (value - minimum) / protocol.geometry_step_m
            if not math.isclose(step, round(step), abs_tol=1e-7):
                raise ValueError("Geometry must lie on the frozen fabrication grid")

    def _candidate(self, run, geometry, model_id, parent_id, proposed_by, reference=False):
        self._validate_geometry(geometry, Geometry(**run["reference_geometry"]), Protocol(**run["protocol"]))
        if not reference and self._budget(run)["designs_remaining"] <= 0:
            raise ValueError("Design budget exhausted")
        artifacts = {ext: self.store.artifact(fn(geometry), ext) for ext, fn in
                     (("stl", mechanics.generate_stl), ("scad", mechanics.generate_scad), ("svg", mechanics.generate_template))}
        props = mechanics.section_properties(geometry)
        return self.store.put("candidate", run["id"], {"id": identifier("candidate"), "run_id": run["id"],
            "geometry": geometry.model_dump(), "model_id": model_id, "parent_id": parent_id,
            "proposed_by": proposed_by, "reference": reference, "artifacts": artifacts,
            "section": props, "volume_m3": props["area_m2"] * geometry.length_m,
            "created_at": utc_now()})

    def add_candidate(self, run_id, geometry: Geometry, parent_id=None, proposed_by="human"):
        with self.store.atomic():
            run = self.store.get(run_id, "run")
            self._ensure_active(run)
            if parent_id:
                self._owned(run_id, parent_id, "candidate")
            candidate = self._candidate(run, geometry, self.latest_model(run_id)["id"], parent_id, proposed_by)
            self.store.event(run_id, "human_intervention", {"action": "candidate_created", "candidate_id": candidate["id"]})
            return candidate

    def _owned(self, run_id, record_id, kind):
        candidates = self.store.list(run_id, kind)
        result = next((item for item in candidates if item["id"] == record_id), None)
        if result is None:
            raise ValueError(f"{kind} does not belong to this run")
        return result

    def fabricate(self, run_id, candidate_id, specimen_id, actual_geometry=None, notes="", duration_s=0.0):
        with self.store.atomic():
            run = self.store.get(run_id, "run")
            candidate = self._owned(run_id, candidate_id, "candidate")
            if not specimen_id.strip() or not notes.strip():
                raise ValueError("A specimen identifier and fabrication notes are required")
            if not math.isfinite(duration_s) or duration_s < 0:
                raise ValueError("Fabrication duration must be finite and nonnegative")
            if any(f["specimen_id"] == specimen_id for f in self.store.list(run_id, "fabrication")):
                raise ValueError("Specimen identifier already registered")
            actual = actual_geometry or Geometry(**candidate["geometry"])
            if actual.material_batch != candidate["geometry"]["material_batch"]:
                raise ValueError("Specimen material batch must match its candidate")
            return self.store.put("fabrication", run_id, {"id": identifier("fab"), "candidate_id": candidate_id,
                "specimen_id": specimen_id, "actual_geometry": actual.model_dump(), "notes": notes,
                "duration_s": duration_s, "source": run["mode"], "created_at": utc_now()})

    def latest_model(self, run_id):
        return self.store.list(run_id, "model")[-1]

    def last_adopted_model(self, run_id):
        return next((model for model in reversed(self.store.list(run_id, "model")) if model.get("adopted")), None)

    def _observations(self, run_id):
        return [Observation(**item) for item in self.store.list(run_id, "observation")]

    def _budget(self, run):
        experiments = self.store.list(run["id"], "experiment")
        decisions = self.store.list(run["id"], "decision") + self.store.list(run["id"], "print_review") + self.store.list(run["id"], "repair")
        cycles = sum(e["purpose"] in ("search", "reserved") for e in experiments)
        designs = sum(not c["reference"] for c in self.store.list(run["id"], "candidate"))
        # Reserve uncertain provider usage conservatively; never treat an errored call as free.
        tokens = sum(max(d.get("usage", {}).get("total_tokens", 0), d.get("usage", {}).get("reserved_tokens", 0)) for d in decisions)
        return {"cycles_used": cycles, "cycles_remaining": run["protocol"]["search_cycles"] - cycles,
                "designs_used": designs, "designs_remaining": run["protocol"]["max_designs"] - designs,
                "tokens_used": tokens, "tokens_remaining": max(0, run["protocol"]["token_budget"] - tokens)}

    def pending(self, run_id):
        done = {o["experiment_id"] for o in self.store.list(run_id, "observation")}
        return next((e for e in self.store.list(run_id, "experiment") if e["id"] not in done), None)

    def _queue(self, run, candidate, force, position, purpose, specimen_id, reason):
        protocol = Protocol(**run["protocol"])
        if self.pending(run["id"]):
            raise ValueError("The fixture already has a pending experiment")
        if purpose in ("search", "reserved") and self._budget(run)["cycles_remaining"] <= 0:
            raise ValueError("Search cycle budget exhausted")
        geom = Geometry(**candidate["geometry"])
        if not 0 < force <= protocol.proof_force_n or not 0 < position <= geom.free_length_m:
            raise ValueError("Experiment exceeds the pilot force or length limits")
        model = self.latest_model(run["id"])
        prediction = mechanics.predict_deflection(geom, ModelParameters(**model["parameters"]), force, position)
        return self.store.put("experiment", run["id"], {"id": identifier("experiment"), "candidate_id": candidate["id"],
            "specimen_id": specimen_id, "force_n": force, "load_position_m": position, "purpose": purpose,
            "reason": reason, "model_id": model.get("parameters_model_id", model["id"]),
            "assessment_model_id": model["id"], "prediction": prediction, "created_at": utc_now(),
            "instructions": [f"Use specimen {specimen_id}; clamp {geom.clamp_length_m * 1000:g} mm of its length.",
                f"Apply {force:g} N including holder at {position * 1000:g} mm from clamp.",
                f"Record settled displacement after {protocol.dwell_s:g} s; unload and recover for {protocol.recovery_s:g} s.",
                "Record uncertainty, residual displacement, fixture motion, and raw evidence."]})

    def _simulate(self, run, experiment):
        # Simulator truth is deliberately not exposed in agent context. This is NOT a physical result.
        candidate = self._owned(run["id"], experiment["candidate_id"], "candidate")
        fabrication = next((f for f in self.store.list(run["id"], "fabrication") if f["specimen_id"] == experiment["specimen_id"] and f["candidate_id"] == candidate["id"]), None)
        geom = Geometry(**(fabrication["actual_geometry"] if fabrication else candidate["geometry"]))
        count = len(self.store.list(run["id"], "observation"))
        rng = np.random.default_rng(run["seed"] * 1000 + count)
        scenario = SimulationScenario(**(run.get("simulation_scenario") or {}))
        notes = "Explicit synthetic observation"
        if scenario.physics == "fea":
            delta, notes = self._rig_deflection(run, candidate, geom, experiment, scenario)
        else:
            truth = ModelParameters(youngs_modulus_pa=scenario.youngs_modulus_pa, rotational_compliance_rad_per_nm=scenario.rotational_compliance_rad_per_nm)
            delta = mechanics.predict_deflection(geom, truth, experiment["force_n"], experiment["load_position_m"])["displacement_m"]
        delta *= 1 + scenario.nonlinear_force_coefficient * experiment["force_n"]
        if experiment["purpose"] == "acceptance":
            delta *= float(rng.normal(1, 0.025))
        return self.record(run["id"], experiment["id"], displacement_m=delta + float(rng.normal(0, scenario.noise_std_m)),
            uncertainty_m=scenario.noise_std_m, residual_m=scenario.residual_m, fixture_motion_m=scenario.fixture_motion_m, dwell_s=run["protocol"]["dwell_s"],
            recovery_s=run["protocol"]["recovery_s"], human_notes=notes, simulated=True)

    def _rig_deflection(self, run, candidate, geom, experiment, scenario):
        """Virtual test rig: 3D finite elements of the part, datasheet resin, elastic clamp jaws."""
        from . import fea
        protocol = Protocol(**run["protocol"])
        stations = sorted({*protocol.diagnostic_positions_m, protocol.reserved_position_m, protocol.proof_position_m,
                           experiment["load_position_m"]})
        stations = [s for s in stations if s <= geom.free_length_m + 1e-12]
        material = fea.MATERIALS[scenario.material]
        modulus = fea.draw_modulus(scenario.material, run["seed"], scenario.cure_fraction)
        options = dict(modulus_pa=modulus, poisson=material["poisson"], fixture=scenario.fixture, stations_m=stations,
                       cache_dir=self.root / "fea_cache")
        # The checked CAD export when there is one (and the specimen was built as designed).
        step = self.cad_step(run["id"], candidate["id"]) if self.cad_step and geom == Geometry(**candidate["geometry"]) else None
        fallback = ""
        try:
            solution = fea.solve(geom, step=step, **options)
        except fea.GeometryMismatch as exc:
            solution, fallback = fea.solve(geom, **options), f" CAD STEP not used ({exc}); meshed the identical generated section."
        delta = fea.compliance_at(solution, experiment["load_position_m"]) * experiment["force_n"]
        source = "the checked CAD export" if solution["geometry_source"] == "cad_step" else "the generated section"
        return delta, (f"Virtual test rig: {solution['solver']} of {source}; {material['label']} (stiffness drawn from its datasheet range, "
                       f"hidden); {fea.FIXTURES[scenario.fixture]['label']}.{fallback}")

    def record(self, run_id, experiment_id, *, displacement_m, uncertainty_m, residual_m, fixture_motion_m,
               dwell_s, recovery_s, quality_flags=(), raw_csv=None, human_notes="", mass_kg=None, simulated=False,
               actual_force_n=None, actual_load_position_m=None):
        with self.store.atomic():
            run = self.store.get(run_id, "run")
            experiment = self._owned(run_id, experiment_id, "experiment")
            if (run["mode"] == "simulated") != simulated:
                raise ValueError("Physical and simulated observations cannot be mixed")
            if any(o.experiment_id == experiment_id for o in self._observations(run_id)):
                raise ValueError("Experiment already has an immutable observation")
            fab = next((f for f in self.store.list(run_id, "fabrication") if f["specimen_id"] == experiment["specimen_id"] and f["candidate_id"] == experiment["candidate_id"]), None)
            if fab is None:
                raise ValueError("Register this specimen's fabrication and actual dimensions first")
            if not simulated and not human_notes.strip() and not raw_csv:
                raise ValueError("Manual measurements need notes describing the measurement method")
            if not simulated and (actual_force_n is None or actual_load_position_m is None):
                raise ValueError("Physical observations require measured force and measured load position")
            force = experiment["force_n"] if actual_force_n is None else actual_force_n
            position = experiment["load_position_m"] if actual_load_position_m is None else actual_load_position_m
            if not math.isfinite(force) or force <= 0 or not math.isfinite(position) or position <= 0:
                raise ValueError("Measured force and load position must be finite and positive")
            flags = list(quality_flags)
            if force > run["protocol"]["proof_force_n"] + 1e-12:
                flags.append("force_exceeds_pilot_limit")
            raw = self.store.artifact(raw_csv, "csv")["filename"] if raw_csv else None
            obs = Observation(candidate_id=experiment["candidate_id"], specimen_id=experiment["specimen_id"],
                experiment_id=experiment_id, geometry=Geometry(**fab["actual_geometry"]), force_n=force,
                load_position_m=position, displacement_m=displacement_m, uncertainty_m=uncertainty_m,
                dwell_s=dwell_s, recovery_s=recovery_s, residual_m=residual_m, fixture_motion_m=fixture_motion_m,
                quality_flags=tuple(flags), source=run["mode"], purpose=experiment["purpose"], raw_artifact=raw,
                human_notes=human_notes, mass_kg=mass_kg)
            self.store.put("observation", run_id, obs.model_dump(mode="json"))
            result = measurement.classify_observation(obs, Protocol(**run["protocol"]))
            self.store.event(run_id, "measurement_completed", {"observation_id": obs.id, "assessment": result})
            if obs.purpose == "reserved":
                self.store.event(run_id, "reserved_prediction_scored", {"observation_id": obs.id, "model_id": experiment["model_id"],
                    "predicted_m": experiment["prediction"]["displacement_m"], "observed_m": obs.displacement_m,
                    "absolute_error_m": abs(experiment["prediction"]["displacement_m"] - obs.displacement_m)})
            return {"observation": obs.model_dump(mode="json"), "assessment": result}

    def fit(self, run_id):
        with self.store.atomic():
            run = self.store.get(run_id, "run")
            reference_id = self.store.list(run_id, "candidate")[0]["id"]
            observations = [o for o in self._observations(run_id) if o.candidate_id == reference_id]
            # Preserve a single mounting condition for the two-parameter fit.
            if observations:
                observations = [o for o in observations if o.specimen_id == observations[0].specimen_id]
            report = mechanics.fit_model(observations, Protocol(**run["protocol"]))
            latest = self.latest_model(run_id)
            if latest.get("fit") == report:
                return latest
            adopted = report["status"] == "identifiable" and report.get("parameters") is not None
            model_id = identifier("model")
            return self.store.put("model", run_id, {"id": model_id, "parameters": report["parameters"] if adopted else latest["parameters"],
                "fit": report, "adopted": adopted, "parent_id": latest["id"],
                "parameters_model_id": model_id if adopted else latest.get("parameters_model_id", latest["id"]),
                "parameters_status": "fitted" if adopted else "carried_forward_not_refitted"})

    def _ensure_active(self, run):
        if self.store.list(run["id"], "nomination"):
            raise ValueError("Final design is frozen; search has ended")

    def _specimen(self, run, candidate):
        prior = [f for f in self.store.list(run["id"], "fabrication") if f["candidate_id"] == candidate["id"]]
        if prior:
            return prior[0]["specimen_id"]
        specimen_id = f"search-{candidate['id']}"
        if run["mode"] == "simulated":
            self.fabricate(run["id"], candidate["id"], specimen_id, notes="Simulated specimen")
        return specimen_id

    def step(self, run_id, *, live=False):
        # One transaction serializes decisions and reserves both fixture and budgets.
        with self.store.atomic():
            run = self.store.get(run_id, "run")
            self._ensure_active(run)
            if self.pending(run_id):
                return {"status": "awaiting_measurement", "experiment": self.pending(run_id)}
            protocol = Protocol(**run["protocol"])
            candidates = self.store.list(run_id, "candidate")
            reference = candidates[0]
            observations = self._observations(run_id)
            initial_count = sum(o.purpose == "initial" for o in observations)
            if initial_count < 4:
                force = protocol.proof_force_n * (0.5 if initial_count < 2 else 1)
                exp = self._queue(run, reference, force, protocol.diagnostic_positions_m[-1], "initial", self._specimen(run, reference), "Shared pilot cycle")
                return {"status": "awaiting_measurement", "experiment": exp}
            budget = self._budget(run)
            if budget["cycles_remaining"] <= 0:
                return {"status": "budget_exhausted"}
            model = self.fit(run_id)
            geom = Geometry(**reference["geometry"])
            diagnostic_observations = [o for o in observations if o.candidate_id == reference["id"]]
            if diagnostic_observations:
                diagnostic_observations = [o for o in diagnostic_observations if o.specimen_id == diagnostic_observations[0].specimen_id]
            position = mechanics.choose_diagnostic_position(diagnostic_observations, geom, protocol)
            optimization = mechanics.optimize_geometry(geom, ModelParameters(**model["parameters"]), protocol) if model["adopted"] else {"geometry": None, "evaluations": 0}
            self.store.event(run_id, "numerical_search", {"evaluations": optimization["evaluations"], "model_id": model["id"]})
            if run["method"] == "adaptive":
                selected = self._adaptive_action(run, candidates, observations, model, optimization, position)
                decision = {"status": "selected", "selected": selected, "usage": {}, "transcript": [], "policy": "adaptive_numerical"}
            else:
                prior_decisions = self.store.list(run_id, "decision")
                cad = {r["asset_id"]: r for r in self.store.list(run_id, "cad_revision")}
                reviews = {r["project_id"]: r["status"] for r in self.store.list(run_id, "print_review")}
                print_reports = [{"id": p["id"], "candidate_id": p["candidate_id"], "source": "preform_api",
                    "grade": p["validation"]["grade"], "issues": p["validation"]["issues"], "warnings": p["validation"]["warnings"],
                    "profile": p["profile"], "cad_source": cad.get(p["asset_id"], {}).get("source"),
                    "cad_check": cad.get(p["asset_id"], {}).get("check", {}).get("status"), "print_review": reviews.get(p["id"]),
                    "mechanical_validation": "Not established by printability checks"}
                    for p in self.store.list(run_id, "print_project")]
                context = {"observations": [o.model_dump(mode="json") for o in observations if o.purpose not in ("reserved", "acceptance")],
                    "candidates": candidates, "model": model, "protocol": run["protocol"], "budget": budget,
                    "printability_reports": print_reports,
                    "evidence_ids": [o.id for o in observations if o.purpose not in ("reserved", "acceptance")] + [model["id"]] + [c["id"] for c in candidates] + [p["id"] for p in print_reports],
                    "agent_states": prior_decisions[-1].get("agent_states", {}) if prior_decisions else {},
                    "tools": {"recommended_geometry": optimization.get("geometry"), "diagnostic_position_m": position,
                              "predictions": [{"candidate_id": c["id"], **mechanics.predict_deflection(Geometry(**c["geometry"]), ModelParameters(**model["parameters"]), protocol.proof_force_n, protocol.proof_position_m)} for c in candidates]}}
                decision = self._collective_decision(context, live, run["method"], run.get("team"))
                selected = decision.get("selected")
            decision.setdefault("id", identifier("decision"))
            self.store.put("decision", run_id, decision)
            if not selected:
                self.store.event(run_id, "failure_to_progress", {"decision_id": decision["id"], "status": decision["status"]})
                return decision
            # Keep the metered decision even if execution fails. Nested Store.atomic
            # does not create savepoints, so use one around only the action writes.
            self.store.db.execute("SAVEPOINT execute_action")
            try:
                result = self._execute_action(run, selected, model, decision["id"])
            except Exception as exc:
                self.store.db.execute("ROLLBACK TO SAVEPOINT execute_action")
                self.store.db.execute("RELEASE SAVEPOINT execute_action")
                error = {"type": type(exc).__name__, "reason": "Selected action failed deterministic execution checks; no experiment or design was committed"}
                self.store.event(run_id, "execution_rejected", {"decision_id": decision["id"], **error})
                return {"status": "execution_rejected", "decision": decision, "error": error}
            self.store.db.execute("RELEASE SAVEPOINT execute_action")
            return {"status": "executed" if run["mode"] == "simulated" else "awaiting_measurement", "decision": decision, **result}

    @staticmethod
    def _live_providers(team=None):
        """The study's frozen team if it has one, otherwise the .env role assignment."""
        from .providers import from_environment
        result = {}
        for role, default in (("mechanics", "openai"), ("fabrication", "anthropic"), ("measurement", "gemini")):
            member = (team or {}).get(role)
            result[role] = (from_environment(member["provider"], member["model"]) if member else
                            from_environment(os.getenv(f"{role.upper()}_PROVIDER", default), os.getenv(f"{role.upper()}_MODEL") or None))
        return result

    @staticmethod
    def _single_provider(team=None):
        from .providers import from_environment
        member = (team or {}).get("single") or (team or {}).get("mechanics")
        if member:
            return from_environment(member["provider"], member["model"])
        name = os.getenv("SINGLE_AGENT_PROVIDER") or os.getenv("MECHANICS_PROVIDER", "openai")
        return from_environment(name, os.getenv("SINGLE_AGENT_MODEL") or os.getenv("MECHANICS_MODEL") or None)

    @classmethod
    def _collective_decision(cls, context, live, method="collective", team=None):
        from .agents import decide, decide_independent, decide_single
        started = time.monotonic()
        providers = None
        configuration_complete = not live
        try:
            if live:
                providers = cls._single_provider(team) if method == "single_agent" else cls._live_providers(team)
                configuration_complete = True
            choose = {"collective": decide, "single_agent": decide_single, "independent": decide_independent}[method]
            decision = choose(context, provider=providers)
        except Exception as exc:
            # Provider adapters normally return safe, metered failures through decide.
            # Unexpected runtime failures may have spent tokens: reserve the remainder
            # rather than erasing that possibility when no usage report is available.
            reserved = context["budget"]["tokens_remaining"] if live and configuration_complete else 0
            decision = {"status": "decision_runtime_error" if configuration_complete else "provider_configuration_error",
                "selected": None, "usage": {"total_tokens": reserved, "reserved_tokens": reserved},
                "transcript": [{"type": "decision_error", "error_type": type(exc).__name__,
                    "reason": "Check provider configuration" if not configuration_complete else "Decision failed before a complete result was available"}],
                "agent_states": context.get("agent_states", {})}
        decision["elapsed_s"] = time.monotonic() - started
        decision["policy"] = POLICIES[method][0 if live else 1]
        return decision

    def _adaptive_action(self, run, candidates, observations, model, optimization, position):
        protocol = Protocol(**run["protocol"])
        if not model["adopted"]:
            return {"action": "experiment", "candidate_id": candidates[0]["id"], "force_n": protocol.proof_force_n,
                    "load_position_m": position, "reason": "Select a separated station to identify stiffness and mount compliance"}
        proposed = optimization.get("geometry")
        same = next((c for c in candidates if c["geometry"] == proposed), None)
        if proposed and not same and self._budget(run)["designs_remaining"] > 0:
            return {"action": "design", "candidate_id": candidates[0]["id"], "geometry": proposed,
                    "reason": "Minimum material volume on the fabrication grid using fitted parameters"}
        candidate = same or candidates[-1]
        return {"action": "experiment", "candidate_id": candidate["id"], "force_n": protocol.proof_force_n,
                "load_position_m": protocol.proof_position_m, "reason": "Validate candidate under frozen proof condition"}

    def _execute_action(self, run, selected, model, decision_id):
        # Agent text cannot bypass deterministic checks or change a protocol.
        action = selected.get("action", selected.get("type"))
        candidate = self._owned(run["id"], selected["candidate_id"], "candidate")
        protocol = Protocol(**run["protocol"])
        if action == "design":
            if selected.get("force_n", protocol.proof_force_n) != protocol.proof_force_n or selected.get("load_position_m", protocol.proof_position_m) != protocol.proof_position_m:
                raise ValueError("New designs must use the frozen proof condition")
            geometry = Geometry(**selected["geometry"])
            prior = next((c for c in self.store.list(run["id"], "candidate") if c["geometry"] == geometry.model_dump()), None)
            candidate = prior or self._candidate(run, geometry, model["id"], candidate["id"], selected.get("agent", "collective"))
            force, position = protocol.proof_force_n, protocol.proof_position_m
        elif action == "experiment":
            force, position = selected["force_n"], selected["load_position_m"]
            if position not in protocol.diagnostic_positions_m and position != protocol.proof_position_m:
                raise ValueError("Agent experiment is outside the frozen diagnostic menu")
        else:
            raise ValueError("Unsupported agent action")
        exp = self._queue(run, candidate, force, position, "search", self._specimen(run, candidate), selected.get("reason", ""))
        self.store.event(run["id"], "proposal_closed", {"decision_id": decision_id, "experiment_id": exp["id"]})
        result = self._simulate(run, exp) if run["mode"] == "simulated" else None
        return {"experiment": exp, "measurement": result}

    def design_space(self, run_id):
        """Every grid design under the current model, for the design-space map. Predictions only."""
        with self.store.lock:
            run = self.store.get(run_id, "run")
            protocol = Protocol(**run["protocol"])
            reference = Geometry(**run["reference_geometry"])
            model = self.latest_model(run_id)
            parameters = ModelParameters(**model["parameters"])
            cells = []
            for width in mechanics._grid(protocol.base_width_min_m, protocol.base_width_max_m, protocol.geometry_step_m):
                for height in mechanics._grid(protocol.wall_height_min_m, protocol.wall_height_max_m, protocol.geometry_step_m):
                    try:
                        geometry = Geometry(**{**reference.model_dump(), "base_width_m": width, "wall_height_m": height})
                        checks = mechanics.requirement_checks(geometry, parameters, protocol)
                    except ValueError:
                        continue
                    deflection = next(r for r in checks if r["name"] == "deflection")
                    cells.append({"base_width_m": width, "wall_height_m": height, "utilization": deflection["value"] / deflection["limit"],
                                  "volume_m3": mechanics.section_properties(geometry)["area_m2"] * geometry.length_m,
                                  "passes": all(r["passed"] for r in checks), "failed": [r["name"] for r in checks if not r["passed"]]})
            fitted = self.last_adopted_model(run_id)
            return {"model_id": model["id"], "basis": "fitted" if fitted and fitted["parameters"] == model["parameters"] else "initial_assumption",
                    "step_m": protocol.geometry_step_m, "cells": cells}

    def diagnose(self, run_id, candidate_id):
        """What is wrong with one candidate, the physics behind it, and concrete fixes."""
        from .diagnosis import diagnose
        with self.store.lock:
            run = self.store.get(run_id, "run")
            candidate = self._owned(run_id, candidate_id, "candidate")
            cad = next((r for r in reversed(self.store.list(run_id, "cad_revision")) if r["candidate_id"] == candidate_id), None)
            project = next((p for p in reversed(self.store.list(run_id, "print_project")) if p.get("candidate_id") == candidate_id), None)
            return diagnose(candidate=candidate, model=self.latest_model(run_id), adopted_model=self.last_adopted_model(run_id),
                            protocol=Protocol(**run["protocol"]), observations=self._observations(run_id),
                            candidates=self.store.list(run_id, "candidate"), cad=cad, project=project)

    def repair(self, run_id, candidate_id, *, live=False):
        """Specialists propose and agree on a repaired design; agreement creates a new linked candidate."""
        from .agents import propose_repair
        with self.store.atomic():
            run = self.store.get(run_id, "run")
            self._ensure_active(run)
            candidate = self._owned(run_id, candidate_id, "candidate")
            diagnosis = self.diagnose(run_id, candidate_id)
            model = self.latest_model(run_id)
            budget = self._budget(run)
            observation_ids = [o.id for o in self._observations(run_id)
                               if o.candidate_id == candidate_id and o.purpose not in ("reserved", "acceptance")]
            context = {"candidate": {k: candidate[k] for k in ("id", "geometry", "reference")}, "diagnosis": diagnosis,
                       "model_parameters": model["parameters"], "protocol": run["protocol"],
                       "budget": {k: budget[k] for k in ("cycles_remaining", "designs_remaining", "tokens_remaining")},
                       "existing_geometries": [c["geometry"] for c in self.store.list(run_id, "candidate")],
                       "evidence_ids": [candidate_id, model["id"], *observation_ids]}
            started = time.monotonic()
            try:
                result = propose_repair(context, provider=self._live_providers(run.get("team")) if live else None)
            except ValueError as exc:
                result = {"id": identifier("repair"), "candidate_id": candidate_id, "status": "provider_configuration_error",
                          "selected": None, "proposals": [], "reviews": [], "usage": {}, "mode": "llm", "policy": "three_provider_repair",
                          "transcript": [{"type": "decision_error", "reason": str(exc)[:300]}]}
            result["elapsed_s"] = time.monotonic() - started
            result["diagnosis_status"] = diagnosis["status"]
            new_candidate = None
            if result["selected"]:
                selected = result["selected"]
                new_candidate = self._candidate(run, Geometry(**selected["geometry"]), model["id"], candidate_id,
                                                f"repair:{selected['agent']}")
                result["new_candidate_id"] = new_candidate["id"]
            self.store.put("repair", run_id, result)
            self.store.event(run_id, "repair_closed", {"repair_id": result["id"], "candidate_id": candidate_id,
                                                       "status": result["status"], "new_candidate_id": result.get("new_candidate_id")})
            return {"repair": result, "candidate": new_candidate,
                    "diagnosis_after": self.diagnose(run_id, new_candidate["id"]) if new_candidate else None}

    def print_review(self, run_id, project_id, *, live=False):
        """Specialists review CAD agreement and PreForm checks before a candidate may be queued."""
        from .agents import review_print
        with self.store.atomic():
            run = self.store.get(run_id, "run")
            project = self.store.get(project_id, "print_project")
            if project["run_id"] != run_id or not project.get("candidate_id"):
                raise ValueError("Print project does not belong to a candidate in this study")
            candidate = self._owned(run_id, project["candidate_id"], "candidate")
            cad = next((r for r in reversed(self.store.list(run_id, "cad_revision")) if r["asset_id"] == project["asset_id"]), None)
            if cad is None:
                raise ValueError("Check the exported CAD (Onshape or local fixture) before the print review")
            protocol = Protocol(**run["protocol"])
            model = self.latest_model(run_id)
            budget = self._budget(run)
            context = {
                "candidate": {k: candidate[k] for k in ("id", "geometry", "reference", "volume_m3")},
                "cad": {k: cad[k] for k in ("id", "candidate_id", "asset_id", "source", "check", "mesh", "onshape")},
                "print_project": {"id": project["id"], "candidate_id": project["candidate_id"], "asset_id": project["asset_id"],
                    "grade": project["validation"]["grade"], "issues": project["validation"]["issues"],
                    "warnings": project["validation"]["warnings"], "profile": project["profile"],
                    "estimate": project.get("estimate"), "material_usage": project.get("material_usage")},
                "prediction": mechanics.predict_deflection(Geometry(**candidate["geometry"]), ModelParameters(**model["parameters"]),
                                                           protocol.proof_force_n, protocol.proof_position_m),
                "model": {"id": model["id"], "fit_status": model["fit"]["status"], "parameters": model["parameters"]},
                "protocol": {k: run["protocol"].get(k) for k in ("proof_force_n", "proof_position_m", "max_deflection_m", "max_uncertainty_m",
                                                                 "diagnostic_positions_m", "reserved_position_m", "max_volume_m3", "max_root_stress_pa")},
                "requirements": mechanics.requirement_checks(Geometry(**candidate["geometry"]), ModelParameters(**model["parameters"]), protocol),
                "budget": {k: budget[k] for k in ("cycles_remaining", "designs_remaining", "tokens_remaining")},
                "evidence_ids": [candidate["id"], cad["id"], project["id"], model["id"]]}
            context["diagnosis"] = [{k: i[k] for k in ("code", "severity", "owner", "title", "detail")}
                                    for i in self.diagnose(run_id, candidate["id"])["issues"]]
            started = time.monotonic()
            try:
                review = review_print(context, provider=self._live_providers(run.get("team")) if live else None)
            except ValueError as exc:
                # Configuration problems occur before any paid call.
                review = {"id": identifier("print_review"), "candidate_id": candidate["id"], "project_id": project_id,
                          "cad_revision_id": cad["id"], "status": "provider_configuration_error", "votes": [], "gate_errors": [],
                          "usage": {}, "transcript": [{"type": "decision_error", "reason": str(exc)[:300]}],
                          "policy": "three_provider_print_review", "mode": "llm"}
            review["elapsed_s"] = time.monotonic() - started
            self.store.put("print_review", run_id, review)
            self.store.event(run_id, "print_review_closed", {"review_id": review["id"], "project_id": project_id, "status": review["status"]})
            return review

    def reserved_check(self, run_id):
        with self.store.atomic():
            run = self.store.get(run_id, "run")
            self._ensure_active(run)
            if any(e["purpose"] == "reserved" for e in self.store.list(run_id, "experiment")):
                raise ValueError("This reserved condition has already been used")
            model = self.fit(run_id)
            if not model["adopted"]:
                raise ValueError("An identifiable model is required before the reserved prediction")
            candidate = self.store.list(run_id, "candidate")[0]
            protocol = Protocol(**run["protocol"])
            exp = self._queue(run, candidate, protocol.proof_force_n, protocol.reserved_position_m, "reserved", self._specimen(run, candidate), "Precommitted held-out prediction; never used for fitting")
            return {"experiment": exp, "measurement": self._simulate(run, exp) if run["mode"] == "simulated" else None}

    def nominate(self, run_id, candidate_id=None):
        with self.store.atomic():
            run = self.store.get(run_id, "run")
            self._ensure_active(run)
            if self.pending(run_id):
                raise ValueError("Complete the pending measurement before nomination")
            candidates = self.store.list(run_id, "candidate")
            if candidate_id is None:
                protocol = Protocol(**run["protocol"])
                passed = {o.candidate_id for o in self._observations(run_id) if measurement.classify_observation(o, protocol)["status"] in ("simulated_pass", "physical_pass")}
                candidate = min((c for c in candidates if c["id"] in passed), key=lambda c: c["volume_m3"], default=candidates[0])
            else:
                candidate = self._owned(run_id, candidate_id, "candidate")
            return self.store.put("nomination", run_id, {"id": identifier("nomination"), "candidate_id": candidate["id"], "created_at": utc_now()})

    def acceptance_cycle(self, run_id):
        with self.store.atomic():
            run = self.store.get(run_id, "run")
            nominations = self.store.list(run_id, "nomination")
            if not nominations:
                raise ValueError("Nominate and freeze a final design first")
            if self.pending(run_id):
                return {"status": "awaiting_measurement", "experiment": self.pending(run_id)}
            count = sum(e["purpose"] == "acceptance" for e in self.store.list(run_id, "experiment"))
            protocol = Protocol(**run["protocol"])
            if count >= protocol.final_copies:
                raise ValueError("All final independent copies have already been tested")
            candidate = self._owned(run_id, nominations[0]["candidate_id"], "candidate")
            specimen_id = f"final-{count + 1}-{candidate['id']}"
            if run["mode"] == "simulated":
                self.fabricate(run_id, candidate["id"], specimen_id, notes="Independent simulated final copy")
            exp = self._queue(run, candidate, protocol.proof_force_n, protocol.proof_position_m, "acceptance", specimen_id, "Independent copy of frozen final design")
            return {"experiment": exp, "measurement": self._simulate(run, exp) if run["mode"] == "simulated" else None}

    def demo(self, run_id, live=False):
        """Run a simulated study to completion. live=True spends model calls (comparisons only)."""
        run = self.store.get(run_id, "run")
        if run["mode"] != "simulated":
            raise ValueError("Automated demo only executes simulated observations")
        # LLM agents may fail to agree once and recover; a fixed offline rule would just repeat.
        stalls, allowed = 0, (3 if live else 1)
        for _ in range(run["protocol"]["search_cycles"] * (2 if live else 1)):
            if self._budget(run)["cycles_remaining"] <= 0:
                break
            result = self.step(run_id, live=live)
            if result["status"] != "executed":
                stalls += 1
                if stalls >= allowed or result["status"] in ("provider_error", "token_budget_exhausted", "provider_configuration_error", "decision_runtime_error"):
                    break
                continue
            stalls = 0
            # Refit first: the newest observation can make a previously identifiable fit inadequate.
            if (not any(e["purpose"] == "reserved" for e in self.store.list(run_id, "experiment"))
                    and self._budget(run)["cycles_remaining"] > 0 and self.fit(run_id)["adopted"]):
                self.reserved_check(run_id)
        self.nominate(run_id)
        for _ in range(run["protocol"]["final_copies"]):
            self.acceptance_cycle(run_id)
        return self.state(run_id)

    def state(self, run_id):
        with self.store.lock:
            run = self.store.get(run_id, "run")
            protocol = Protocol(**run["protocol"])
            observations = self._observations(run_id)
            return {"run": run, "budget": self._budget(run), "candidates": self.store.list(run_id, "candidate"),
                "model": self.latest_model(run_id), "last_adopted_model": self.last_adopted_model(run_id), "models": self.store.list(run_id, "model"),
                "observations": [{**o.model_dump(mode="json"), "assessment": measurement.classify_observation(o, protocol)} for o in observations],
                "pending_experiment": self.pending(run_id), "experiments": self.store.list(run_id, "experiment"),
                "fabrications": self.store.list(run_id, "fabrication"), "decisions": self.store.list(run_id, "decision"),
                "print_projects": self.store.list(run_id, "print_project"), "print_receipts": self.store.list(run_id, "print_receipt"),
                "print_assets": self.store.list(run_id, "print_asset"), "print_submissions": self.store.list(run_id, "print_submission"),
                "cad_revisions": self.store.list(run_id, "cad_revision"), "print_reviews": self.store.list(run_id, "print_review"),
                "repairs": self.store.list(run_id, "repair"),
                "nominations": self.store.list(run_id, "nomination"), "acceptance": measurement.acceptance_summary(observations, protocol),
                "events": self.store.events(run_id), "log_verified": self.store.verify()}
