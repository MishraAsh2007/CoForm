"""Shared board: one SQLite file that every agent reads and writes through.

Tables: runs, versions (design spec + parent + author + reason), checks
(per-version check results), messages (propose/object/support/note),
decisions (accepted/rejected/discuss) and usage (tokens, cost).
Every row is timestamped (UTC, ISO 8601).
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, mode TEXT, brief TEXT, seed INTEGER, budget INTEGER,
  model TEXT, mocked INTEGER, status TEXT, notes TEXT, started_at TEXT, ended_at TEXT);
CREATE TABLE IF NOT EXISTS versions (
  id INTEGER PRIMARY KEY, run_id INTEGER, parent_id INTEGER, author TEXT, reason TEXT,
  spec_json TEXT, patch_json TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY, run_id INTEGER, version_id INTEGER, name TEXT, owner TEXT,
  passed INTEGER, failures INTEGER, details_json TEXT, mocked INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, run_id INTEGER, version_id INTEGER, author TEXT, kind TEXT,
  text TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, run_id INTEGER, version_id INTEGER, decision TEXT, reason TEXT,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY, run_id INTEGER, version_id INTEGER, kind TEXT, path TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY, run_id INTEGER, agent TEXT, input_tokens INTEGER,
  output_tokens INTEGER, cost_usd REAL, created_at TEXT);
"""

MESSAGE_KINDS = ("propose", "object", "support", "note", "need", "decline", "breakdown")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Board:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def _insert(self, table, **row):
        row.setdefault("created_at", now())
        cols = ", ".join(row)
        q = ", ".join("?" for _ in row)
        cur = self.db.execute(f"INSERT INTO {table} ({cols}) VALUES ({q})", list(row.values()))
        self.db.commit()
        return cur.lastrowid

    # ---- runs ----
    def start_run(self, mode, brief, seed, budget, model, mocked):
        cur = self.db.execute(
            "INSERT INTO runs (mode, brief, seed, budget, model, mocked, status, started_at) VALUES (?,?,?,?,?,?,?,?)",
            (mode, brief, seed, budget, model, int(mocked), "running", now()))
        self.db.commit()
        return cur.lastrowid

    def end_run(self, run_id, status, notes=""):
        self.db.execute("UPDATE runs SET status=?, notes=?, ended_at=? WHERE id=?", (status, notes, now(), run_id))
        self.db.commit()

    def run(self, run_id):
        return dict(self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    # ---- versions ----
    def add_version(self, run_id, spec, author, reason, parent_id=None, patch=None, status="proposed"):
        return self._insert("versions", run_id=run_id, parent_id=parent_id, author=author, reason=reason,
                            spec_json=json.dumps(spec, sort_keys=True), patch_json=json.dumps(patch or []),
                            status=status)

    def set_version_status(self, version_id, status):
        self.db.execute("UPDATE versions SET status=? WHERE id=?", (status, version_id))
        self.db.commit()

    def version(self, version_id):
        r = self.db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
        return self._version_row(r) if r else None

    def versions(self, run_id):
        return [self._version_row(r) for r in
                self.db.execute("SELECT * FROM versions WHERE run_id=? ORDER BY id", (run_id,))]

    def current(self, run_id):
        """Latest accepted (or baseline) version."""
        r = self.db.execute("SELECT * FROM versions WHERE run_id=? AND status IN ('accepted','baseline') "
                            "ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
        return self._version_row(r) if r else None

    @staticmethod
    def _version_row(r):
        d = dict(r)
        d["spec"] = json.loads(d.pop("spec_json"))
        d["patch"] = json.loads(d.pop("patch_json") or "[]")
        return d

    # ---- checks ----
    def add_check(self, run_id, version_id, name, owner, result):
        """result: {passed, failures: [..], ...}; stored whole as JSON."""
        return self._insert("checks", run_id=run_id, version_id=version_id, name=name, owner=owner,
                            passed=int(bool(result.get("passed"))), failures=len(result.get("failures", [])),
                            details_json=json.dumps(result, default=str), mocked=int(bool(result.get("mocked"))))

    def checks(self, run_id, version_id=None):
        q, args = "SELECT * FROM checks WHERE run_id=?", [run_id]
        if version_id is not None:
            q += " AND version_id=?"
            args.append(version_id)
        out = []
        for r in self.db.execute(q + " ORDER BY id", args):
            d = dict(r)
            d["details"] = json.loads(d.pop("details_json"))
            out.append(d)
        return out

    # ---- messages, decisions, usage ----
    def post(self, run_id, author, kind, text, version_id=None):
        if kind not in MESSAGE_KINDS:
            raise ValueError(f"message kind must be one of {MESSAGE_KINDS}, got {kind}")
        return self._insert("messages", run_id=run_id, version_id=version_id, author=author, kind=kind, text=text)

    def messages(self, run_id, version_id=None):
        q, args = "SELECT * FROM messages WHERE run_id=?", [run_id]
        if version_id is not None:
            q += " AND version_id=?"
            args.append(version_id)
        return [dict(r) for r in self.db.execute(q + " ORDER BY id", args)]

    def decide(self, run_id, version_id, decision, reason):
        return self._insert("decisions", run_id=run_id, version_id=version_id, decision=decision, reason=reason)

    def decisions(self, run_id):
        return [dict(r) for r in self.db.execute("SELECT * FROM decisions WHERE run_id=? ORDER BY id", (run_id,))]

    def add_artifact(self, run_id, version_id, kind, path):
        return self._insert("artifacts", run_id=run_id, version_id=version_id, kind=kind, path=str(path))

    def artifacts(self, run_id, version_id=None):
        q, args = "SELECT * FROM artifacts WHERE run_id=?", [run_id]
        if version_id is not None:
            q += " AND version_id=?"
            args.append(version_id)
        return [dict(r) for r in self.db.execute(q + " ORDER BY id", args)]

    def log_usage(self, run_id, agent, input_tokens, output_tokens, cost_usd):
        return self._insert("usage", run_id=run_id, agent=agent, input_tokens=input_tokens,
                            output_tokens=output_tokens, cost_usd=cost_usd)

    def usage_total(self, run_id):
        r = self.db.execute("SELECT COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o, "
                            "COALESCE(SUM(cost_usd),0) c FROM usage WHERE run_id=?", (run_id,)).fetchone()
        return {"input_tokens": r["i"], "output_tokens": r["o"], "cost_usd": round(r["c"], 4)}
