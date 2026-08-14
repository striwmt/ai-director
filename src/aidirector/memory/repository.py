"""Media Memory repository — the only place that speaks SQL to the DB.

The AI Director never re-reads raw footage when this memory can answer
(AGENT.md §42, §78).
"""

from __future__ import annotations

import json
import sqlite3
import struct
import uuid
from pathlib import Path

from ..ai.schemas import Provenance, Transcript, VisionAnalysis
from ..color.profile import ColorProfile, ColorProfileDetection
from ..media.metadata import MediaMetadata
from .models import (
    AssetRecord,
    FrameRecord,
    ProjectRecord,
    SegmentRecord,
    TechnicalFeatures,
)


def _pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


class MediaMemory:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- projects --------------------------------------------------------

    def get_or_create_project(self, name: str, root_dir: Path) -> ProjectRecord:
        row = self.conn.execute(
            "SELECT * FROM projects WHERE root_dir = ?", (str(root_dir),)
        ).fetchone()
        if row:
            return ProjectRecord(id=row["id"], name=row["name"], root_dir=row["root_dir"])
        return self._create_project(name, root_dir)

    def _create_project(self, name: str, root_dir: Path) -> ProjectRecord:
        project = ProjectRecord(id=f"prj_{uuid.uuid4().hex[:12]}", name=name, root_dir=str(root_dir))
        self.conn.execute(
            "INSERT INTO projects (id, name, root_dir) VALUES (?, ?, ?)",
            (project.id, project.name, project.root_dir),
        )
        self.conn.commit()
        return project

    # -- assets ----------------------------------------------------------

    def find_asset_by_identity(
        self, project_id: str, partial_hash: str, size: int
    ) -> AssetRecord | None:
        row = self.conn.execute(
            "SELECT * FROM assets WHERE project_id = ? AND partial_hash = ? AND size = ?",
            (project_id, partial_hash, size),
        ).fetchone()
        return self._asset_from_row(row) if row else None

    def upsert_asset(self, asset: AssetRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO assets
                (id, project_id, path, file_name, kind, size, mtime, partial_hash,
                 full_hash, duration, metadata_json, sidecar_paths_json, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                path = excluded.path,
                file_name = excluded.file_name,
                mtime = excluded.mtime,
                duration = excluded.duration,
                metadata_json = excluded.metadata_json,
                sidecar_paths_json = excluded.sidecar_paths_json,
                status = excluded.status,
                error = excluded.error
            """,
            (
                asset.id, asset.project_id, asset.path, asset.file_name, asset.kind,
                asset.size, asset.mtime, asset.partial_hash, asset.full_hash,
                asset.duration, asset.metadata.model_dump_json(),
                json.dumps(asset.sidecar_paths), asset.status, asset.error,
            ),
        )
        self.conn.commit()

    def rename_project(self, project_id: str, name: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE projects SET name = ? WHERE id = ?", (name, project_id)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def set_asset_status(self, asset_id: str, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE assets SET status = ?, error = ? WHERE id = ?",
            (status, error, asset_id),
        )
        self.conn.commit()

    def _asset_from_row(self, row: sqlite3.Row) -> AssetRecord:
        return AssetRecord(
            id=row["id"],
            project_id=row["project_id"],
            path=row["path"],
            file_name=row["file_name"],
            kind=row["kind"],
            size=row["size"],
            mtime=row["mtime"],
            partial_hash=row["partial_hash"],
            full_hash=row["full_hash"],
            duration=row["duration"],
            metadata=MediaMetadata.model_validate_json(row["metadata_json"]),
            sidecar_paths=json.loads(row["sidecar_paths_json"]),
            status=row["status"],
            error=row["error"],
        )

    def list_assets(self, project_id: str, kind: str | None = None) -> list[AssetRecord]:
        if kind:
            rows = self.conn.execute(
                "SELECT * FROM assets WHERE project_id = ? AND kind = ? ORDER BY file_name",
                (project_id, kind),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM assets WHERE project_id = ? ORDER BY file_name",
                (project_id,),
            ).fetchall()
        return [self._asset_from_row(r) for r in rows]

    def get_asset(self, asset_id: str) -> AssetRecord | None:
        row = self.conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return self._asset_from_row(row) if row else None

    # -- color -----------------------------------------------------------

    def save_color_profile(self, asset_id: str, detection: ColorProfileDetection) -> None:
        self.conn.execute(
            """
            INSERT INTO asset_color_profiles (asset_id, profile, confidence, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                profile = excluded.profile,
                confidence = excluded.confidence,
                source = excluded.source,
                detected_at = datetime('now')
            """,
            (asset_id, detection.profile.value, detection.confidence, detection.source),
        )
        self.conn.commit()

    def get_color_profile(self, asset_id: str) -> ColorProfileDetection | None:
        row = self.conn.execute(
            "SELECT * FROM asset_color_profiles WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if not row:
            return None
        return ColorProfileDetection(
            profile=ColorProfile(row["profile"]),
            confidence=row["confidence"],
            source=row["source"],
        )

    def save_color_transform(
        self,
        asset_id: str,
        purpose: str,
        transform_id: str | None,
        lut_hash: str | None,
        is_fallback: bool,
        proxy_path: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO color_transforms
                (asset_id, purpose, transform_id, lut_hash, is_fallback, proxy_path)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, purpose) DO UPDATE SET
                transform_id = excluded.transform_id,
                lut_hash = excluded.lut_hash,
                is_fallback = excluded.is_fallback,
                proxy_path = excluded.proxy_path,
                created_at = datetime('now')
            """,
            (asset_id, purpose, transform_id, lut_hash, int(is_fallback), proxy_path),
        )
        self.conn.commit()

    def get_analysis_proxy(self, asset_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT proxy_path FROM color_transforms WHERE asset_id = ? AND purpose = 'analysis'",
            (asset_id,),
        ).fetchone()
        return row["proxy_path"] if row else None

    def get_color_transform_id(self, asset_id: str, purpose: str = "analysis") -> str | None:
        row = self.conn.execute(
            "SELECT transform_id FROM color_transforms WHERE asset_id = ? AND purpose = ?",
            (asset_id, purpose),
        ).fetchone()
        return row["transform_id"] if row else None

    # -- segments ----------------------------------------------------------

    def replace_segments(self, asset_id: str, segments: list[SegmentRecord]) -> None:
        seg_ids = [
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM segments WHERE asset_id = ?", (asset_id,)
            ).fetchall()
        ]
        for seg_id in seg_ids:
            self.conn.execute("DELETE FROM frames WHERE segment_id = ?", (seg_id,))
            self.conn.execute("DELETE FROM technical_features WHERE segment_id = ?", (seg_id,))
            self.conn.execute("DELETE FROM semantic_annotations WHERE segment_id = ?", (seg_id,))
            self.conn.execute(
                "DELETE FROM embeddings WHERE owner_type = 'segment' AND owner_id = ?",
                (seg_id,),
            )
        self.conn.execute("DELETE FROM segments WHERE asset_id = ?", (asset_id,))
        for seg in segments:
            self.conn.execute(
                """
                INSERT INTO segments (id, asset_id, idx, start, end, boundary_reasons_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (seg.id, seg.asset_id, seg.idx, seg.start, seg.end,
                 json.dumps(seg.boundary_reasons)),
            )
        self.conn.commit()

    def list_segments(self, asset_id: str) -> list[SegmentRecord]:
        rows = self.conn.execute(
            "SELECT * FROM segments WHERE asset_id = ? ORDER BY idx", (asset_id,)
        ).fetchall()
        return [
            SegmentRecord(
                id=r["id"], asset_id=r["asset_id"], idx=r["idx"],
                start=r["start"], end=r["end"],
                boundary_reasons=json.loads(r["boundary_reasons_json"]),
            )
            for r in rows
        ]

    def get_segment(self, segment_id: str) -> SegmentRecord | None:
        r = self.conn.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
        if not r:
            return None
        return SegmentRecord(
            id=r["id"], asset_id=r["asset_id"], idx=r["idx"],
            start=r["start"], end=r["end"],
            boundary_reasons=json.loads(r["boundary_reasons_json"]),
        )

    def list_project_segments(self, project_id: str) -> list[SegmentRecord]:
        # Chronological where recording times exist (ISO strings sort
        # lexicographically); file name is the fallback ordering.
        rows = self.conn.execute(
            """
            SELECT s.* FROM segments s
            JOIN assets a ON a.id = s.asset_id
            WHERE a.project_id = ?
            ORDER BY COALESCE(json_extract(a.metadata_json, '$.creation_time'),
                              'zzz' || a.file_name),
                     a.file_name, s.idx
            """,
            (project_id,),
        ).fetchall()
        return [
            SegmentRecord(
                id=r["id"], asset_id=r["asset_id"], idx=r["idx"],
                start=r["start"], end=r["end"],
                boundary_reasons=json.loads(r["boundary_reasons_json"]),
            )
            for r in rows
        ]

    # -- frames ------------------------------------------------------------

    def add_frames(self, frames: list[FrameRecord]) -> None:
        for frame in frames:
            self.conn.execute(
                "INSERT INTO frames (segment_id, timestamp, path) VALUES (?, ?, ?)",
                (frame.segment_id, frame.timestamp, frame.path),
            )
        self.conn.commit()

    def list_frames(self, segment_id: str) -> list[FrameRecord]:
        rows = self.conn.execute(
            "SELECT * FROM frames WHERE segment_id = ? ORDER BY timestamp", (segment_id,)
        ).fetchall()
        return [
            FrameRecord(segment_id=r["segment_id"], timestamp=r["timestamp"], path=r["path"])
            for r in rows
        ]

    # -- transcripts ---------------------------------------------------------

    def save_transcript(
        self, asset_id: str, transcript: Transcript, provenance: Provenance
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO transcripts (asset_id, language, duration, transcript_json, provenance_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                language = excluded.language,
                duration = excluded.duration,
                transcript_json = excluded.transcript_json,
                provenance_json = excluded.provenance_json
            """,
            (
                asset_id, transcript.language, transcript.duration,
                transcript.model_dump_json(), provenance.model_dump_json(),
            ),
        )
        self.conn.commit()

    def get_transcript(self, asset_id: str) -> Transcript | None:
        row = self.conn.execute(
            "SELECT transcript_json FROM transcripts WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        return Transcript.model_validate_json(row["transcript_json"]) if row else None

    # -- technical features ---------------------------------------------------

    def save_technical_features(self, segment_id: str, features: TechnicalFeatures) -> None:
        self.conn.execute(
            """
            INSERT INTO technical_features (segment_id, features_json)
            VALUES (?, ?)
            ON CONFLICT(segment_id) DO UPDATE SET
                features_json = excluded.features_json,
                created_at = datetime('now')
            """,
            (segment_id, features.model_dump_json()),
        )
        self.conn.commit()

    def get_technical_features(self, segment_id: str) -> TechnicalFeatures | None:
        row = self.conn.execute(
            "SELECT features_json FROM technical_features WHERE segment_id = ?",
            (segment_id,),
        ).fetchone()
        return TechnicalFeatures.model_validate_json(row["features_json"]) if row else None

    # -- semantic annotations ---------------------------------------------------

    def save_semantic_annotation(
        self, segment_id: str, analysis: VisionAnalysis, provenance: Provenance
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO semantic_annotations (segment_id, analysis_json, provenance_json)
            VALUES (?, ?, ?)
            ON CONFLICT(segment_id) DO UPDATE SET
                analysis_json = excluded.analysis_json,
                provenance_json = excluded.provenance_json
            """,
            (segment_id, analysis.model_dump_json(), provenance.model_dump_json()),
        )
        self.conn.commit()

    def get_semantic_annotation(self, segment_id: str) -> VisionAnalysis | None:
        row = self.conn.execute(
            "SELECT analysis_json FROM semantic_annotations WHERE segment_id = ?",
            (segment_id,),
        ).fetchone()
        return VisionAnalysis.model_validate_json(row["analysis_json"]) if row else None

    # -- embeddings -----------------------------------------------------------

    def save_embedding(
        self, owner_type: str, owner_id: str, kind: str, model: str, vector: list[float]
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO embeddings (owner_type, owner_id, kind, model, dim, vector)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_type, owner_id, kind, model) DO UPDATE SET
                dim = excluded.dim,
                vector = excluded.vector,
                created_at = datetime('now')
            """,
            (owner_type, owner_id, kind, model, len(vector), _pack_vector(vector)),
        )
        self.conn.commit()

    def get_embedding(
        self, owner_type: str, owner_id: str, kind: str, model: str
    ) -> list[float] | None:
        row = self.conn.execute(
            """
            SELECT vector FROM embeddings
            WHERE owner_type = ? AND owner_id = ? AND kind = ? AND model = ?
            """,
            (owner_type, owner_id, kind, model),
        ).fetchone()
        return _unpack_vector(row["vector"]) if row else None

    def iter_segment_embeddings(
        self, project_id: str, kind: str = "text"
    ) -> list[tuple[str, list[float]]]:
        rows = self.conn.execute(
            """
            SELECT e.owner_id, e.vector FROM embeddings e
            JOIN segments s ON s.id = e.owner_id
            JOIN assets a ON a.id = s.asset_id
            WHERE e.owner_type = 'segment' AND e.kind = ? AND a.project_id = ?
            """,
            (kind, project_id),
        ).fetchall()
        return [(r["owner_id"], _unpack_vector(r["vector"])) for r in rows]

    # -- director runs / edit plans ---------------------------------------------

    def create_director_run(self, project_id: str, intent: dict) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            "INSERT INTO director_runs (id, project_id, intent_json) VALUES (?, ?, ?)",
            (run_id, project_id, json.dumps(intent, ensure_ascii=False)),
        )
        self.conn.commit()
        return run_id

    def finish_director_run(self, run_id: str, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE director_runs SET status = ?, error = ? WHERE id = ?",
            (status, error, run_id),
        )
        self.conn.commit()

    def save_edit_plan(
        self, run_id: str, plan_json: str, version: int = 1, name: str | None = None
    ) -> str:
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            "INSERT INTO edit_plans (id, run_id, version, plan_json, name) "
            "VALUES (?, ?, ?, ?, ?)",
            (plan_id, run_id, version, plan_json, name),
        )
        clips = json.loads(plan_json).get("clips", [])
        for idx, clip in enumerate(clips):
            self.conn.execute(
                "INSERT INTO edit_decisions (plan_id, idx, decision_json) VALUES (?, ?, ?)",
                (plan_id, idx, json.dumps(clip, ensure_ascii=False)),
            )
        self.conn.commit()
        return plan_id

    def rename_plan(self, plan_id: str, name: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE edit_plans SET name = ? WHERE id = ?", (name, plan_id)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_plan_name(self, plan_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT name FROM edit_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        return row["name"] if row else None

    def get_edit_plan(self, plan_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT plan_json FROM edit_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        return row["plan_json"] if row else None

    def latest_edit_plan(self, project_id: str) -> tuple[str, str] | None:
        """Return (plan_id, plan_json) of the newest plan in the project."""
        row = self.conn.execute(
            """
            SELECT p.id, p.plan_json FROM edit_plans p
            JOIN director_runs r ON r.id = p.run_id
            WHERE r.project_id = ?
            ORDER BY p.created_at DESC, p.rowid DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        return (row["id"], row["plan_json"]) if row else None

    def add_user_feedback(
        self, plan_id: str, action: str, decision_idx: int | None = None,
        reason: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO user_feedback (plan_id, decision_idx, action, reason) VALUES (?, ?, ?, ?)",
            (plan_id, decision_idx, action, reason),
        )
        self.conn.commit()
