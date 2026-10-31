r"""Run one design session.

  .venv\Scripts\python.exe run.py --mode collective --brief benchmark\briefs\a_pi5_standard.yaml --budget 12 --seed 0 [--mock]

Modes (same model, tools, brief, budget and the SAME naive starting spec):
  one_shot    the design agent edits v0 once from the brief; no checks
  generalist  one agent with every check, data source and spec key
  collective  design + assembly + manufacturing + fit agents on a shared board (needs, joint edits, votes)
Local checks run on every version. Real PreForm runs only when the Manufacturing agent (or the generalist)
asks (limited per run) and on every final design (cached by spec). Passing everything does not end a run:
agents keep improving the quality score until the budget runs out.
"""
import argparse
import json
import random
import shutil
import time
from pathlib import Path

import yaml

import copy

import jsonpatch

import agents
import families
from cad.common import ROOT, load_config, print_settings, write_print_info
from cad.components import load_bundle
from checks.fit_rules import rules as make_rules
from core import protocol
from core.board import Board
from core.evaluate import evaluate, real_preform
from core.llm import LLM
from core.report import write_report


def run(mode, brief_path, budget=None, seed=0, mock=False, clearances=None, out_root=None, mock_preform=None,
        cfg=None, quiet=False, model=None, final_real_preform=True):
    cfg = cfg or load_config()
    if model:
        cfg["llm"]["model"] = model
    budget = budget if budget is not None else cfg["run"]["default_budget"]
    brief = yaml.safe_load(Path(brief_path).read_text(encoding="utf-8"))
    rules = make_rules(cfg, clearances or cfg.get("clearance_source", "generic"))
    fam = families.for_brief(brief)
    protocol.configure(fam)
    comp = load_bundle(brief["bundle"]) if "bundle" in brief else None

    def apply_patch(sp, patch):
        return fam.validate(jsonpatch.apply_patch(copy.deepcopy(sp), patch))

    def quality(sp, ev, rules_, cfg_, brief_, real_preform=None):
        return fam.quality(sp, ev, rules_, cfg_, brief_, real_preform)
    tag = f"__{cfg['llm']['model']}" if model else ""
    name = (f"{brief['name']}__{mode}__{rules['source']}__s{seed}{'__mock' if mock else ''}{tag}"
            f"{'__EXPERIMENTAL' if fam.experimental else ''}")
    out = Path(out_root or ROOT / cfg["run"]["out_dir"]) / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    say = (lambda *a: None) if quiet else (lambda *a: print(*a, flush=True))

    llm = LLM(cfg, mock=mock)
    board = Board(out / "board.sqlite")
    run_id = board.start_run(mode, brief["name"], seed, budget, llm.model, mock)
    rng = random.Random(seed)
    bud = protocol.Budget(budget, cfg["llm"]["cost_cap_usd"])
    t0 = time.time()

    spec = fam.validate(fam.default_spec(brief, rules, cfg, start=cfg["run"]["start_spec"]))
    current = board.add_version(run_id, spec, "baseline",
                                f"v0 ({cfg['run']['start_spec']}) from component data + clearance rules", status="baseline")
    for n in rules["notes"]:
        board.post(run_id, "fit" if mode == "collective" else "system", "note", n, current)
    state = {"spec": spec, "brief": brief, "comp": comp, "rules": rules, "results": None, "needs": [],
             "rejected": set(), "pictures": {}, "errors": {}, "outcomes": [], "quality": None,
             "preform_left": cfg["preform_requests_per_run"] if mode != "one_shot" else 0, "real_preform_last": None,
             "family": fam}
    if fam.experimental:
        board.post(run_id, "system", "note", f"EXPERIMENTAL part family '{fam.name}': only general checks run", current)
    evs = {}

    def remember(who, patch, why):
        state["errors"].setdefault(who, []).append({"patch": patch, "why": why[:300]})

    def store(version_id, ev, q):
        evs[version_id] = ev
        for cname, r in ev["results"].items():
            board.add_check(run_id, version_id, cname, protocol.CHECK_OWNERS[cname], r)
        board.add_check(run_id, version_id, "quality", "all", {"passed": True, "failures": [], "metrics": q})
        for kind, p in {**ev.get("pictures", {}), **ev.get("paths", {})}.items():
            board.add_artifact(run_id, version_id, kind, p)

    def route_needs(vid):
        """Protocol rule: a failure whose suggested fix is in another agent's keys becomes a need from the
        check's owner to that key owner (once per failure). Agents still accept or decline it."""
        if mode != "collective":
            return
        from agents.mock_policy import with_hints
        hinted = with_hints(state["results"], state, cfg)
        for check, r in hinted.items():
            frm = protocol.CHECK_OWNERS[check]
            for f in r.get("failures", []):
                h = f.get("hint")
                if not h or frm in h["owner"]:
                    continue
                to = h["owner"][0]
                pk = json.dumps(h["patch"], sort_keys=True)
                if any((n["failure"] == f["msg"] or json.dumps(n["suggest_patch"], sort_keys=True) == pk)
                       and n["to"] == to and n["status"] in ("open", "tried") for n in state["needs"]):
                    continue
                try:
                    if apply_patch(state["spec"], h["patch"]) == state["spec"]:
                        continue
                except Exception:
                    continue
                mid = board.post(run_id, frm, "need", f"to {to} (routed by protocol): {f['msg']} | suggest "
                                 f"{json.dumps(h['patch'])}", vid)
                state["needs"].append({"id": mid, "to": to, "frm": frm, "failure": f["msg"],
                                       "amount_mm": f.get("amount_mm", 0), "suggest_patch": h["patch"],
                                       "reason": "routed by protocol: fix is in your keys", "status": "open"})

    def measure(sp):
        ev = evaluate(sp, brief, cfg, rules, mock_preform=True)
        return ev, quality(sp, ev, rules, cfg, brief)

    stop = None
    if mode == "one_shot":
        designer = agents.make("design", llm, cfg, mode, board, run_id, rng)
        reply = designer.one_shot(state)
        if reply.get("action") == "propose" and reply.get("patch"):
            try:
                new = apply_patch(spec, reply["patch"])
                board.post(run_id, "design", "propose", f"{reply.get('reason', '')} | patch {json.dumps(reply['patch'])}",
                           current)
                current = board.add_version(run_id, new, "design", reply.get("reason", ""), current, reply["patch"],
                                            status="accepted")
                board.decide(run_id, current, "accepted", "one-shot: no checks")
                spec = new
            except Exception as e:  # invalid patch: the one shot is spent
                board.post(run_id, "design", "note", f"invalid patch rejected: {e}", current)
        state["spec"] = spec
        stop = "one-shot done"
    else:
        ev, q = measure(spec)
        bud.spend()
        store(current, ev, q)
        state.update(results=ev["results"], pictures=ev["pictures"], quality=q)
        route_needs(current)
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
                state["budget_left"] = bud.left
                reply = agent.act(state)
                act = reply.get("action")
                extra = reply.get("needs") if isinstance(reply.get("needs"), list) else []
                if act == "need":
                    extra = [reply] + extra
                for nd in extra if mode == "collective" else []:
                    if not isinstance(nd, dict):
                        continue
                    to = nd.get("to")
                    if to not in members or to == who:
                        remember(who, nd.get("suggest_patch"), f"need to unknown/self agent {to}")
                        continue
                    amount = max([f.get("amount_mm", 0) for r in state["results"].values()
                                  for f in r.get("failures", []) if f["msg"] in str(nd.get("failure", ""))] or [0])
                    mid = board.post(run_id, who, "need", f"to {to}: {nd.get('failure', '')} | suggest "
                                     f"{json.dumps(nd.get('suggest_patch'))} | {nd.get('reason', '')}", current)
                    state["needs"].append({"id": mid, "to": to, "frm": who, "failure": str(nd.get("failure", ""))[:300],
                                           "amount_mm": amount, "suggest_patch": nd.get("suggest_patch") or [],
                                           "reason": str(nd.get("reason", ""))[:300], "status": "open"})
                if act == "need":
                    continue
                if act == "decline":
                    n = next((x for x in state["needs"] if x["id"] == reply.get("need_id") and x["to"] == who), None)
                    if n:
                        n["status"] = "declined"
                        board.post(run_id, who, "decline", f"need #{n['id']}: {reply.get('reason', '')}", current)
                    continue
                if act == "run_preform":
                    if who in ("manufacturing", "generalist") and state["preform_left"] > 0:
                        state["preform_left"] -= 1
                        rp = real_preform(evs[current], cfg)
                        state["real_preform_last"] = {"version": current, "mocked": rp.get("mocked"),
                                                      "failures": rp["failures"], "metrics": rp.get("metrics")}
                        board.add_check(run_id, current, "preform_real", who, rp)
                        state.setdefault("real_by_version", {})[current] = rp
                        board.post(run_id, who, "note", f"PreForm on v{current}: {'MOCK' if rp.get('mocked') else 'real'}, "
                                   f"{len(rp['failures'])} warnings, {rp['metrics'].get('print_time_s')} s, "
                                   f"{rp['metrics'].get('volume_ml')} ml", current)
                    continue
                if act != "propose":
                    continue

                patch, reason = reply["patch"], reply.get("reason", "")
                key = json.dumps(patch, sort_keys=True)
                need = next((x for x in state["needs"] if x["id"] == reply.get("need_id") and x["to"] == who), None)
                try:
                    new_spec = apply_patch(state["spec"], patch)
                except Exception as e:
                    board.post(run_id, who, "note", f"invalid patch rejected without evaluation: {e}", current)
                    state["rejected"].add(key)
                    remember(who, patch, f"invalid patch: {e}")
                    continue
                if new_spec == state["spec"]:
                    board.post(run_id, who, "note", f"no-op patch (spec unchanged), not evaluated: {key[:200]}", current)
                    state["rejected"].add(key)
                    remember(who, patch, "no-op: the spec already has these values")
                    if need:
                        need["status"] = "done"
                    continue
                vid = board.add_version(run_id, new_spec, who, reason, current, patch)
                board.post(run_id, who, "propose", f"{reason} | patch {json.dumps(patch)}", vid)
                supports, objections = [who], []
                # joint edit: every owner of keys the proposer doesn't own must support it first
                owners = protocol.owners_needed(who, patch) if mode == "collective" else []
                blocked = None
                for o in owners:
                    v = members[o].vote(state, reply, None, None, None, joint=True)
                    (members[o].support if v["vote"] == "support" else members[o].object)(vid, f"joint edit: {v['reason']}")
                    if v["vote"] != "support":
                        blocked = f"joint edit: owner {o} objected: {v['reason']}"
                        break
                    supports.append(o)
                if blocked:
                    board.decide(run_id, vid, "rejected", blocked)
                    board.set_version_status(vid, "rejected")
                    state["rejected"].add(key)
                    remember(who, patch, blocked)
                    state["outcomes"].append({"version": vid, "by": who, "patch": patch, "decision": "rejected",
                                              "why": blocked})
                    say(f"  r{rnd} {who:13s} v{vid} rejected (joint, not evaluated)")
                    continue
                proposed = True
                ev, q = measure(new_spec)
                bud.spend()
                q_old = (state["quality"] or {}).get("score")
                parent_rp = state.get("real_by_version", {}).get(current)
                if parent_rp and not parent_rp.get("mocked") and state["preform_left"] > 0:
                    # real PreForm already ran on the current design: compare both with real warnings included
                    state["preform_left"] -= 1
                    rp_new = real_preform(ev, cfg)
                    board.add_check(run_id, vid, "preform_real", "protocol", rp_new)
                    state.setdefault("real_by_version", {})[vid] = rp_new
                    if not rp_new.get("mocked"):
                        q = quality(new_spec, ev, rules, cfg, brief, real_preform=rp_new)
                        q_old = quality(state["spec"], evs[current], rules, cfg, brief, real_preform=parent_rp)["score"]
                store(vid, ev, q)
                old, new = state["results"], ev["results"]
                bt, at = protocol.total_failures(old), protocol.total_failures(new)
                bd = protocol.failure_diff(old, new)
                board.post(run_id, "system", "breakdown", json.dumps(bd), vid)
                if protocol.worsened(old, new) and not protocol.all_pass(old):
                    for owner, cname, nums in protocol.auto_objectors(old, new, mode):
                        if owner != who and owner in members and owner not in objections:
                            members[owner].object(vid, f"{nums}; broke: {'; '.join(bd['new'][:3]) or 'none'}")
                            objections.append(owner)
                    for other in order:
                        if other in supports or other in objections:
                            continue
                        v = members[other].vote(state, reply, bd, bt, at)
                        if v["vote"] == "support":
                            members[other].support(vid, v["reason"])
                            supports.append(other)
                        else:
                            members[other].object(vid, v["reason"])
                            objections.append(other)
                decision, why = protocol.decide(old, new, supports, objections, q_old, q["score"])
                board.decide(run_id, vid, decision, why)
                board.set_version_status(vid, decision)
                state["outcomes"].append({"version": vid, "by": who, "patch": patch, "decision": decision, "why": why,
                                          "fixed": bd["fixed"][:5], "broke": bd["new"][:5], "quality": q["score"]})
                say(f"  r{rnd} {who:13s} v{vid} {decision:8s} failures {bt}->{at} q {q['score']}  {reason[:60]}")
                if need:
                    need["status"] = "done" if decision == "accepted" else "tried"
                if decision == "accepted":
                    current = vid
                    state.update(spec=new_spec, results=new, pictures=ev["pictures"], quality=q)
                    route_needs(current)
                else:
                    state["rejected"].add(key)
                    remember(who, patch, f"rejected after evaluation: {why}")
            if stop:
                break
            idle = 0 if proposed else idle + 1
        stop = stop or bud.stop_reason(state["results"], board.usage_total(run_id)["cost_usd"], idle) or "max rounds"

    # ---- final measurement: local checks + REAL PreForm (cached by spec), not counted against the budget ----
    final_spec = state["spec"]
    final, _ = measure(final_spec)
    rp = real_preform(final, cfg) if final_real_preform else final["results"]["preform"]
    final_results = dict(final["results"], preform=rp)
    fq = quality(final_spec, final, rules, cfg, brief, real_preform=None if rp.get("mocked") else rp)
    if mode == "one_shot":
        store(current, final, fq)
    board.add_check(run_id, current, "preform_final", "manufacturing", rp)
    board.add_check(run_id, current, "quality_final", "all", {"passed": True, "failures": [], "metrics": fq})
    for src in list(Path(final["dir"]).glob("*.stl")) + list(Path(final["dir"]).glob("*.form")) + \
            list(Path(final["dir"]).glob("*.png")):
        shutil.copy(src, out / src.name)
    write_print_info(out, cfg, {"parts": sorted(Path(p).name for p in final.get("paths", {}).values()),
                                "clearance_source": rules["source"], "family": fam.name,
                                "experimental": fam.experimental})
    (out / "final_spec.json").write_text(json.dumps(final_spec, indent=1, sort_keys=True), encoding="utf-8")
    (out / "final_checks.json").write_text(json.dumps(final_results, indent=1, default=str), encoding="utf-8")
    usage = board.usage_total(run_id)
    summary = {"run": name, "family": fam.name, "experimental": fam.experimental,
               "print_settings": print_settings(cfg), "mode": mode, "brief": brief["name"], "seed": seed,
               "clearances": rules["source"], "clearance_fallbacks": rules["fallbacks"], "model": llm.model,
               "mocked_llm": mock, "mocked_preform": bool(rp.get("mocked", True)), "budget": budget,
               "start_spec": cfg["run"]["start_spec"],
               "evaluations_used": bud.used, "preform_requests_used": cfg["preform_requests_per_run"] - state["preform_left"]
               if mode != "one_shot" else 0, "stop_reason": stop, "seconds": round(time.time() - t0, 1),
               "final_failures": {n: len(r["failures"]) for n, r in final_results.items()},
               "final_passed": all(r["passed"] for r in final_results.values()),
               "quality": fq, "usage": usage, "final_version": current}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    board.end_run(run_id, "done", stop)
    write_report(board, run_id, out, summary)
    say(f"{name}: {stop}; passed={summary['final_passed']} quality={fq['score']} final failures "
        f"{summary['final_failures']}; {bud.used} evaluations; ${usage['cost_usd']:.4f}")
    stls = " ".join(str(p) for p in sorted(out.glob("*.stl")))
    say(f"PRINT READY{' (EXPERIMENTAL)' if fam.experimental else ''}: {stls}" if stls else "no printable parts")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["one_shot", "generalist", "collective"])
    ap.add_argument("--brief", required=True)
    ap.add_argument("--budget", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mock", action="store_true", help="rule-based fake model, zero API cost")
    ap.add_argument("--clearances", choices=["generic", "measured"], default=None)
    ap.add_argument("--model", default=None, help="override llm.model (same model for every agent)")
    ap.add_argument("--no-final-preform", action="store_true")
    a = ap.parse_args()
    run(a.mode, a.brief, a.budget, a.seed, a.mock, a.clearances, model=a.model,
        final_real_preform=not a.no_final_preform)


if __name__ == "__main__":
    main()
