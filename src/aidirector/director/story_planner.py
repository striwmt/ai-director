"""Story Planner — decides what the video should say (AGENT.md §48)."""

from __future__ import annotations

from ..ai.schemas import Message
from ..ai.services import AIServices
from .profile import DirectorProfile
from .prompts import load_prompt
from .schemas import StoryPlan


async def plan_story(
    ai: AIServices,
    *,
    user_prompt: str,
    target_duration: float,
    profile: DirectorProfile,
    project_summary: str,
) -> StoryPlan:
    prompt = load_prompt("story").format(
        user_prompt=user_prompt or "(none — infer a fitting story from the material)",
        target_duration=int(target_duration),
        profile_yaml=profile.to_prompt_yaml(),
        project_summary=project_summary,
    )
    return await ai.generate_structured(
        [Message(role="user", content=prompt)], StoryPlan
    )
