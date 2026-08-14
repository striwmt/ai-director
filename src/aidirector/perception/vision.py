"""Vision perception: run the VLM over segment frames and persist results.

Frames come from the color-managed analysis proxy (AGENT.md §13/§29); the
transform used is recorded in provenance (§44).
"""

from __future__ import annotations

from ..ai.schemas import ImageInput, Provenance, VisionAnalysis, VisionContext
from ..ai.services import AIServices
from ..logging import get_logger
from ..memory.models import AssetRecord, FrameRecord, SegmentRecord
from ..memory.repository import MediaMemory

log = get_logger("perception.vision")


async def analyze_segment_vision(
    asset: AssetRecord,
    segment: SegmentRecord,
    frames: list[FrameRecord],
    transcript_excerpt: str,
    ai: AIServices,
    memory: MediaMemory,
    *,
    force: bool = False,
) -> VisionAnalysis | None:
    if not force:
        cached = memory.get_semantic_annotation(segment.id)
        if cached is not None:
            return cached
    if not frames:
        log.warning("segment %s has no frames; skipping vision", segment.id)
        return None

    from pathlib import Path

    images = [ImageInput(path=Path(f.path), timestamp=f.timestamp) for f in frames]
    context = VisionContext(
        segment_id=segment.id,
        asset_name=asset.file_name,
        recorded_at=asset.metadata.creation_time,
        duration=segment.duration,
        transcript_excerpt=transcript_excerpt or None,
    )
    analysis = await ai.understand_segment(images, context)

    provider_name = ai.provider_name("vision")
    provider = ai.runtime._active.get("vision")  # noqa: SLF001
    provenance = Provenance(
        provider=provider_name,
        model=provider_name.split(":", 1)[-1],
        prompt_version=getattr(provider, "prompt_version", None),
        analysis_color_transform=memory.get_color_transform_id(asset.id, "analysis"),
    )
    memory.save_semantic_annotation(segment.id, analysis, provenance)
    return analysis
