"""Pydantic row models for Media Memory."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..ai.schemas import Provenance, Transcript, VisionAnalysis
from ..color.profile import ColorProfileDetection
from ..media.metadata import MediaMetadata

AssetKind = Literal["video", "audio", "image"]


class ProjectRecord(BaseModel):
    id: str
    name: str
    root_dir: str


class AssetRecord(BaseModel):
    id: str
    project_id: str
    path: str
    file_name: str
    kind: AssetKind
    size: int
    mtime: float
    partial_hash: str
    full_hash: str | None = None
    duration: float | None = None
    metadata: MediaMetadata = MediaMetadata()
    sidecar_paths: list[str] = Field(default_factory=list)
    status: str = "ingested"
    error: str | None = None


class SegmentRecord(BaseModel):
    id: str
    asset_id: str
    idx: int
    start: float
    end: float
    boundary_reasons: list[str] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


class FrameRecord(BaseModel):
    segment_id: str
    timestamp: float
    path: str


class TechnicalFeatures(BaseModel):
    """Deterministic signal-layer measurements (AGENT.md §21)."""

    mean_luma: float | None = None
    luma_stddev: float | None = None
    clipped_highlight_ratio: float | None = None
    clipped_shadow_ratio: float | None = None
    sharpness: float | None = None
    loudness_lufs: float | None = None
    silence_ratio: float | None = None
    speech_likely: bool | None = None
    quality_flags: list[str] = Field(default_factory=list)  # e.g. over_exposed, silent


class TranscriptRow(BaseModel):
    asset_id: str
    transcript: Transcript
    provenance: Provenance


class SemanticAnnotationRow(BaseModel):
    segment_id: str
    analysis: VisionAnalysis
    provenance: Provenance


class ColorStateRow(BaseModel):
    asset_id: str
    detection: ColorProfileDetection
    analysis_transform_id: str | None = None
    analysis_lut_hash: str | None = None
    analysis_is_fallback: bool = False
    analysis_proxy_path: str | None = None
