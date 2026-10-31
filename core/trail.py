"""'Why this design' decision trail for a version, built only from the board log, in order:
failed checks (numbers) -> needs posted -> proposal + stated reason -> objections/supports -> breakdown -> decision.

ai_summary() asks the model for 2-3 plain sentences that may contain NO numbers of its own: it must use named
placeholders ({failures_before} ...) that code fills in from the logged data, and cite trail entries as [E3].
Any digit the model writes outside a placeholder or citation is removed and the summary is flagged.
"""
import json
import re

MAIN = ("assembly", "printability", "preform", "fit")


def _checks(board, vid):
    return {c["name"]: c for c in board.checks(1, vid)}


def trail(board, vid):
    """List of entries {id: 'E1', kind, who, text} for one version."""
    v = board.version(vid)
    if v is None:
        return [], {}
    parent = v["parent_id"]
    entries = []

    def add(kind, who, text):
        entries.append({"id": f"E{len(entries) + 1}", "kind": kind, "who": who, "text": text})

    before = _checks(board, parent) if parent else {}
    after = _checks(board, vid)
    for name in MAIN:
        c = before.get(name)
        if c and not c["passed"]:
            fl = c["details"].get("failures", [])
            add("failed_check", name, f"{name} failed with {len(fl)} failures before this edit, e.g. "
                + "; ".join(f["msg"] for f in fl[:3]))
    if parent:
        for m in board.messages(1, parent):
            if m["kind"] in ("need", "decline"):
                add(m["kind"], m["author"], m["text"][:300])
    for m in board.messages(1, vid):
        if m["kind"] == "propose":
            add("proposal", m["author"], m["text"][:400])
    for m in board.messages(1, vid):
        if m["kind"] in ("object", "support"):
            add(m["kind"], m["author"], m["text"][:300])
    bd = next((json.loads(m["text"]) for m in board.messages(1, vid) if m["kind"] == "breakdown"), None)
    if bd:
        add("breakdown", "system", f"fixed {len(bd['fixed'])}: {'; '.join(bd['fixed'][:3])} | broke {len(bd['new'])}: "
            f"{'; '.join(bd['new'][:3])}")
    dec = next((d for d in board.decisions(1) if d["version_id"] == vid), None)
    if dec:
        add("decision", "protocol", f"{dec['decision']}: {dec['reason']}")
    fb = sum(len(before[n]["details"].get("failures", [])) for n in MAIN if n in before)
    fa = sum(len(after[n]["details"].get("failures", [])) for n in MAIN if n in after)
    q = lambda cs: (cs.get("quality") or {}).get("details", {}).get("metrics", {}).get("score")  # noqa: E731
    numbers = {"version": vid, "author": v["author"], "failures_before": fb, "failures_after": fa,
               "fixed": len(bd["fixed"]) if bd else 0, "broke": len(bd["new"]) if bd else 0,
               "supports": sum(e["kind"] == "support" for e in entries),
               "objections": sum(e["kind"] == "object" for e in entries),
               "quality_before": q(before), "quality_after": q(after),
               "decision": dec["decision"] if dec else v["status"]}
    return entries, numbers


PROMPT = """Write a 2-3 sentence plain-language summary of why this design version looks the way it does, for an
engineer. Use ONLY the decision trail below. Rules: write NO digits at all. For any number use these placeholders
exactly: {placeholders}. Cite the trail entries you rely on like [E2]. Reply with the summary text only."""


def ai_summary(board, vid, llm, mock=False):
    """Returns {"text", "flagged", "entries_cited"}; cached on the board as an artifact."""
    cached = [a for a in board.artifacts(1, vid) if a["kind"] == "ai_summary"]
    if cached:
        return json.loads(cached[-1]["path"])
    entries, numbers = trail(board, vid)
    ph = ", ".join("{" + k + "}" for k in numbers)
    body = "\n".join(f"[{e['id']}] {e['kind']} by {e['who']}: {e['text']}" for e in entries)

    def mock_text():
        cite = " ".join(f"[{e['id']}]" for e in entries if e["kind"] in ("proposal", "decision"))
        return ("{author} proposed this edit; it fixed {fixed} failures and broke {broke}, taking the design from "
                "{failures_before} to {failures_after} failures. The protocol {decision} it with {supports} "
                f"supporting and {{objections}} objecting. {cite}")

    if mock or llm is None:
        raw = mock_text()
    else:
        raw, _ = llm.complete("You summarize engineering decision logs. No digits.",
                              PROMPT.format(placeholders=ph) + "\n\n" + body, [], mock_fn=lambda: mock_text())
        if raw.strip().startswith("{") and '"action"' in raw:
            raw = mock_text()
    # strip digits that are not inside {placeholders} or [E#] citations
    protected = re.sub(r"\{[a-z_]+\}|\[E\d+\]", lambda m: "\0" * len(m.group(0)), raw)
    flagged = bool(re.search(r"\d", protected))
    clean = "".join(ch if not (protected[i].isdigit()) else "" for i, ch in enumerate(raw))
    try:
        text = clean.format(**{k: ("n/a" if v is None else v) for k, v in numbers.items()})
    except (KeyError, IndexError, ValueError):
        text, flagged = clean, True
    out = {"text": text.strip(), "flagged": flagged, "entries_cited": sorted(set(re.findall(r"E\d+", raw))),
           "mocked": bool(mock or llm is None)}
    board.add_artifact(1, vid, "ai_summary", json.dumps(out))
    return out
