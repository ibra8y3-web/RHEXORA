"""Small durable idempotency ledger for worker task requests."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class TaskStore:
    """Persist task request IDs, hashes and outcomes without persisting task inputs."""

    def __init__(self, path: str | Path, *, retention_seconds: int = 14 * 24 * 60 * 60) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_seconds = retention_seconds
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_records (
                    request_id TEXT PRIMARY KEY,
                    task_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_json TEXT,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            cutoff = int(time.time()) - retention_seconds
            connection.execute("DELETE FROM task_records WHERE updated_at < ?", (cutoff,))
            # An in-progress task from a previous process cannot still be running.
            connection.execute(
                "UPDATE task_records SET status='interrupted', updated_at=? WHERE status='running'",
                (int(time.time()),),
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def claim(self, request_id: str, task_hash: str) -> dict[str, Any]:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT task_hash, status, response_json FROM task_records WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO task_records(request_id, task_hash, status, response_json, updated_at) VALUES(?, ?, 'running', NULL, ?)",
                    (request_id, task_hash, now),
                )
                return {"state": "claimed"}
            if row["task_hash"] != task_hash:
                return {"state": "conflict"}
            state = row["status"]
            if state == "completed" or state == "failed":
                return {
                    "state": state,
                    "response": json.loads(row["response_json"] or "{}"),
                }
            if state == "interrupted":
                connection.execute(
                    "UPDATE task_records SET status='running', updated_at=? WHERE request_id=?",
                    (now, request_id),
                )
                return {"state": "claimed"}
            return {"state": "running"}

    def save_response(
        self,
        request_id: str,
        *,
        task_hash: str,
        response: dict[str, Any],
        status: str,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("status must be 'completed' or 'failed'")
        encoded = json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_records
                SET status=?, response_json=?, updated_at=?
                WHERE request_id=? AND task_hash=?
                """,
                (status, encoded, int(time.time()), request_id, task_hash),
            )

    def record_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM task_records").fetchone()
            return int(row["count"])
