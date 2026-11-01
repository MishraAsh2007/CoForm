# Shares results and messages, collects proposals, and chooses the next queued experiment.
#
# So far: the experiment budget and the shared experiment pipeline used by agents
# and baselines alike. The proposal queue and agent round loop come with the
# agents (milestone M8).
import config
from schemas import CandidateRecord, GeometryParams, PrintSettings, Quantity


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Counts evaluations per kind (config.BUDGETS keys) for one run.

    Every evaluation, by an agent or a baseline method, must go through the same
    Budget so that method comparisons use identical computational budgets.
    """

    def __init__(self, limits=None):
        self.limits = dict(config.BUDGETS if limits is None else limits)
        self.used = {kind: 0 for kind in self.limits}

    def can_spend(self, kind, n=1):
        return kind in self.limits and self.used[kind] + n <= self.limits[kind]

    def spend(self, kind, n=1):
        if not self.can_spend(kind, n):
            raise BudgetExceeded(f"{kind}: used {self.used.get(kind)} of {self.limits.get(kind)}")
        self.used[kind] += n

    def remaining(self):
        return {kind: self.limits[kind] - self.used[kind] for kind in self.limits}


def _has_selection_grade_structure(record: CandidateRecord):
    return any(r.tip_deflection.counts_for_selection and r.max_stress.counts_for_selection
               for r in record.structural)


def run_experiment(params: GeometryParams, run_id, budget: Budget, accuracy="screening",
                   proposed_by="baseline", parent_id=None, request_level=None):
    """Build a candidate's geometry, run the Mechanic's evaluation, and save its record.

    Re-running a candidate reuses its geometry and any selection-grade results.
    """
    import bracket
    import store
    import structural_evaluator

    cid = params.candidate_id()
    record = store.load_record(run_id, cid) or CandidateRecord(
        candidate_id=cid, params=params, proposed_by=proposed_by, parent_id=parent_id)
    stl, step = store.geometry_paths(run_id, cid)
    if not step.exists() or record.volume is None:
        built = bracket.build_bracket(params.plate_thickness, params.rib_height,
                                      params.rib_thickness, stl, step)
        record.volume = Quantity(value=built["volume_mm3"], unit="mm^3", source="analytical",
                                 status="completed")
        record.mass = Quantity(value=built["volume_mm3"] * config.DENSITY_G_PER_MM3, unit="g",
                               source="analytical", status="completed")
        record.files.update(stl=str(stl), step=str(step))

    if _has_selection_grade_structure(record) and request_level != 2:
        record.decisions.append("evaluation skipped: selection-grade structural results exist")
    else:
        evaluation = structural_evaluator.evaluate(params, step, accuracy=accuracy, budget=budget,
                                                   request_level=request_level)
        record.structural.extend(evaluation.results)
        record.decisions.extend(evaluation.decisions)
        # Feedback loop: every converged FEA result recalibrates L1 for later candidates.
        for result in evaluation.results:
            if result.level == 2 and structural_evaluator.record_fea(params, result):
                record.decisions.append("converged L2 added to the L1 calibration (refitted)")
    store.save_record(run_id, record)
    return record


def run_print_prep(record: CandidateRecord, run_id, budget: Budget, preform,
                   print_settings: PrintSettings | None = None):
    """Prepare a candidate in PreForm (charged to budget "preform_prep") and save the result."""
    import preform_client
    import store

    budget.spend("preform_prep")
    settings = print_settings or PrintSettings()
    index = len(record.printability)
    form = store.candidate_dir(run_id, record.candidate_id) / f"print{index}.form"
    result = preform_client.prepare_print(preform, record.files["stl"], settings, form_path=form,
                                          name=record.candidate_id)
    record.printability.append(result)
    store.save_record(run_id, record)
    return result


def run_agents(run_id, budget: Budget, rounds=3, mode="collective", preform=None, client=None,
               log=print):
    """Run the agents in rounds (Fabrication, Mechanic, Measurement; or one "single" agent).

    Each agent turn sees a fresh digest of the shared state, acts through its tools,
    and posts its final text to the board. Stops after `rounds`, when the llm_calls
    budget runs out, or after a round in which no agent took a successful action.
    Returns the final SelectionReport (also saved as runs/<run_id>/selection.json).
    """
    import agents
    import llm_client
    import selection
    import store
    from schemas import Message

    ctx = agents.Context(run_id, budget, preform=preform, client=client)
    specs = agents.build_agents(ctx, mode)
    for round_number in range(1, rounds + 1):
        ctx.round = round_number
        actions = 0
        for spec in specs:
            if not budget.can_spend("llm_calls"):
                break
            turn = llm_client.run_turn(spec.system, agents.digest(ctx, spec.name), spec.tools, budget,
                                       client=client)
            actions += sum(1 for call in turn.tool_calls if call["ok"])
            text = turn.text or "(no message) " + "; ".join(turn.notes)
            store.append_message(run_id, Message(agent=spec.name, round=round_number, text=text,
                                                 cites=sorted(set(agents.CANDIDATE_ID.findall(text)))))
            store.append_turn(run_id, {"round": round_number, "agent": spec.name,
                                       "api_calls": turn.api_calls, "stop_reason": turn.stop_reason,
                                       "input_tokens": turn.input_tokens,
                                       "output_tokens": turn.output_tokens, "notes": turn.notes,
                                       "tool_calls": turn.tool_calls, "text": turn.text})
            log(f"round {round_number} {spec.name}: {len(turn.tool_calls)} tool calls "
                f"({turn.api_calls} API calls); budget left {budget.remaining()}")
        if not budget.can_spend("llm_calls"):
            log("stopping: llm_calls budget exhausted")
            break
        if actions == 0:
            log("stopping: no agent took a successful action this round")
            break

    report = selection.select(store.list_records(run_id))
    path = store.RUNS_DIR / str(run_id) / "selection.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
