"""Append-only SQLite records and a verifiable event chain."""
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .domain import identifier, utc_now


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Store:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.artifacts = self.directory / "artifacts"
        self.artifacts.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "scienceclaw.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS records (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE NOT NULL, run_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS records_run ON records(run_id,kind);
        CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
            run_id TEXT NOT NULL, timestamp TEXT NOT NULL, type TEXT NOT NULL,
            payload TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS records_no_update BEFORE UPDATE ON records BEGIN SELECT RAISE(ABORT,'immutable record'); END;
        CREATE TRIGGER IF NOT EXISTS records_no_delete BEFORE DELETE ON records BEGIN SELECT RAISE(ABORT,'immutable record'); END;
        CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only log'); END;
        CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only log'); END;
        """)

    @contextmanager
    def atomic(self):
        with self.lock:
            nested = self.db.in_transaction
            if not nested:
                self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                if not nested:
                    self.db.commit()
            except Exception:
                if not nested:
                    self.db.rollback()
                raise

    def put(self, kind: str, run_id: str, value: dict):
        with self.atomic():
            self.db.execute("INSERT INTO records(id,run_id,kind,payload) VALUES(?,?,?,?)", (value["id"], run_id, kind, canonical(value)))
            self.event(run_id, f"{kind}_recorded", {"record_id": value["id"], "record_hash": digest(value)})
        return value

    def get(self, record_id: str, kind: str | None = None):
        with self.lock:
            row = self.db.execute("SELECT kind,payload FROM records WHERE id=?", (record_id,)).fetchone()
            if row is None or (kind is not None and row["kind"] != kind):
                raise KeyError(f"Unknown {kind or 'record'}: {record_id}")
            return json.loads(row["payload"])

    def list(self, run_id: str | None, kind: str):
        with self.lock:
            query = "SELECT payload FROM records WHERE kind=?"
            args = [kind]
            if run_id is not None:
                query += " AND run_id=?"
                args.append(run_id)
            rows = self.db.execute(query + " ORDER BY seq", args).fetchall()
            return [json.loads(row["payload"]) for row in rows]

    def event(self, run_id: str, event_type: str, payload: dict):
        with self.atomic():
            row = self.db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            event = {"id": identifier("evt"), "run_id": run_id, "timestamp": utc_now(), "type": event_type,
                     "payload": payload, "previous_hash": row["hash"] if row else "0" * 64}
            event["hash"] = digest(event)
            self.db.execute("INSERT INTO events(id,run_id,timestamp,type,payload,previous_hash,hash) VALUES(?,?,?,?,?,?,?)",
                            (event["id"], run_id, event["timestamp"], event_type, canonical(payload), event["previous_hash"], event["hash"]))
            return event

    def events(self, run_id: str | None = None):
        with self.lock:
            rows = self.db.execute("SELECT * FROM events" + (" WHERE run_id=?" if run_id else "") + " ORDER BY seq", (run_id,) if run_id else ()).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def verify(self):
        previous = "0" * 64
        for event in self.events():
            supplied = event.pop("hash")
            event.pop("seq")
            if event["previous_hash"] != previous or digest(event) != supplied:
                return False
            previous = supplied
        return True

    def artifact(self, content: str, suffix: str):
        raw = content.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        name = f"{sha}.{suffix}"
        destination = self.artifacts / name
        if not destination.exists():
            destination.write_bytes(raw)
        return {"filename": name, "sha256": sha, "bytes": len(raw)}

    def close(self):
        self.db.close()
