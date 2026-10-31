r"""Run one design session.

  .venv\Scripts\python.exe run.py --mode collective --brief benchmark\briefs\a_pi5_standard.yaml --budget 8 --seed 0 --mock

Modes (same model, tools, brief and budget):
  one_shot    the design agent edits v0 once from the brief; no checks
  generalist  one agent with every check and data source
  collective  design + assembly + manufacturing + fit agents on a shared board
--clearances generic|measured picks the clearance rules (measured falls back to generic, and says so, until
data/fit_test_results.yaml is filled in). Outputs go to results/runs/<run name>/.
"""
import argparse
import json
import random
import shutil
import time
from pathlib import Path

import yaml

import agents
from cad.common import ROOT, load_config, print_settings, write_print_info
from cad.components import load_bundle
from cad.spec import apply_patch, default_spec, validate
from checks.fit_rules import rules as make_rules
from core import protocol
from core.board import Board
from core.evaluate import evaluate
from core.llm import LLM
from core.report import write_report


def run(mode, brief_path, budget=None, seed=0, mock=False, clearances=None, out_root=None, mock_preform=None,
        cfg=None, quiet=False, model=None):
    cfg = cfg or load_config()
    if model:
        cfg["llm"]["model"] = model
    budget = budget if budget is not None else cfg["run"]["default_budget"]
    mock_preform = mock if mock_preform is None else mock_preform
    brief = yaml.safe_load(Path(brief_path).read_text(encoding="utf-8"))
    rules = make_rules(cfg, clearances or cfg.get("clearance_source", "generic"))
    comp = load_bundle(brief["bundle"])
    name = f"{brief['name']}__{mode}__{rules['source']}__s{seed}{'__mock' if mock else ''}"
    out = Path(out_root or ROOT / cfg["run"]["out_dir"]) / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    say = (lambda *a: None) if quiet else print

    llm = LLM(cfg, mock=mock)
    board = Board(out / "board.sqlite")
    run_id = board.start_run(mode, brief["name"], seed, budget, llm.model, mock)
    rng = random.Random(seed)
    bud = protocol.Budget(budget, cfg["llm"]["cost_cap_usd"])
    t0 = time.time()

    spec = validate(default_spec(brief["bundle"], rules, cfg, start=cfg["run"]["start_spec"]))
    current = board.add_version(run_id, spec, "baseline", f"v0 ({cfg['run']['start_spec']}) from component data + clearance rules",
                                status="baseline")
    for n in rules["notes"]:
        board.post(run_id, "fit" if mode == "collective" else "system", "note", n, current)
    state = {"spec": spec, "brief": brief, "comp": comp, "rules": rules, "results": None, "requests": [],
             "rejected": set(), "pictures": {}, "errors": {}}

    def remember(who, patch, why):
        state["errors"].setdefault(who, []).append({"patch": patch, "why": why[:300]})

    def store(version_id, ev):
        for cname, r in ev["results"].items():
            board.add_check(run_id, version_id, cname, protocol.CHECK_OWNERS[cname], r)
        for kind, p in {**ev.get("pictures", {}), **ev.get("paths", {})}.items():
            board.add_artifact(run_id, version_id, kind, p)

    stop = None
    if mode == "one_shot":
        designer = agents.make("design", llm, cfg, mode, board, run_id, rng)
        reply = designer.one_shot(state)
        if reply.get("action") == "propose" and reply.get("patch"):
            try:
                new = apply_patch(spec, reply["patch"])
                designer.propose_edit(current, reply["patch"], reply.get("reason", ""))
                current = board.add_version(run_id, new, "design", reply.get("reason", ""), current, reply["patch"],
                                            status="accepted")
                board.decide(run_id, current, "accepted", "one-shot: no checks")
                spec = new
            except Exception as e:  # invalid patch: the one shot is spent
                board.post(run_id, "design", "note", f"invalid patch rejected: {e}", current)
        stop = "one-shot done"
    else:
        ev = evaluate(spec, brief, cfg, rules, mock_preform)
        bud.spend()
        store(current, ev)
        state.update(results=ev["results"], pictures=ev["pictures"])
        order = protocol.turn_order(mode, seed)
        members = {n: agents.make(n, llm, cfg, mode, board, run_id, rng) for n in order}
        idle = 0
        for rnd in range(cfg["run"]["max_rounds"]):
            proposed = False
            for who in order:
                stop = bud.stop_reason(state["results"], board.usage_total(run_id)["cost_usd"], idle)
                if stop:
                    break
                agent = members[who]
                reply = agent.act(state)
                if reply.get("action") == "note":
                    patch = reply.get("suggest_patch") or []
                    board.post(run_id, who, "note", reply.get("text", ""), current)
                    if patch:
                        state["requests"].append({"author": who, "text": reply.get("text", ""), "patch": patch})
                    continue
                if reply.get("action") != "propose":
                    continue
                patch, reason = reply["patch"], reply.get("reason", "")
                key = json.dumps(patch, sort_keys=True)
                if agent.propose_edit(current, patch, reason) is None:
                    state["rejected"].add(key)
                    remember(who, patch, f"ownership: you may not edit {protocol.forbidden_keys(who, patch)}; "
                                         f"post a note with suggest_patch for the owner instead")
                    continue
                try:
                    new_spec = apply_patch(state["spec"], patch)
                except Exception as e:
                    board.post(run_id, who, "note", f"invalid patch rejected without evaluation: {e}", current)
                    state["rejected"].add(key)
                    remember(who, patch, f"invalid patch: {e}")
                    continue
                proposed = True
                vid = board.add_version(run_id, new_spec, who, reason, current, patch)
                ev = evaluate(new_spec, brief, cfg, rules, mock_preform)
                bud.spend()
                store(vid, ev)
                old, new = state["results"], ev["results"]
                bt, at = protocol.total_failures(old), protocol.total_failures(new)
                supports, objections = [who], []
                if protocol.worsened(old, new):
                    for owner, cname, nums in protocol.auto_objectors(old, new, mode):
                        if owner != who and owner in members:
                            members[owner].object(vid, nums)
                            objections.append(owner)
                    for other in order:
                        if other in (who, *objections):
                            continue
                        v = members[other].vote(state, reply, old, new, bt, at)
                        if v["vote"] == "support":
                            members[other].support(vid, v["reason"])
                            supports.append(other)
                        else:
                            members[other].object(vid, v["reason"])
                            objections.append(other)
                decision, why = protocol.decide(old, new, supports, objections)
                board.decide(run_id, vid, decision, why)
                board.set_version_status(vid, decision)
                say(f"  r{rnd} {who:13s} v{vid} {decision:8s} failures {bt}->{at}  {reason[:70]}")
                if decision == "accepted":
                    current = vid
                    state.update(spec=new_spec, results=new, pictures=ev["pictures"])
                    state["requests"] = [r for r in state["requests"]
                                         if json.dumps(r["patch"], sort_keys=True) != key]
                else:
                    state["rejected"].add(key)
                    remember(who, patch, f"rejected after evaluation: {why}")
            if stop:
                break
            idle = 0 if proposed else idle + 1
        stop = stop or bud.stop_reason(state["results"], board.usage_total(run_id)["cost_usd"], idle) or "max rounds"

    # final measurement (not counted against the budget)
    final = evaluate(spec if mode == "one_shot" else state["spec"], brief, cfg, rules, mock_preform)
    if mode == "one_shot":
        store(current, final)
    final_spec = spec if mode == "one_shot" else state["spec"]
    for f in ("base.stl", "lid.stl", "assembled.png", "exploded.png", "base_top.png", "lid_iso.png"):
        src = Path(final["dir"]) / f
        if src.exists():
            shutil.copy(src, out / f)
    write_print_info(out, cfg, {"parts": ["base.stl", "lid.stl"], "clearance_source": rules["source"]})
    (out / "final_spec.json").write_text(json.dumps(final_spec, indent=1, sort_keys=True), encoding="utf-8")
    (out / "final_checks.json").write_text(json.dumps(final["results"], indent=1, default=str), encoding="utf-8")
    usage = board.usage_total(run_id)
    summary = {"run": name, "print_settings": print_settings(cfg), "mode": mode, "brief": brief["name"], "seed": seed, "clearances": rules["source"],
               "clearance_fallbacks": rules["fallbacks"], "model": llm.model, "mocked_llm": mock,
               "mocked_preform": final["results"]["preform"].get("mocked", True), "budget": budget,
               "evaluations_used": bud.used, "stop_reason": stop, "seconds": round(time.time() - t0, 1),
               "final_failures": {n: len(r["failures"]) for n, r in final["results"].items()},
               "final_passed": all(r["passed"] for r in final["results"].values()),
               "usage": usage, "final_version": current}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    board.end_run(run_id, "done", stop)
    write_report(board, run_id, out, summary)
    say(f"{name}: {stop}; final failures {summary['final_failures']}; {bud.used} evaluations; "
        f"${usage['cost_usd']:.4f}")
    say(f"PRINT READY: {out / 'base.stl'} {out / 'lid.stl'}")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["one_shot", "generalist", "collective"])
    ap.add_argument("--brief", required=True)
    ap.add_argument("--budget", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mock", action="store_true", help="rule-based fake model + mock PreForm, zero API cost")
    ap.add_argument("--clearances", choices=["generic", "measured"], default=None)
    ap.add_argument("--real-preform", action="store_true", help="with --mock: still try the real PreFormServer")
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    run(a.mode, a.brief, a.budget, a.seed, a.mock, a.clearances,
        mock_preform=(False if a.real_preform else None), model=a.model)


if __name__ == "__main__":
    main()
