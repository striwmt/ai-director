"""Beat Planner — converts the story arc into a time structure (AGENT.md §49)."""

from __future__ import annotations

from ..ai.schemas import Message
from ..ai.services import AIServices
from ..logging import get_logger
from .prompts import load_prompt
from .schemas import BeatPlan, StoryPlan

log = get_logger("director.beats")


def normalize_beats(plan: BeatPlan, target_duration: float) -> BeatPlan:
    """Deterministically rescale beat durations to hit the target exactly.

    The model proposes proportions; code guarantees the constraint
    (AGENT.md: AI judges meaning, code guarantees facts).
    """
    total = sum(b.duration for b in plan.beats)
    if total <= 0:
        return plan
    scale = target_duration / total
    for beat in plan.beats:
        beat.duration = round(beat.duration * scale, 2)
    plan.target_duration = target_duration
    return plan


async def plan_beats(
    ai: AIServices,
    *,
    story: StoryPlan,
    target_duration: float,
) -> BeatPlan:
    prompt = load_prompt("beats").format(
        story_json=story.model_dump_json(),
        target_duration=int(target_duration),
    )
    plan = await ai.generate_structured(
        [Message(role="user", content=prompt)], BeatPlan
    )
    return normalize_beats(plan, target_duration)
