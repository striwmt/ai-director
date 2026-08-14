"""SQLite connection management for Media Memory (AGENT.md §42)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .migrations import apply_migrations


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the web layer resolves a connection in one
    # threadpool thread and may use it from another — always sequentially
    # within a single request, which SQLite allows.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    apply_migrations(conn)
    return conn
