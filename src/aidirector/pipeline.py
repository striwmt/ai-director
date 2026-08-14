"""Analysis pipeline orchestration.

Phase execution for single-GPU machines (AGENT.md §38): deterministic work
first, then Whisper phase, then VLM phase, then embedding phase — each model
loaded once, results persisted to Media Memory, model unloaded.

AI stages degrade gracefully: if a provider is unavailable the pipeline
logs, skips that stage and continues (§66).
"""

from __future__ import annotations

from pathlib import Path

from .ai.services import AIServices
from .color.detect import ColorProfileDetector
from .color.profile import ColorProfile
from .color.registry import ColorTransformRegistry
from .config import AppConfig, ensure_dirs
from .logging import get_logger
from .media.frames import extract_frames
from .media.ingest import IngestReport, ingest_directory
from .media.proxy import generate_analysis_proxy
from .media.segment import segment_video
from .memory.models import AssetRecord
from .memory.repository import MediaMemory
from .perception.embeddings import embed_segments
from .perception.interpretation import SegmentUnderstanding, build_understanding
from .perception.speech import segment_transcripts, transcribe_asset
from .perception.technical import analyze_segment_technical
from .perception.vision import analyze_segment_vision

log = get_logger("pipeline")


_BUNDLED_PROFILES = Path(__file__).parent / "color" / "data" / "color_profiles.yaml"


def _profiles_file(config: AppConfig, project_root: Path) -> Path:
    """User-editable config/color_profiles.yaml wins; bundled copy is the fallback."""
    profiles_file = config.color.profiles_file
    if not profiles_file.is_absolute():
        profiles_file = project_root / profiles_file
    return profiles_file if profiles_file.is_file() else _BUNDLED_PROFILES


def make_detector(config: AppConfig, project_root: Path) -> ColorProfileDetector:
    return ColorProfileDetector.from_yaml(
        _profiles_file(config, project_root), config.color.min_confidence
    )


def make_registry(config: AppConfig, project_root: Path) -> ColorTransformRegistry:
    return ColorTransformRegistry.from_yaml(
        _profiles_file(config, project_root),
        luts_dir=config.color.luts_dir,
        base_dir=project_root,
    )


def run_ingest(
    footage: Path,
    config: AppConfig,
    memory: MediaMemory,
    project_root: Path,
    *,
    color_override: ColorProfile | None = None,
) -> IngestReport:
    ensure_dirs(config)
    detector = make_detector(config, project_root)
    return ingest_directory(
        footage, config, memory, detector, color_override=color_override
    )


def prepare_asset(
    asset: AssetRecord,
    config: AppConfig,
    memory: MediaMemory,
    registry: ColorTransformRegistry,
) -> Path | None:
    """Deterministic per-asset work: proxy, segments, frames, technical CV.

    Returns the analysis proxy path, or None if the asset was skipped/failed.
    """
    if asset.kind != "video":
        return None
    try:
        detection = memory.get_color_profile(asset.id)
        profile = detection.profile if detection else ColorProfile.UNKNOWN

        proxy_path, _color = generate_analysis_proxy(
            asset, profile, registry, config, memory
        )

        segments = memory.list_segments(asset.id)
        if not segments:
            duration = asset.duration or 0.0
            segments = segment_video(
                asset.id, proxy_path, duration, config.segmentation
            )
            memory.replace_segments(asset.id, segments)

        for segment in segments:
            if not memory.list_frames(segment.id):
                frames = extract_frames(segment, proxy_path, config)
                memory.add_frames(frames)
            if memory.get_technical_features(segment.id) is None:
                features = analyze_segment_technical(
                    segment, proxy_path,
                    has_audio=asset.metadata.has_audio,
                    silence_noise_db=config.segmentation.silence_noise_db,
                )
                memory.save_technical_features(segment.id, features)
        return proxy_path
    except Exception as exc:
        log.error("prepare failed for %s: %s", asset.file_name, exc)
        memory.set_asset_status(asset.id, "failed", str(exc))
        return None


async def run_analyze(
    footage: Path,
    config: AppConfig,
    memory: MediaMemory,
    ai: AIServices,
    project_root: Path,
    *,
    color_override: ColorProfile | None = None,
) -> str:
    """Full analysis: ingest + deterministic prep + phased AI passes.

    Returns the project id.
    """
    run_ingest(
        footage, config, memory, project_root, color_override=color_override
    )
    project = memory.get_or_create_project(footage.resolve().name, footage.resolve())
    registry = make_registry(config, project_root)

    assets = memory.list_assets(project.id, kind="video")

    # -- Phase A: deterministic (no AI) --------------------------------
    prepared: dict[str, Path] = {}
    for asset in assets:
        proxy = prepare_asset(asset, config, memory, registry)
        if proxy is not None:
            prepared[asset.id] = proxy

    # -- Phase B: speech (Whisper loaded once) --------------------------
    try:
        for asset in assets:
            if asset.id not in prepared:
                continue
            if asset.metadata.has_audio:
                await transcribe_asset(asset, ai, config, memory)
        await ai.runtime.release("speech")
    except Exception as exc:
        log.warning("speech phase skipped: %s", exc)

    # -- Phase C: vision (VLM loaded once) ------------------------------
    try:
        for asset in assets:
            if asset.id not in prepared:
                continue
            transcript = memory.get_transcript(asset.id)
            segments = memory.list_segments(asset.id)
            excerpts = segment_transcripts(transcript, segments)
            for segment in segments:
                frames = memory.list_frames(segment.id)
                await analyze_segment_vision(
                    asset, segment, frames, excerpts.get(segment.id, ""),
                    ai, memory,
                )
        await ai.runtime.release("vision")
    except Exception as exc:
        log.warning("vision phase skipped: %s", exc)

    # -- Phase D: embeddings --------------------------------------------
    try:
        understandings: list[SegmentUnderstanding] = [
            build_understanding(segment, memory)
            for segment in memory.list_project_segments(project.id)
        ]
        await embed_segments(understandings, ai, memory)
        await ai.runtime.release("embedding")
    except Exception as exc:
        log.warning("embedding phase skipped: %s", exc)

    for asset in assets:
        if asset.id in prepared:
            memory.set_asset_status(asset.id, "analyzed")

    await ai.runtime.release_all()
    log.info("analysis complete for project %s (%d assets)", project.id, len(prepared))
    return project.id
