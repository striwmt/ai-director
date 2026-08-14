"""Library-neutral AI data schemas.

These are the ONLY shapes that cross the provider boundary; no
transformers/faster-whisper objects may leak past providers (AGENT.md §23/§76).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Chat / director


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


# ---------------------------------------------------------------------------
# Vision


class ImageInput(BaseModel):
    path: Path
    timestamp: float | None = None  # seconds within the source segment


class VisionContext(BaseModel):
    """Non-visual context handed to the VLM alongside frames."""

    segment_id: str | None = None
    asset_name: str | None = None
    recorded_at: str | None = None
    duration: float | None = None
    transcript_excerpt: str | None = None
    hints: list[str] = Field(default_factory=list)


class VisionAnalysis(BaseModel):
    description: str

    subjects: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)

    camera_motion: str | None = None

    notable_events: list[str] = Field(default_factory=list)

    story_roles: list[str] = Field(default_factory=list)
    narrative_values: list[str] = Field(default_factory=list)

    @field_validator(
        "subjects", "actions", "mood", "notable_events",
        "story_roles", "narrative_values",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, value):
        # VLMs frequently emit "calm, serene, urban" instead of an array;
        # tolerate that instead of discarding the whole analysis.
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


# ---------------------------------------------------------------------------
# Speech


class TranscriptionOptions(BaseModel):
    language: str | None = None  # None = auto-detect
    word_timestamps: bool = True
    vad: bool = True
    beam_size: int = 5
    # False curbs hallucinated loops on music/instrumental input.
    condition_on_previous_text: bool = True


class TranscriptWord(BaseModel):
    start: float
    end: float
    text: str
    probability: float | None = None


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    words: list[TranscriptWord] = Field(default_factory=list)


class Transcript(BaseModel):
    language: str
    duration: float
    segments: list[TranscriptSegment] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()


# ---------------------------------------------------------------------------
# Embedding


class Embedding(BaseModel):
    vector: list[float]
    model: str = ""

    @property
    def dim(self) -> int:
        return len(self.vector)


class MultimodalEmbeddingInput(BaseModel):
    text: str | None = None
    images: list[Path] = Field(default_factory=list)
    video: Path | None = None


# ---------------------------------------------------------------------------
# Provenance (AGENT.md §44) — attached to every AI-generated record.


class Provenance(BaseModel):
    provider: str
    model: str
    model_version: str | None = None
    prompt_version: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    # Vision only: which color transform produced the frames the model saw.
    analysis_color_transform: str | None = None
    # ASR only:
    options: dict | None = None
    language: str | None = None
