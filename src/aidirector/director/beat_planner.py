"""Beat Planner — converts the story arc into a time structure (AGENT.md §49)."""

from __future__ import annotations

import re

from ..ai.schemas import Message
from ..ai.services import AIServices
from ..logging import get_logger
from .prompts import load_prompt
from .schemas import Beat, BeatPlan, StoryPlan

log = get_logger("director.beats")

_OUTLINE_SEPARATORS = re.compile(r"[,、\n→>]+")


def parse_outline(text: str | None) -> list[str]:
    """Split a user-written flow into ordered beat names.

    Accepts one item per line or separators: "," "、" "→" ">".
    e.g. "出発, 電車移動, レストラン" or "出発 → 電車移動 → レストラン".
    """
    if not text:
        return []
    return [part.strip() for part in _OUTLINE_SEPARATORS.split(text) if part.strip()]


def uniquify_outline(outline: list[str]) -> list[str]:
    """Repeated section names (電車移動 out and back) get instance
    suffixes — beat identity is by name everywhere downstream, so
    duplicates would merge into one section."""
    counts: dict[str, int] = {}
    result: list[str] = []
    for name in outline:
        counts[name] = counts.get(name, 0) + 1
        result.append(name if counts[name] == 1 else f"{name} ({counts[name]})")
    return result


def enforce_outline(
    plan: BeatPlan, outline: list[str], target_duration: float
) -> BeatPlan:
    """A user-specified flow is fact: exact names, exact order (AGENT.md §2).

    The LLM's durations and purposes are kept when it followed the flow
    (or at least produced one beat per item); otherwise the structure is
    rebuilt deterministically with even durations.
    """
    if [b.name for b in plan.beats] == outline:
        return plan
    if len(plan.beats) == len(outline):
        for beat, name in zip(plan.beats, outline):
            beat.name = name
        return plan
    log.warning(
        "beat planner returned %d beats for a %d-item flow; rebuilding evenly",
        len(plan.beats), len(outline),
    )
    even = target_duration / len(outline)
    return BeatPlan(
        target_duration=target_duration,
        beats=[Beat(name=name, duration=round(even, 2)) for name in outline],
    )


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
    outline: list[str] | None = None,
) -> BeatPlan:
    outline_section = ""
    if outline:
        items = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(outline))
        outline_section = (
            "## Required flow (user-specified — overrides the beat-count rule)\n"
            "Use EXACTLY these beats, in this order, keeping every name "
            f"verbatim; assign each a duration and a clear purpose:\n{items}"
        )
    prompt = load_prompt("beats").format(
        story_json=story.model_dump_json(),
        target_duration=int(target_duration),
        outline=outline_section,
    )
    plan = await ai.generate_structured(
        [Message(role="user", content=prompt)], BeatPlan
    )
    if outline:
        plan = enforce_outline(plan, outline, target_duration)
    return normalize_beats(plan, target_duration)
