"""Director pipeline schemas, including the Edit Plan — the project's
central data format (AGENT.md §54/§55). All AI outputs validate against
these models (§28).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Story Planner (§48)


class StoryPlan(BaseModel):
    concept: str
    tone: str
    pace: str = "medium"
    story_arc: list[str] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Beat Planner (§49)


class Beat(BaseModel):
    name: str
    duration: float = Field(gt=0)
    purpose: str = ""


class BeatPlan(BaseModel):
    target_duration: float = Field(gt=0)
    beats: list[Beat] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Clip Selector (§51)


class ClipChoice(BaseModel):
    segment_id: str
    reason: str = ""


class BeatSelection(BaseModel):
    beat_name: str
    choices: list[ClipChoice] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sequence Editor (§52)


class SequenceClip(BaseModel):
    segment_id: str
    source_in: float = Field(ge=0)
    source_out: float = Field(gt=0)
    story_beat: str
    audio_intent: Literal[
        "preserve_ambient", "preserve_speech", "mute", "duck"
    ] = "preserve_ambient"
    transition: Literal["cut", "crossfade"] = "cut"
    # Short place name if clearly identifiable from the material
    # (e.g. "平等院", "Kawagoe station"); null when uncertain.
    location: str | None = None
    reason: str


class SequencePlan(BaseModel):
    clips: list[SequenceClip] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Critic (§53)


class CritiqueIssue(BaseModel):
    severity: Literal["low", "medium", "high"]
    type: str
    description: str
    segment_ids: list[str] = Field(default_factory=list)


class Critique(BaseModel):
    score: int = Field(ge=0, le=100)
    issues: list[CritiqueIssue] = Field(default_factory=list)
    revision_required: bool = False
    revision_notes: str = ""


# ---------------------------------------------------------------------------
# Edit Plan (§54/§55) — JSON-serializable, versioned, user-editable.


class EditPlanIntent(BaseModel):
    target_duration: float = Field(gt=0)
    profile: str = "travel_vlog"
    user_prompt: str = ""


class EditPlanStory(BaseModel):
    concept: str = ""
    tone: str = ""


class ClipAudio(BaseModel):
    mode: Literal["original", "muted", "ducked"] = "original"
    gain_db: float = 0.0


class ClipTransition(BaseModel):
    type: Literal["cut", "crossfade"] = "cut"
    duration: float = 0.0


class SubtitleLine(BaseModel):
    """One spoken line shown as a subtitle. Times are SOURCE-file seconds
    (same coordinate system as source_in/source_out), so trimming a clip
    keeps subtitles aligned."""

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str


class ClipCaption(BaseModel):
    """Centered caption shown at the start of a clip (scene-change titles).

    Part of the Edit Plan, so the user can edit or remove it and re-render.
    """

    text: str = ""             # main line, typically the place
    secondary: str = ""        # smaller second line, typically the time
    duration: float = Field(default=3.0, gt=0, le=10)


class EditClip(BaseModel):
    segment_id: str
    source_in: float = Field(ge=0)
    source_out: float = Field(gt=0)
    story_beat: str = ""
    audio: ClipAudio = ClipAudio()
    transition: ClipTransition = ClipTransition()
    caption: ClipCaption | None = None
    # Spoken-word subtitles from the transcript (deterministic facts,
    # user-editable like everything else in the plan).
    subtitles: list[SubtitleLine] = Field(default_factory=list)
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.source_out - self.source_in


class EditPlan(BaseModel):
    version: int = 1
    intent: EditPlanIntent
    story: EditPlanStory = EditPlanStory()
    clips: list[EditClip] = Field(default_factory=list)

    @property
    def total_duration(self) -> float:
        return sum(c.duration for c in self.clips)
