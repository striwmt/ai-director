"""Critic — reviews the draft sequence before it reaches a human (AGENT.md §53)."""

from __future__ import annotations

from ..ai.schemas import Message
from ..ai.services import AIServices
from ..perception.interpretation import SegmentUnderstanding
from .prompts import load_prompt
from .schemas import Critique, SequencePlan, StoryPlan


def _sequence_summary(
    plan: SequencePlan, segments_by_id: dict[str, SegmentUnderstanding]
) -> str:
    lines: list[str] = []
    for i, clip in enumerate(plan.clips):
        segment = segments_by_id.get(clip.segment_id)
        desc = segment.description if segment else "(unknown segment)"
        speech = (
            f' speech:"{segment.transcript[:80]}"' if segment and segment.transcript else ""
        )
        lines.append(
            f"{i + 1}. [{clip.story_beat}] {clip.segment_id} "
            f"{clip.source_in:.1f}-{clip.source_out:.1f}s "
            f"({clip.source_out - clip.source_in:.1f}s, {clip.transition}, "
            f"{clip.audio_intent}) — {desc}{speech} | reason: {clip.reason}"
        )
    return "\n".join(lines)


async def critique_edit(
    ai: AIServices,
    *,
    story: StoryPlan,
    plan: SequencePlan,
    target_duration: float,
    segments_by_id: dict[str, SegmentUnderstanding],
) -> Critique:
    actual = sum(c.source_out - c.source_in for c in plan.clips)
    prompt = load_prompt("critic").format(
        story_json=story.model_dump_json(),
        target_duration=int(target_duration),
        actual_duration=round(actual, 1),
        sequence_summary=_sequence_summary(plan, segments_by_id),
    )
    return await ai.generate_structured(
        [Message(role="user", content=prompt)], Critique
    )
