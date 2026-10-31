"""Readable markdown log of a run: every edit, who proposed it, why, who objected (with numbers), outcome."""
import json


def _patch_lines(patch):
    out = []
    for op in patch:
        v = json.dumps(op.get("value")) if "value" in op else ""
        out.append(f"`{op['op']} {op['path']} {v}`".strip())
    return out


def write_report(board, run_id, out_dir, summary):
    run = board.run(run_id)
    lines = [f"# Run log: {summary['run']}", "",
             f"- mode **{run['mode']}**, brief **{run['brief']}**, seed {run['seed']}, budget {run['budget']} evaluations",
             f"- model `{run['model']}`{' (MOCK: rule-based fake model, no API calls)' if summary['mocked_llm'] else ''}",
             f"- print: **{summary['print_settings']['printer']}, {summary['print_settings']['material']}, "
             f"{summary['print_settings']['layer_thickness_mm']} mm layers** "
             f"({summary['print_settings']['machine_type']} / {summary['print_settings']['material_code']})",
             f"- clearances **{summary['clearances']}**"
             + (f" (fell back to generic for: {', '.join(summary['clearance_fallbacks'])})"
                if summary["clearance_fallbacks"] else ""),
             f"- PreForm: {'MOCK (not real PreForm results)' if summary['mocked_preform'] else 'real PreFormServer'}",
             f"- stopped: {summary['stop_reason']}; evaluations used {summary['evaluations_used']}; "
             f"{summary['seconds']} s; tokens in/out {summary['usage']['input_tokens']}/"
             f"{summary['usage']['output_tokens']}; est. cost ${summary['usage']['cost_usd']:.4f}",
             f"- final failures per check: {summary['final_failures']} -> "
             f"{'ALL PASS' if summary['final_passed'] else 'NOT all passing'}", ""]
    decisions = {d["version_id"]: d for d in board.decisions(run_id)}
    checks = {}
    for c in board.checks(run_id):
        checks.setdefault(c["version_id"], {})[c["name"]] = c
    lines += ["## Versions", ""]
    for v in board.versions(run_id):
        d = decisions.get(v["id"])
        status = d["decision"] if d else v["status"]
        lines.append(f"### v{v['id']} by {v['author']}: {status}")
        lines.append(f"Reason: {v['reason']}")
        if v["patch"]:
            lines.append("Patch: " + "; ".join(_patch_lines(v["patch"])))
        if v["id"] in checks:
            cs = checks[v["id"]]
            lines.append("Checks: " + ", ".join(f"{n} {'pass' if c['passed'] else str(c['failures']) + ' fail'}"
                                               + (" (MOCK)" if c["mocked"] else "") for n, c in cs.items()))
        msgs = [m for m in board.messages(run_id, v["id"]) if m["kind"] in ("object", "support", "note")]
        for m in msgs:
            lines.append(f"- {m['kind']} by **{m['author']}**: {m['text']}")
        if d:
            lines.append(f"Decision: **{d['decision']}** ({d['reason']})")
        lines.append("")
    final = checks.get(summary["final_version"], {})
    if final:
        lines += ["## Remaining failures in the final design", ""]
        for n, c in final.items():
            for f in c["details"].get("failures", []):
                lines.append(f"- {n}: {f['msg']}")
        lines.append("")
    (out_dir / "log.md").write_text("\n".join(lines), encoding="utf-8")
