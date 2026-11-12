"""Private local metadata journal, independent of optional business/PostgreSQL services.

SQLite provides cross-process compare-and-swap for profile revisions and task attempts.
It stores no provider credentials and never resumes a paid task automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ACTIVE = ("loading", "planning", "executing", "reviewing", "validating", "saving")
TERMINAL = ("completed", "failed", "cancelled", "interrupted")
LEASE_SECONDS = 90


class WorkspaceError(RuntimeError):
    status_code = 503


class WorkspaceConflict(WorkspaceError):
    status_code = 409


class WorkspaceNotFound(WorkspaceError):
    status_code = 404


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def decode_record(value: str) -> dict:
    try:
        result = json.loads(value)
        if not isinstance(result, dict):
            raise ValueError("invalid record")
        canonical(result)
        return result
    except (ValueError, TypeError):
        raise WorkspaceError("本机工作区记录损坏，请从备份恢复或重新导入有效口径") from None


class WorkspaceStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise WorkspaceError("本机工作区版本较新，请使用相应版本的应用")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS business_profiles (
                    id TEXT NOT NULL, revision INTEGER NOT NULL, definition TEXT NOT NULL,
                    digest TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(id, revision)
                );
                CREATE TABLE IF NOT EXISTS analysis_tasks (
                    id TEXT PRIMARY KEY, question TEXT NOT NULL, session_id TEXT NOT NULL,
                    mode TEXT NOT NULL, profile_ref TEXT NOT NULL, context_revision INTEGER NOT NULL,
                    status TEXT NOT NULL, attempt INTEGER NOT NULL, owner TEXT NOT NULL,
                    heartbeat REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    stages TEXT NOT NULL, response TEXT, error TEXT
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL, event TEXT NOT NULL, created_at REAL NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES analysis_tasks(id)
                );
                CREATE TABLE IF NOT EXISTS session_profiles (
                    session_id TEXT PRIMARY KEY, profile_ref TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS task_recent ON analysis_tasks(updated_at DESC);
                CREATE INDEX IF NOT EXISTS event_task ON task_events(task_id, sequence);
                PRAGMA user_version=1;
            """)
        if os.name != "nt":
            path.chmod(0o600)

    @contextmanager
    def _connect(self, *, write=False):
        conn = None
        try:
            conn = sqlite3.connect(self.path, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except sqlite3.Error:
            if conn is not None:
                conn.rollback()
            raise WorkspaceError("本机工作区暂时不可读写，请检查磁盘与文件权限") from None
        finally:
            if conn is not None:
                conn.close()

    def save_profile(self, identifier: str, definition: dict, expected_revision: int) -> dict:
        encoded = canonical(definition)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self._connect(write=True) as conn:
            current = (
                conn.execute(
                    "SELECT MAX(revision) FROM business_profiles WHERE id=?", (identifier,)
                ).fetchone()[0]
                or 0
            )
            if current != expected_revision:
                raise WorkspaceConflict("业务口径已被修改，请重新读取最新版本后保存")
            conn.execute(
                "INSERT INTO business_profiles VALUES(?,?,?,?,?)",
                (identifier, current + 1, encoded, digest, time.time()),
            )
        return self.get_profile(identifier)

    def get_profile(self, identifier: str, revision: int | None = None) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM business_profiles WHERE id=? "
                + ("AND revision=? " if revision is not None else "")
                + "ORDER BY revision DESC LIMIT 1",
                (identifier, revision) if revision is not None else (identifier,),
            ).fetchone()
        if row is None:
            raise WorkspaceNotFound("业务口径不存在或该版本不可用")
        definition = decode_record(row["definition"])
        if hashlib.sha256(canonical(definition).encode()).hexdigest() != row["digest"]:
            raise WorkspaceError("业务口径版本校验失败，请恢复备份或导入新口径")
        return {
            "id": row["id"],
            "revision": row["revision"],
            "digest": row["digest"],
            "definition": definition,
            "created_at": timestamp(row["created_at"]),
        }

    def list_profiles(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,MAX(revision) revision FROM business_profiles GROUP BY id ORDER BY id LIMIT 200"
            ).fetchall()
        return [self.get_profile(row["id"], row["revision"]) for row in rows]

    def _recover_stale(self, conn) -> None:
        marks = ",".join("?" for _ in ACTIVE)
        rows = conn.execute(
            f"SELECT id,attempt,stages FROM analysis_tasks WHERE status IN ({marks}) AND heartbeat<?",
            (*ACTIVE, time.time() - LEASE_SECONDS),
        ).fetchall()
        for row in rows:
            stages = {
                key: "interrupted" if value == "running" else value
                for key, value in decode_record(row["stages"]).items()
            }
            conn.execute(
                "UPDATE analysis_tasks SET status='interrupted',owner='',stages=?,updated_at=?,error=? WHERE id=?",
                (
                    canonical(stages),
                    time.time(),
                    "上一次执行已中断；手动恢复将重新查询数据，可能产生模型费用。",
                    row["id"],
                ),
            )
            self._event(
                conn, row["id"], row["attempt"], {"type": "finished", "status": "interrupted"}
            )

    def start_task(
        self,
        question: str,
        session_id: str,
        mode: str,
        profile_ref: dict,
        context_revision: int,
        *,
        resume_id: str | None = None,
    ) -> dict:
        now, owner = time.time(), uuid.uuid4().hex
        with self._connect(write=True) as conn:
            self._recover_stale(conn)
            binding = conn.execute(
                "SELECT profile_ref FROM session_profiles WHERE session_id=?", (session_id,)
            ).fetchone()
            if binding is not None and decode_record(binding["profile_ref"]) != profile_ref:
                raise WorkspaceConflict("此会话已绑定其他业务口径或版本，请新建分析，不能混用条件")
            conn.execute(
                "INSERT OR IGNORE INTO session_profiles VALUES(?,?)",
                (session_id, canonical(profile_ref)),
            )
            if resume_id:
                row = conn.execute(
                    "SELECT * FROM analysis_tasks WHERE id=?", (resume_id,)
                ).fetchone()
                if row is None:
                    raise WorkspaceNotFound("分析任务不存在")
                if row["status"] not in {"failed", "cancelled", "interrupted"}:
                    raise WorkspaceConflict(
                        "只能恢复失败、停止或已中断的任务；运行中或完成的任务不能重复提交"
                    )
                if (row["question"], row["session_id"], row["mode"]) != (
                    question,
                    session_id,
                    mode,
                ):
                    raise WorkspaceConflict("恢复请求与原任务不一致，请重新读取任务")
                if (
                    decode_record(row["profile_ref"]) != profile_ref
                    or row["context_revision"] != context_revision
                ):
                    raise WorkspaceConflict(
                        "业务口径或会话条件已变化，请基于当前条件新建分析，旧任务不再复用"
                    )
                identifier, attempt = resume_id, row["attempt"] + 1
                if attempt > 20:
                    raise WorkspaceConflict("此任务已达到恢复次数上限，请新建分析")
                conn.execute(
                    "UPDATE analysis_tasks SET status='loading',attempt=?,owner=?,heartbeat=?,updated_at=?,stages='{}',response=NULL,error=NULL WHERE id=?",
                    (attempt, owner, now, now, identifier),
                )
            else:
                identifier, attempt = str(uuid.uuid4()), 1
                conn.execute(
                    "INSERT INTO analysis_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        question,
                        session_id,
                        mode,
                        canonical(profile_ref),
                        context_revision,
                        "loading",
                        attempt,
                        owner,
                        now,
                        now,
                        now,
                        "{}",
                        None,
                        None,
                    ),
                )
            self._event(
                conn,
                identifier,
                attempt,
                {"type": "attempt", "attempt": attempt, "fresh_queries": True},
            )
        return {"id": identifier, "owner": owner, "attempt": attempt}

    def _owned(self, conn, identifier: str, owner: str):
        row = conn.execute(
            "SELECT * FROM analysis_tasks WHERE id=? AND owner=?", (identifier, owner)
        ).fetchone()
        if row is None or row["status"] not in ACTIVE:
            raise WorkspaceConflict("任务执行权已失效，本次执行不能继续写入")
        return row

    @staticmethod
    def _event(conn, identifier: str, attempt: int, event: dict):
        conn.execute(
            "INSERT INTO task_events(task_id,attempt,event,created_at) VALUES(?,?,?,?)",
            (identifier, attempt, canonical(event), time.time()),
        )

    def phase(self, identifier: str, owner: str, phase: str) -> None:
        if phase not in ACTIVE:
            raise ValueError("invalid task phase")
        with self._connect(write=True) as conn:
            row = self._owned(conn, identifier, owner)
            stages = decode_record(row["stages"])
            for key in stages:
                if stages[key] == "running":
                    stages[key] = "completed"
            stages[phase] = "running"
            now = time.time()
            conn.execute(
                "UPDATE analysis_tasks SET status=?,stages=?,heartbeat=?,updated_at=? WHERE id=?",
                (phase, canonical(stages), now, now, identifier),
            )
            self._event(conn, identifier, row["attempt"], {"type": "phase", "phase": phase})

    def bind_context(self, identifier: str, owner: str, revision: int) -> None:
        with self._connect(write=True) as conn:
            row = self._owned(conn, identifier, owner)
            if row["context_revision"] not in (-1, revision):
                raise WorkspaceConflict("会话条件已变化，请新建分析；恢复任务不能沿用旧条件")
            conn.execute(
                "UPDATE analysis_tasks SET context_revision=? WHERE id=?", (revision, identifier)
            )

    def clear_binding(self, session_id: str) -> None:
        with self._connect(write=True) as conn:
            conn.execute("DELETE FROM session_profiles WHERE session_id=?", (session_id,))

    def record_event(self, identifier: str, owner: str, event: dict) -> None:
        # Deliberately exclude assistant free text / thought and any client settings.
        allowed = {
            key: event[key]
            for key in (
                "type",
                "step",
                "tool",
                "arguments",
                "ok",
                "output",
                "duration_ms",
                "steps",
                "trace_id",
                "session_id",
            )
            if key in event
        }
        if len(canonical(allowed)) > 48000:
            raise WorkspaceError("工具执行记录超过本机存储预算，请简化查询；本次执行已停止")
        with self._connect(write=True) as conn:
            row = self._owned(conn, identifier, owner)
            self._event(conn, identifier, row["attempt"], allowed)
            now = time.time()
            conn.execute(
                "UPDATE analysis_tasks SET heartbeat=?,updated_at=? WHERE id=?",
                (now, now, identifier),
            )

    def heartbeat(self, identifier: str, owner: str) -> None:
        with self._connect(write=True) as conn:
            self._owned(conn, identifier, owner)
            conn.execute(
                "UPDATE analysis_tasks SET heartbeat=? WHERE id=?", (time.time(), identifier)
            )

    def finish_task(
        self,
        identifier: str,
        owner: str,
        status: str,
        *,
        response: dict | None = None,
        error: str | None = None,
    ) -> None:
        if status not in TERMINAL:
            raise ValueError("invalid terminal status")
        with self._connect(write=True) as conn:
            row = self._owned(conn, identifier, owner)
            stages = decode_record(row["stages"])
            for key in stages:
                if stages[key] == "running":
                    stages[key] = "completed" if status == "completed" else status
            conn.execute(
                "UPDATE analysis_tasks SET status=?,owner='',updated_at=?,stages=?,response=?,error=? WHERE id=?",
                (
                    status,
                    time.time(),
                    canonical(stages),
                    canonical(response) if response else None,
                    error,
                    identifier,
                ),
            )
            self._event(conn, identifier, row["attempt"], {"type": "finished", "status": status})

    def get_task(self, identifier: str, *, include_events=True) -> dict:
        with self._connect(write=True) as conn:
            self._recover_stale(conn)
            row = conn.execute("SELECT * FROM analysis_tasks WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise WorkspaceNotFound("分析任务不存在")
            events = (
                conn.execute(
                    "SELECT attempt,event,created_at FROM task_events WHERE task_id=? ORDER BY sequence DESC LIMIT 500",
                    (identifier,),
                ).fetchall()
                if include_events
                else []
            )
            event_count = conn.execute(
                "SELECT COUNT(*) FROM task_events WHERE task_id=?", (identifier,)
            ).fetchone()[0]
        return {
            "id": row["id"],
            "question": row["question"],
            "session_id": row["session_id"],
            "mode": row["mode"],
            "profile_ref": decode_record(row["profile_ref"]),
            "context_revision": row["context_revision"],
            "status": row["status"],
            "attempt": row["attempt"],
            "stages": decode_record(row["stages"]),
            "created_at": timestamp(row["created_at"]),
            "updated_at": timestamp(row["updated_at"]),
            "error": row["error"],
            "resumable": row["status"] in {"failed", "cancelled", "interrupted"},
            "resume_notice": "恢复会重新查询当前数据，不复用上次数字，并可能产生模型费用。",
            "response": decode_record(row["response"]) if row["response"] else None,
            "event_count": event_count,
            "events_complete": include_events and event_count <= 500,
            "events": [
                {
                    "attempt": event["attempt"],
                    "event": decode_record(event["event"]),
                    "created_at": timestamp(event["created_at"]),
                }
                for event in reversed(events)
            ],
        }

    def list_tasks(self, limit: int = 30) -> list[dict]:
        with self._connect(write=True) as conn:
            self._recover_stale(conn)
            identifiers = conn.execute(
                "SELECT id FROM analysis_tasks ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        tasks = [self.get_task(row["id"], include_events=False) for row in identifiers]
        return [
            {key: value for key, value in task.items() if key not in {"events", "response"}}
            for task in tasks
        ]
