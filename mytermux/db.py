"""SQLite storage for my-termux — sessions, goals, tasks, logs, repairs."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .paths import DB_FILE, HOME

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    project TEXT,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER,
    project TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 3,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(goal_id) REFERENCES goals(id)
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    path TEXT NOT NULL,
    kind TEXT,
    notes TEXT,
    last_opened TEXT,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: Optional[Path] = None):
    path = db_path or DB_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def log(level: str, source: str, message: str) -> None:
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO logs(level, source, message, created_at) VALUES (?,?,?,?)",
                (level, source, message, now_iso()),
            )
    except Exception:
        # never crash on logging
        pass


def start_session(project: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO sessions(started_at, project) VALUES (?,?)",
            (now_iso(), project),
        )
        return int(cur.lastrowid)


def end_session(session_id: int, summary: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at=?, summary=? WHERE id=?",
            (now_iso(), summary, session_id),
        )


def add_message(session_id: int, role: str, content: str, model: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO messages(session_id, role, content, model, created_at) VALUES (?,?,?,?,?)",
            (session_id, role, content, model, now_iso()),
        )


def get_last_session() -> Optional[sqlite3.Row]:
    with connect() as conn:
        cur = conn.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 1")
        return cur.fetchone()


def get_session_messages(session_id: int) -> list[sqlite3.Row]:
    with connect() as conn:
        cur = conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        )
        return list(cur.fetchall())


def add_goal(title: str, project: str = "") -> int:
    with connect() as conn:
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO goals(project, title, created_at, updated_at) VALUES (?,?,?,?)",
            (project, title, ts, ts),
        )
        return int(cur.lastrowid)


def add_task(title: str, project: str = "", goal_id: Optional[int] = None, priority: int = 3) -> int:
    with connect() as conn:
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO tasks(goal_id, project, title, priority, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (goal_id, project, title, priority, ts, ts),
        )
        return int(cur.lastrowid)


def list_pending_tasks(project: str = "", limit: int = 10) -> list[sqlite3.Row]:
    with connect() as conn:
        if project:
            cur = conn.execute(
                "SELECT * FROM tasks WHERE status='pending' AND project=? ORDER BY priority ASC, id ASC LIMIT ?",
                (project, limit),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM tasks WHERE status='pending' ORDER BY priority ASC, id ASC LIMIT ?",
                (limit,),
            )
        return list(cur.fetchall())


def complete_task(task_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE tasks SET status='done', updated_at=? WHERE id=?",
            (now_iso(), task_id),
        )


def add_repair(action: str, status: str, details: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO repairs(action, status, details, created_at) VALUES (?,?,?,?)",
            (action, status, details, now_iso()),
        )


def recent_repairs(limit: int = 10) -> list[sqlite3.Row]:
    with connect() as conn:
        cur = conn.execute(
            "SELECT * FROM repairs ORDER BY id DESC LIMIT ?", (limit,)
        )
        return list(cur.fetchall())


def upsert_project(name: str, path: str, kind: str = "", notes: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects(name, path, kind, notes, last_opened, created_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 path=excluded.path,
                 kind=COALESCE(NULLIF(excluded.kind,''), projects.kind),
                 notes=COALESCE(NULLIF(excluded.notes,''), projects.notes),
                 last_opened=excluded.last_opened""",
            (name, path, kind, notes, now_iso(), now_iso()),
        )


def list_projects() -> list[sqlite3.Row]:
    with connect() as conn:
        cur = conn.execute("SELECT * FROM projects ORDER BY last_opened DESC")
        return list(cur.fetchall())


# --------------------------------------------------------------------------
# "since you were last here" — used by the startup digest.
# created_at is ISO-8601 UTC with second precision, so lexicographic string
# comparison is a valid chronological comparison.
# --------------------------------------------------------------------------

def count_since(table: str, since: str, where: str = "") -> int:
    """Count rows in a timestamped table created/updated after `since`.

    `table` is validated against a whitelist: it is interpolated because SQLite
    cannot parameterise identifiers, so it must never come from user input.
    """
    allowed = {"sessions": "started_at", "messages": "created_at",
               "tasks": "updated_at", "goals": "created_at", "logs": "created_at"}
    if table not in allowed:
        return 0
    sql = f"SELECT COUNT(*) FROM {table} WHERE {allowed[table]} > ?"
    params: list = [since]
    if where:
        sql += f" AND {where}"
    try:
        with connect() as conn:
            return int(conn.execute(sql, params).fetchone()[0])
    except sqlite3.Error:
        return 0


def last_message(role: str = "") -> Optional[sqlite3.Row]:
    """Most recent message, optionally filtered by role."""
    try:
        with connect() as conn:
            if role:
                cur = conn.execute(
                    "SELECT * FROM messages WHERE role=? ORDER BY id DESC LIMIT 1",
                    (role,))
            else:
                cur = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 1")
            return cur.fetchone()
    except sqlite3.Error:
        return None


def totals() -> Dict[str, int]:
    """Lifetime counts, for the dashboard's workspace row."""
    out = {}
    for key, sql in (
        ("sessions", "SELECT COUNT(*) FROM sessions"),
        ("messages", "SELECT COUNT(*) FROM messages"),
        ("tasks_pending", "SELECT COUNT(*) FROM tasks WHERE status='pending'"),
        ("tasks_done", "SELECT COUNT(*) FROM tasks WHERE status='done'"),
        ("goals", "SELECT COUNT(*) FROM goals"),
        ("projects", "SELECT COUNT(*) FROM projects"),
    ):
        try:
            with connect() as conn:
                out[key] = int(conn.execute(sql).fetchone()[0])
        except sqlite3.Error:
            out[key] = 0
    return out

