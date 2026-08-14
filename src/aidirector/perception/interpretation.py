"""Interpretation: compose per-segment structured understanding.

This joins signal-layer facts, transcript and VLM output into the
human-readable structured record that Media Memory serves to the Director
(AGENT.md §20, Phase 2 deliverable).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from ..ai.schemas import VisionAnalysis
from ..memory.models import SegmentRecord, TechnicalFeatures
from ..memory.repository import MediaMemory


def parse_creation_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def segment_recorded_at(creation_time: str | None, offset_seconds: float) -> str | None:
    """Absolute wall-clock time a segment starts at, if the asset is dated."""
    start = parse_creation_time(creation_time)
    if start is None:
        return None
    return (start + timedelta(seconds=offset_seconds)).isoformat()


class SegmentUnderstanding(BaseModel):
    """Everything the Director may need to know about one segment."""

    segment_id: str
    asset_id: str
    asset_name: str
    start: float
    end: float
    duration: float
    recorded_at: str | None = None  # absolute time this segment starts, if known
    orientation: str | None = None  # portrait | landscape

    description: str = ""
    subjects: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    camera_motion: str | None = None
    notable_events: list[str] = Field(default_factory=list)
    story_roles: list[str] = Field(default_factory=list)
    narrative_values: list[str] = Field(default_factory=list)

    transcript: str = ""
    quality_flags: list[str] = Field(default_factory=list)
    sharpness: float | None = None
    loudness_lufs: float | None = None
    silence_ratio: float | None = None

    def to_search_text(self) -> str:
        """Text used for embedding / keyword retrieval."""
        parts = [
            self.description,
            " ".join(self.subjects),
            " ".join(self.actions),
            " ".join(self.mood),
            " ".join(self.notable_events),
            self.transcript,
        ]
        return "\n".join(p for p in parts if p).strip()

    def to_summary_line(self) -> str:
        """One-line summary for director prompts."""
        bits = [f"[{self.segment_id}] {self.start:.1f}-{self.end:.1f}s ({self.duration:.1f}s)"]
        if self.recorded_at:
            bits.append(f"shot at {self.recorded_at[:16].replace('T', ' ')}")
        if self.orientation == "portrait":
            bits.append("PORTRAIT (vertical)")
        if self.description:
            bits.append(self.description)
        if self.mood:
            bits.append("mood: " + ", ".join(self.mood[:4]))
        if self.transcript:
            excerpt = self.transcript[:120]
            bits.append(f'speech: "{excerpt}"')
        if self.quality_flags:
            bits.append("issues: " + ",".join(self.quality_flags))
        return " | ".join(bits)


def build_understanding(
    segment: SegmentRecord,
    memory: MediaMemory,
) -> SegmentUnderstanding:
    asset = memory.get_asset(segment.asset_id)
    analysis: VisionAnalysis | None = memory.get_semantic_annotation(segment.id)
    features: TechnicalFeatures | None = memory.get_technical_features(segment.id)
    transcript = memory.get_transcript(segment.asset_id)

    from .speech import transcript_for_span

    orientation = None
    if asset and asset.metadata.display_size:
        orientation = "portrait" if asset.metadata.is_portrait else "landscape"

    understanding = SegmentUnderstanding(
        segment_id=segment.id,
        asset_id=segment.asset_id,
        asset_name=asset.file_name if asset else "",
        start=segment.start,
        end=segment.end,
        duration=segment.duration,
        recorded_at=segment_recorded_at(
            asset.metadata.creation_time if asset else None, segment.start
        ),
        orientation=orientation,
        transcript=transcript_for_span(transcript, segment.start, segment.end),
    )
    if analysis:
        understanding.description = analysis.description
        understanding.subjects = analysis.subjects
        understanding.actions = analysis.actions
        understanding.mood = analysis.mood
        understanding.camera_motion = analysis.camera_motion
        understanding.notable_events = analysis.notable_events
        understanding.story_roles = analysis.story_roles
        understanding.narrative_values = analysis.narrative_values
    if features:
        understanding.quality_flags = features.quality_flags
        understanding.sharpness = features.sharpness
        understanding.loudness_lufs = features.loudness_lufs
        understanding.silence_ratio = features.silence_ratio
    return understanding
