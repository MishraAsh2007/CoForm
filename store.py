# Saves candidate parameters, results, agent messages, and paths to generated files.
#
# Layout:  runs/<run_id>/<candidate_id>/{bracket.stl, bracket.step, record.json}
#          runs/<run_id>/messages.jsonl, proposals.jsonl, turns.jsonl
# The Fusion add-in depends on the runs/<run_id>/<candidate_id>/ layout.
import json
import os
from pathlib import Path

from schemas import CandidateRecord, Message, Proposal

RUNS_DIR = Path(__file__).resolve().parent / "runs"


def candidate_dir(run_id, candidate_id):
    path = RUNS_DIR / str(run_id) / str(candidate_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def geometry_paths(run_id, candidate_id):
    """Return (stl_path, step_path) for a candidate, creating its directory."""
    path = candidate_dir(run_id, candidate_id)
    return path / "bracket.stl", path / "bracket.step"


def save_record(run_id, record: CandidateRecord):
    """Write record.json atomically (temp file + rename)."""
    path = candidate_dir(run_id, record.candidate_id) / "record.json"
    tmp = path.with_name("record.partial.json")
    tmp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_record(run_id, candidate_id):
    path = RUNS_DIR / str(run_id) / str(candidate_id) / "record.json"
    if not path.exists():
        return None
    return CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))


def list_records(run_id):
    run_dir = RUNS_DIR / str(run_id)
    if not run_dir.is_dir():
        return []
    return [CandidateRecord.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(run_dir.glob("*/record.json"))]


def append_message(run_id, message: Message):
    path = RUNS_DIR / str(run_id) / "messages.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(message.model_dump_json() + "\n")


def read_messages(run_id):
    path = RUNS_DIR / str(run_id) / "messages.jsonl"
    if not path.exists():
        return []
    return [Message.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_jsonl(run_id, name, line):
    path = RUNS_DIR / str(run_id) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _read_jsonl(run_id, name):
    path = RUNS_DIR / str(run_id) / name
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_proposal(run_id, proposal: Proposal):
    _append_jsonl(run_id, "proposals.jsonl", proposal.model_dump_json())


def read_proposals(run_id):
    return [Proposal.model_validate_json(line) for line in _read_jsonl(run_id, "proposals.jsonl")]


def append_turn(run_id, entry: dict):
    """Log one agent turn (tool calls, tokens, final text) for later analysis."""
    _append_jsonl(run_id, "turns.jsonl", json.dumps(entry, default=str))


def read_turns(run_id):
    return [json.loads(line) for line in _read_jsonl(run_id, "turns.jsonl")]
