"""Versioned schema migrations. All SQL DDL lives here (AGENT.md §75)."""

from __future__ import annotations

import sqlite3

_MIGRATIONS: list[str] = [
    # v1 — initial schema (AGENT.md §43)
    """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        root_dir TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE assets (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        path TEXT NOT NULL,
        file_name TEXT NOT NULL,
        kind TEXT NOT NULL,               -- video | audio | image
        size INTEGER NOT NULL,
        mtime REAL NOT NULL,
        partial_hash TEXT NOT NULL,
        full_hash TEXT,
        duration REAL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        sidecar_paths_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'ingested',   -- ingested | analyzed | failed
        error TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_assets_project ON assets(project_id);
    CREATE UNIQUE INDEX idx_assets_identity ON assets(project_id, partial_hash, size);

    CREATE TABLE asset_color_profiles (
        asset_id TEXT PRIMARY KEY REFERENCES assets(id),
        profile TEXT NOT NULL,
        confidence REAL NOT NULL,
        source TEXT NOT NULL,             -- auto | user | sidecar
        detected_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE color_transforms (
        asset_id TEXT NOT NULL REFERENCES assets(id),
        purpose TEXT NOT NULL,            -- analysis | preview
        transform_id TEXT,
        lut_hash TEXT,
        is_fallback INTEGER NOT NULL DEFAULT 0,
        proxy_path TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (asset_id, purpose)
    );

    CREATE TABLE segments (
        id TEXT PRIMARY KEY,
        asset_id TEXT NOT NULL REFERENCES assets(id),
        idx INTEGER NOT NULL,
        start REAL NOT NULL,
        end REAL NOT NULL,
        boundary_reasons_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_segments_asset ON segments(asset_id);

    CREATE TABLE frames (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        segment_id TEXT NOT NULL REFERENCES segments(id),
        timestamp REAL NOT NULL,
        path TEXT NOT NULL
    );
    CREATE INDEX idx_frames_segment ON frames(segment_id);

    CREATE TABLE transcripts (
        asset_id TEXT PRIMARY KEY REFERENCES assets(id),
        language TEXT NOT NULL,
        duration REAL NOT NULL,
        transcript_json TEXT NOT NULL,
        provenance_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE technical_features (
        segment_id TEXT PRIMARY KEY REFERENCES segments(id),
        features_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE semantic_annotations (
        segment_id TEXT PRIMARY KEY REFERENCES segments(id),
        analysis_json TEXT NOT NULL,
        provenance_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE embeddings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_type TEXT NOT NULL,         -- segment | asset
        owner_id TEXT NOT NULL,
        kind TEXT NOT NULL,               -- text | image | video
        model TEXT NOT NULL,
        dim INTEGER NOT NULL,
        vector BLOB NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX idx_embeddings_owner ON embeddings(owner_type, owner_id, kind, model);

    CREATE TABLE director_runs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        intent_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',   -- running | done | failed
        error TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE edit_plans (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES director_runs(id),
        version INTEGER NOT NULL,
        plan_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE edit_decisions (
        plan_id TEXT NOT NULL REFERENCES edit_plans(id),
        idx INTEGER NOT NULL,
        decision_json TEXT NOT NULL,
        PRIMARY KEY (plan_id, idx)
    );

    CREATE TABLE user_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id TEXT NOT NULL REFERENCES edit_plans(id),
        decision_idx INTEGER,
        action TEXT NOT NULL,             -- accept | reject | trim | extend | shorten | reorder | replace
        reason TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
]


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] or 0
    for version, script in enumerate(_MIGRATIONS, start=1):
        if version > current:
            conn.executescript(script)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.commit()
