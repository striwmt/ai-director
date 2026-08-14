"""SQLite connection management for Media Memory (AGENT.md §42)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .migrations import apply_migrations


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    apply_migrations(conn)
    return conn
