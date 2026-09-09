"""SQLite state: sessions, an append-only event log, and a usage log.

Every agent message is written here before it is published, so the log survives
a backend restart and a reconnecting tab replays the whole conversation rather
than resuming mid-sentence.

`events` and `usage` are deliberately two tables and not one. `events` is the
agent's transcript — what Claude said, kept so you can read it back. `usage` is
what *you* did with the workbench, kept so the workbench can be made better. The
first holds prose and the second holds none; keeping them apart is what makes
that promise checkable rather than a claim.
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
    ended_at          REAL,
    sel_path          TEXT,
    sel_start         INTEGER,
    sel_end           INTEGER,
    sel_text          TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    ts           REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS events_by_session ON events(session_id, id);

CREATE TABLE IF NOT EXISTS usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          REAL NOT NULL,
    kind        TEXT NOT NULL,
    detail_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS usage_by_time ON usage(at);
CREATE INDEX IF NOT EXISTS usage_by_kind ON usage(kind, at);
"""

# Columns added after the first release. SQLite has no "ADD COLUMN IF NOT
# EXISTS", so the additions are listed here and applied to whatever the file on
# disk already has: an existing .galley/galley.db keeps its sessions.
ADDED_COLUMNS = {
    "sessions": {
        "sel_path": "TEXT",
        "sel_start": "INTEGER",
        "sel_end": "INTEGER",
        "sel_text": "TEXT",
    }
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        for table, columns in ADDED_COLUMNS.items():
            have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

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

    # -- events -----------------------------------------------------------

    def append_event(
        self,
        kind: str,
        payload: Any,
        session_id: str | None = None,
    ) -> dict:
        ts = time.time()
        cur = self.execute(
            "INSERT INTO events (session_id, kind, payload_json, ts) VALUES (?, ?, ?, ?)",
            (session_id, kind, json.dumps(payload, default=str), ts),
        )
        return {
            "id": cur.lastrowid,
            "session_id": session_id,
            "kind": kind,
            "payload": payload,
            "ts": ts,
        }

    # -- usage ------------------------------------------------------------

    def append_usage(self, rows: list[tuple[float, str, str]]) -> int:
        """Write a batch of already-scrubbed usage rows. Returns how many.

        A batch because the browser sends them in one go: an interaction log
        that made a request per click would itself be the slowest thing in the
        UI, and would change the behaviour it is trying to measure.
        """
        if not rows:
            return 0
        self._conn.executemany(
            "INSERT INTO usage (at, kind, detail_json) VALUES (?, ?, ?)", rows
        )
        self._conn.commit()
        return len(rows)

    def usage_since(self, since: float) -> list[dict]:
        rows = self.query("SELECT * FROM usage WHERE at >= ? ORDER BY at", (since,))
        for r in rows:
            r["detail"] = json.loads(r.pop("detail_json"))
        return rows

    def usage_span(self) -> dict | None:
        """The first and last thing recorded, and how many there are."""
        return self.one("SELECT MIN(at) AS first, MAX(at) AS last, COUNT(*) AS n FROM usage")

    def forget_usage(self, before: float | None = None) -> int:
        """Delete usage rows, all of them or everything older than `before`."""
        if before is None:
            cur = self.execute("DELETE FROM usage")
        else:
            cur = self.execute("DELETE FROM usage WHERE at < ?", (before,))
        self.execute("VACUUM")
        return cur.rowcount

    def session_events(self, session_id: str, after: int = 0) -> list[dict]:
        rows = self.query(
            "SELECT * FROM events WHERE session_id = ? AND id > ? ORDER BY id",
            (session_id, after),
        )
        for r in rows:
            r["payload"] = json.loads(r.pop("payload_json"))
        return rows
