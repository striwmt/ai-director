"""Compiled timeline model.

The Timeline Compiler translates an Edit Plan into concrete timelines; it
never changes editorial decisions (AGENT.md §57). Different outputs
reference different media (§58):

    preview  -> analysis/preview proxy
    NLE      -> ORIGINAL camera file (D-Log2 stays D-Log2)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..director.schemas import ClipAudio, ClipCaption, ClipTransition


class TimelineClip(BaseModel):
    index: int
    segment_id: str
    # Media references
    original_path: str
    proxy_path: str | None = None
    # Source time range (in source-file seconds)
    source_in: float
    source_out: float
    # Position on the assembled timeline
    timeline_start: float
    audio: ClipAudio = ClipAudio()
    transition: ClipTransition = ClipTransition()
    caption: ClipCaption | None = None
    story_beat: str = ""
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.source_out - self.source_in

    @property
    def timeline_end(self) -> float:
        return self.timeline_start + self.duration


class Timeline(BaseModel):
    name: str = "AI Director Timeline"
    fps: float = 30.0
    # Canvas: every output (preview, NLE) renders into this frame; clips of a
    # different aspect are letter/pillar-boxed, never stretched.
    width: int = 1920
    height: int = 1080
    clips: list[TimelineClip] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.clips[-1].timeline_end if self.clips else 0.0

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width
