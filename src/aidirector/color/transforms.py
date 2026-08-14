"""Color transform model (AGENT.md §15)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .profile import ColorProfile


@dataclass(frozen=True)
class ColorTransform:
    id: str
    source_profile: ColorProfile
    destination_profile: ColorProfile
    type: str  # lut3d | ffmpeg_filter | passthrough
    path: Path | None = None
    vendor: str | None = None
    version: str | None = None
    # For type == ffmpeg_filter: a literal ffmpeg -vf chain.
    filter_expr: str | None = None
    purposes: tuple[str, ...] = field(default=("analysis", "preview"))

    def supports(self, purpose: str) -> bool:
        return purpose in self.purposes
