"""SQLite persistence: sessions, step traces, and fetch snapshots.

Snapshots are append-only. A fetch never overwrites its predecessor, because
"what did we believe at 21:04, and on what evidence" is the question that
matters after an evacuation, and an overwriting store cannot answer it
(DESIGN section 2.5).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import settings
from app.models import Step

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    state        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    label        TEXT NOT NULL,
    detail       TEXT,
    arguments    TEXT,
    outcome      TEXT,
    latency_ms   INTEGER,
    simulated    INTEGER NOT NULL DEFAULT 0,
    at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_session ON steps(session_id, seq);

CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,
    source_id    TEXT NOT NULL,
    url          TEXT,
    outcome      TEXT NOT NULL,
    status       INTEGER,
    fetched_at   TEXT NOT NULL,
    body         TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_source ON snapshots(source_id, fetched_at);
"""

_lock = threading.Lock()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)


def purge_all() -> dict[str, int]:
    """Delete prototype session data and return auditable row counts."""
    with _lock, _connect() as conn:
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("sessions", "steps", "snapshots")
        }
        # Child-like records first, even though this prototype has no FK rules.
        conn.execute("DELETE FROM steps")
        conn.execute("DELETE FROM snapshots")
        conn.execute("DELETE FROM sessions")
    return counts


def save_session(session_id: str, created_at: str, updated_at: str, state: dict[str, Any]) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO sessions (session_id, created_at, updated_at, state)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                 updated_at = excluded.updated_at,
                 state      = excluded.state""",
            (session_id, created_at, updated_at, json.dumps(state, default=str)),
        )


def load_session(session_id: str) -> dict[str, Any] | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT state FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["state"])
    except ValueError:
        return None


def append_step(session_id: str, step: Step) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO steps
                 (session_id, seq, kind, label, detail, arguments, outcome,
                  latency_ms, simulated, at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                step.seq,
                step.kind.value,
                step.label,
                step.detail,
                json.dumps(step.arguments, default=str) if step.arguments else None,
                step.outcome,
                step.latency_ms,
                int(step.simulated),
                step.at.isoformat(),
            ),
        )


def load_steps(session_id: str) -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM steps WHERE session_id = ? ORDER BY seq", (session_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def append_snapshot(
    *,
    session_id: str | None,
    source_id: str,
    url: str | None,
    outcome: str,
    status: int | None,
    fetched_at: str,
    body: str | None,
    max_body: int = 200_000,
) -> None:
    """Archive one fetch. Never updates an existing row."""
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO snapshots
                 (session_id, source_id, url, outcome, status, fetched_at, body)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                source_id,
                url,
                outcome,
                status,
                fetched_at,
                (body or "")[:max_body] or None,
            ),
        )


def snapshot_count() -> int:
    with _lock, _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) c FROM snapshots").fetchone()["c"])
