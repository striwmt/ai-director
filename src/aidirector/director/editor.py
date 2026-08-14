"""Sequence Editor — decides exact cuts, order and audio intent (AGENT.md §52)."""

from __future__ import annotations

from ..ai.schemas import Message
from ..ai.services import AIServices
from ..logging import get_logger
from ..perception.interpretation import SegmentUnderstanding
from .prompts import load_prompt
from .schemas import BeatPlan, SequenceClip, SequencePlan, StoryPlan

log = get_logger("director.editor")

_MIN_CLIP_SECONDS = 0.8


def clamp_sequence(
    plan: SequencePlan,
    segments_by_id: dict[str, SegmentUnderstanding],
) -> SequencePlan:
    """Deterministic repair: keep every clip inside its segment's real source
    range and drop clips that reference unknown segments or collapse to
    nothing. AI judges meaning; code guarantees facts (AGENT.md §2)."""
    repaired: list[SequenceClip] = []
    used_spans: dict[str, list[tuple[float, float]]] = {}
    for clip in plan.clips:
        segment = segments_by_id.get(clip.segment_id)
        if segment is None:
            log.warning("dropping clip with unknown segment %s", clip.segment_id)
            continue
        source_in = max(clip.source_in, segment.start)
        source_out = min(clip.source_out, segment.end)
        if source_out - source_in < _MIN_CLIP_SECONDS:
            # Try to salvage by taking the strongest available span.
            source_in = segment.start
            source_out = min(segment.end, segment.start + max(_MIN_CLIP_SECONDS, 2.0))
        if source_out - source_in < _MIN_CLIP_SECONDS:
            log.warning("dropping too-short clip on %s", clip.segment_id)
            continue
        # Duplicate reference guard: reusing an overlapping span of the same
        # segment is a fact violation, not an editorial choice (AGENT.md §56).
        overlaps = any(
            source_in < end and source_out > start
            for start, end in used_spans.get(clip.segment_id, [])
        )
        if overlaps:
            log.warning("dropping duplicate use of %s", clip.segment_id)
            continue
        used_spans.setdefault(clip.segment_id, []).append((source_in, source_out))
        clip.source_in = round(source_in, 3)
        clip.source_out = round(source_out, 3)
        repaired.append(clip)
    return SequencePlan(clips=repaired) if repaired else plan


def enforce_target_duration(
    plan: SequencePlan,
    target_duration: float,
    *,
    tolerance: float = 0.15,
) -> SequencePlan:
    """Last-resort deterministic fit to the target duration.

    The director gets revision chances first; if the draft still overshoots,
    code enforces the constraint: proportionally trim clip tails (protecting
    speech clips as long as possible, floor 2.0s), then drop trailing clips.
    """
    total = sum(c.source_out - c.source_in for c in plan.clips)
    limit = target_duration * (1 + tolerance)
    if total <= limit or not plan.clips:
        return plan
    log.warning(
        "sequence is %.1fs vs target %.0fs; applying deterministic trim",
        total, target_duration,
    )

    floor = 2.0
    clips = [c.model_copy() for c in plan.clips]

    def trim_group(protect_speech: bool) -> None:
        nonlocal total
        group = [
            c for c in clips
            if (c.audio_intent != "preserve_speech") or not protect_speech
        ]
        excess = total - target_duration
        reducible = sum(max(0.0, (c.source_out - c.source_in) - floor) for c in group)
        if excess <= 0 or reducible <= 0:
            return
        ratio = min(1.0, excess / reducible)
        for c in group:
            cut = max(0.0, (c.source_out - c.source_in) - floor) * ratio
            c.source_out = round(c.source_out - cut, 3)
        total = sum(c.source_out - c.source_in for c in clips)

    trim_group(protect_speech=True)
    if total > limit:
        trim_group(protect_speech=False)
    while total > limit and len(clips) > 1:
        dropped = clips.pop()
        log.warning("dropping trailing clip on %s to meet duration", dropped.segment_id)
        total = sum(c.source_out - c.source_in for c in clips)
    return SequencePlan(clips=clips)


async def edit_sequence(
    ai: AIServices,
    *,
    story: StoryPlan,
    beats: BeatPlan,
    selections_text: str,
    segments_by_id: dict[str, SegmentUnderstanding],
    revision_notes: str = "",
) -> SequencePlan:
    notes = (
        f"## Revision required — fix these issues from the previous draft\n{revision_notes}"
        if revision_notes
        else ""
    )
    prompt = load_prompt("sequence").format(
        story_json=story.model_dump_json(),
        beats_json=beats.model_dump_json(),
        selections=selections_text,
        revision_notes=notes,
    )
    plan = await ai.generate_structured(
        [Message(role="user", content=prompt)], SequencePlan
    )
    return clamp_sequence(plan, segments_by_id)


def describe_selection(u: SegmentUnderstanding) -> str:
    """Detailed segment description for the sequence prompt."""
    lines = [
        f"- id: {u.segment_id}",
        f"  source range: {u.start:.2f} - {u.end:.2f} (duration {u.duration:.2f}s)",
    ]
    if u.recorded_at:
        lines.append(f"  shot at: {u.recorded_at[:19].replace('T', ' ')}")
    if u.orientation:
        lines.append(f"  orientation: {u.orientation}")
    if u.description:
        lines.append(f"  description: {u.description}")
    if u.transcript:
        lines.append(f'  speech: "{u.transcript[:200]}"')
    if u.mood:
        lines.append(f"  mood: {', '.join(u.mood[:5])}")
    if u.camera_motion:
        lines.append(f"  camera: {u.camera_motion}")
    if u.quality_flags:
        lines.append(f"  technical issues: {', '.join(u.quality_flags)}")
    if u.silence_ratio is not None:
        lines.append(f"  silence ratio: {u.silence_ratio:.2f}")
    return "\n".join(lines)
