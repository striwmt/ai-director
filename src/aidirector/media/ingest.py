"""Media ingest: scan, identify, probe, persist (AGENT.md §8-§10).

Asset identity is content-based (size + mtime + partial hash), never the
file path alone. One corrupt file never aborts the whole ingest (§66).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ..color.detect import ColorProfileDetector
from ..color.profile import ColorProfile
from ..config import AppConfig
from ..errors import AIDirectorError
from ..logging import get_logger
from ..memory.models import AssetRecord
from ..memory.repository import MediaMemory
from .metadata import MediaMetadata, extract_metadata
from .probe import probe_file

log = get_logger("media.ingest")


def compute_partial_hash(path: Path, chunk_bytes: int = 65536) -> str:
    """Hash of the first and last chunk — cheap but positionally robust."""
    size = path.stat().st_size
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        digest.update(fh.read(chunk_bytes))
        if size > chunk_bytes * 2:
            fh.seek(-chunk_bytes, 2)
            digest.update(fh.read(chunk_bytes))
    digest.update(str(size).encode())
    return digest.hexdigest()


def compute_full_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_asset_id(partial_hash: str, size: int) -> str:
    return "ast_" + hashlib.sha1(f"{partial_hash}:{size}".encode()).hexdigest()[:16]


def make_music_id(partial_hash: str, size: int) -> str:
    """Content key for the global music-library cache (rename-safe)."""
    return "mus_" + hashlib.sha1(f"{partial_hash}:{size}".encode()).hexdigest()[:16]


@dataclass
class IngestReport:
    ingested: list[str] = field(default_factory=list)
    skipped_unchanged: list[str] = field(default_factory=list)
    sidecars: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def _classify(path: Path, config: AppConfig) -> str | None:
    suffix = path.suffix.lower()
    ingest = config.ingest
    if suffix in ingest.video_extensions:
        return "video"
    if suffix in ingest.audio_extensions:
        return "audio"
    if suffix in ingest.image_extensions:
        return "image"
    if suffix in ingest.sidecar_extensions:
        return "sidecar"
    return None


def scan_directory(root: Path, config: AppConfig) -> dict[str, list[Path]]:
    """Classify every file under root by kind. Hidden files are skipped."""
    found: dict[str, list[Path]] = {"video": [], "audio": [], "image": [], "sidecar": []}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        kind = _classify(path, config)
        if kind:
            found[kind].append(path)
    return found


def _match_sidecars(video: Path, sidecars: list[Path]) -> list[Path]:
    """DJI pairs DJI_0042.MP4 with DJI_0042.LRF — match on stem."""
    stem = video.stem.lower()
    return [s for s in sidecars if s.stem.lower() == stem]


def ingest_directory(
    root: Path,
    config: AppConfig,
    memory: MediaMemory,
    detector: ColorProfileDetector,
    *,
    color_override: ColorProfile | None = None,
    project_name: str | None = None,
) -> IngestReport:
    root = root.resolve()
    if not root.is_dir():
        raise AIDirectorError(f"footage directory not found: {root}")

    project = memory.get_or_create_project(project_name or root.name, root)
    found = scan_directory(root, config)
    report = IngestReport(sidecars=[str(p) for p in found["sidecar"]])

    for kind in ("video", "audio", "image"):
        for path in found[kind]:
            try:
                self_ingest_one(
                    path, kind, project.id, config, memory, detector,
                    sidecars=found["sidecar"], color_override=color_override,
                    report=report,
                )
            except Exception as exc:
                log.error("ingest failed for %s: %s", path.name, exc)
                report.failed[str(path)] = str(exc)
    log.info(
        "ingest done: %d new/updated, %d unchanged, %d failed",
        len(report.ingested), len(report.skipped_unchanged), len(report.failed),
    )
    return report


def self_ingest_one(
    path: Path,
    kind: str,
    project_id: str,
    config: AppConfig,
    memory: MediaMemory,
    detector: ColorProfileDetector,
    *,
    sidecars: list[Path],
    color_override: ColorProfile | None,
    report: IngestReport,
) -> AssetRecord:
    stat = path.stat()
    partial = compute_partial_hash(path, config.ingest.partial_hash_bytes)
    asset_id = make_asset_id(partial, stat.st_size)

    existing = memory.find_asset_by_identity(project_id, partial, stat.st_size)
    if existing is not None and existing.mtime == stat.st_mtime:
        # Unchanged content — incremental processing reuses everything (§45).
        # A user override still updates the stored color profile.
        if color_override is not None:
            detection = detector.detect(existing.metadata, path.name, override=color_override)
            memory.save_color_profile(existing.id, detection)
        report.skipped_unchanged.append(str(path))
        return existing

    probe = probe_file(path) if kind in ("video", "audio") else None
    metadata = extract_metadata(probe, path) if probe else None

    linked = _match_sidecars(path, sidecars) if kind == "video" else []

    asset = AssetRecord(
        id=asset_id,
        project_id=project_id,
        path=str(path),
        file_name=path.name,
        kind=kind,  # type: ignore[arg-type]
        size=stat.st_size,
        mtime=stat.st_mtime,
        partial_hash=partial,
        duration=metadata.duration if metadata else None,
        metadata=metadata or MediaMetadata(),
        sidecar_paths=[str(s) for s in linked],
        status="ingested",
    )
    memory.upsert_asset(asset)

    if kind == "video" and metadata is not None:
        detection = detector.detect(metadata, path.name, override=color_override)
        memory.save_color_profile(asset_id, detection)
        log.info(
            "ingested %s [%s, %.2f confidence, %s]",
            path.name, detection.profile.value, detection.confidence, detection.source,
        )
    else:
        log.info("ingested %s [%s]", path.name, kind)

    report.ingested.append(str(path))
    return asset
