"""SQLite state: sessions, jobs, and an append-only event log.

Every job transition is written here *before* it is published to the UI, so a
backend restart resumes from the record rather than from whatever the UI last
saw. The event log is replayed to rebuild a session's log after a restart.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,
    claude_session_id TEXT,
    slug              TEXT NOT NULL,
    branch            TEXT NOT NULL,
    worktree_path     TEXT NOT NULL,
    prompt            TEXT NOT NULL,
    status            TEXT NOT NULL,
    base_sha          TEXT,
    error             TEXT,
    created_at        REAL NOT NULL,
    ended_at          REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    session_id      TEXT,
    scheduler_id    TEXT,
    host            TEXT,
    code_sha        TEXT,
    submit_cmd      TEXT,
    state           TEXT NOT NULL,
    exit_code       INTEGER,
    artifacts_local TEXT,
    note            TEXT,
    workdir         TEXT,
    submitted_at    REAL NOT NULL,
    started_at      REAL,
    finished_at     REAL
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,
    job_id       TEXT,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    ts           REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS events_by_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS jobs_by_state     ON jobs(state);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- generic ----------------------------------------------------------

    def execute(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        cur = self._conn.execute(sql, tuple(args))
        self._conn.commit()
        return cur

    def query(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self._conn.execute(sql, tuple(args)).fetchall()]

    def one(self, sql: str, args: Iterable[Any] = ()) -> dict | None:
        row = self._conn.execute(sql, tuple(args)).fetchone()
        return dict(row) if row else None

    # -- sessions ---------------------------------------------------------

    def create_session(self, **fields: Any) -> None:
        fields.setdefault("created_at", time.time())
        cols = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        self.execute(f"INSERT INTO sessions ({cols}) VALUES ({marks})", fields.values())

    def update_session(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.execute(
            f"UPDATE sessions SET {sets} WHERE id = ?", [*fields.values(), session_id]
        )

    def get_session(self, session_id: str) -> dict | None:
        return self.one("SELECT * FROM sessions WHERE id = ?", (session_id,))

    def list_sessions(self) -> list[dict]:
        return self.query("SELECT * FROM sessions ORDER BY created_at DESC")

    def active_session_count(self) -> int:
        row = self.one("SELECT COUNT(*) AS n FROM sessions WHERE status = 'running'")
        return int(row["n"]) if row else 0

    # -- jobs -------------------------------------------------------------

    def create_job(self, **fields: Any) -> None:
        fields.setdefault("submitted_at", time.time())
        cols = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        self.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", fields.values())

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE jobs SET {sets} WHERE id = ?", [*fields.values(), job_id])

    def get_job(self, job_id: str) -> dict | None:
        return self.one("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def list_jobs(self, state: str | None = None, since: float | None = None) -> list[dict]:
        sql = "SELECT * FROM jobs"
        args: list[Any] = []
        where = []
        if state:
            where.append("state = ?")
            args.append(state)
        if since:
            where.append("submitted_at >= ?")
            args.append(since)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.query(sql + " ORDER BY submitted_at DESC", args)

    def live_jobs(self) -> list[dict]:
        return self.query(
            "SELECT * FROM jobs WHERE state IN "
            "('SUBMITTED','PENDING','RUNNING','UNREACHABLE') ORDER BY submitted_at"
        )

    # -- events -----------------------------------------------------------

    def append_event(
        self,
        kind: str,
        payload: Any,
        session_id: str | None = None,
        job_id: str | None = None,
    ) -> dict:
        ts = time.time()
        cur = self.execute(
            "INSERT INTO events (session_id, job_id, kind, payload_json, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, job_id, kind, json.dumps(payload, default=str), ts),
        )
        return {
            "id": cur.lastrowid,
            "session_id": session_id,
            "job_id": job_id,
            "kind": kind,
            "payload": payload,
            "ts": ts,
        }

    def session_events(self, session_id: str, after: int = 0) -> list[dict]:
        rows = self.query(
            "SELECT * FROM events WHERE session_id = ? AND id > ? ORDER BY id",
            (session_id, after),
        )
        for r in rows:
            r["payload"] = json.loads(r.pop("payload_json"))
        return rows
