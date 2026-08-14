"""Candidate retrieval + clip selection per beat (AGENT.md §50/§51).

Retrieval searches Media Memory per beat — the whole library is never
stuffed into the LLM context (§61/§78).
"""

from __future__ import annotations

from ..ai.schemas import Message
from ..ai.services import AIServices
from ..logging import get_logger
from ..memory.repository import MediaMemory
from ..memory.search import MediaSearch
from ..perception.interpretation import SegmentUnderstanding, build_understanding
from .prompts import load_prompt
from .schemas import Beat, BeatSelection, StoryPlan

log = get_logger("director.selector")


async def retrieve_candidates(
    search: MediaSearch,
    memory: MediaMemory,
    project_id: str,
    beat: Beat,
    story: StoryPlan,
    *,
    limit: int,
    exclude: set[str],
) -> list[SegmentUnderstanding]:
    query = " ".join(
        part for part in (beat.name, beat.purpose, story.concept, story.tone) if part
    )
    hits = await search.search(
        project_id, query, limit=limit, exclude_segment_ids=exclude
    )
    candidates: list[SegmentUnderstanding] = []
    for hit in hits:
        segment = memory.get_segment(hit.segment_id)
        if segment is None:
            continue
        candidates.append(build_understanding(segment, memory))

    # Fallback: with no retrieval signal (e.g. no embeddings and no keyword
    # hits) offer unused segments in chronological order so the director can
    # still work.
    if not candidates:
        for segment in memory.list_project_segments(project_id):
            if segment.id in exclude:
                continue
            candidates.append(build_understanding(segment, memory))
            if len(candidates) >= limit:
                break
    return candidates


def _filter_technically_bad(
    candidates: list[SegmentUnderstanding],
) -> list[SegmentUnderstanding]:
    """Score-style data may exclude obvious failures — never rank the rest
    (AGENT.md §3)."""
    hard_flags = {"over_exposed", "under_exposed"}
    good = [c for c in candidates if not (hard_flags & set(c.quality_flags))]
    return good or candidates  # if everything is flagged, let the AI judge


async def select_for_beat(
    ai: AIServices,
    *,
    story: StoryPlan,
    beat: Beat,
    candidates: list[SegmentUnderstanding],
    used: list[SegmentUnderstanding],
    max_choices: int = 4,
) -> BeatSelection:
    candidates = _filter_technically_bad(candidates)
    if not candidates:
        return BeatSelection(beat_name=beat.name, choices=[])

    candidate_lines = "\n".join(c.to_summary_line() for c in candidates)
    used_summary = (
        "\n".join(u.to_summary_line() for u in used[-12:]) if used else "(nothing yet)"
    )
    prompt = load_prompt("select").format(
        story_json=story.model_dump_json(),
        beat_name=beat.name,
        beat_duration=beat.duration,
        beat_purpose=beat.purpose or "(not specified)",
        candidates=candidate_lines,
        used_summary=used_summary,
        max_choices=max_choices,
    )
    selection = await ai.generate_structured(
        [Message(role="user", content=prompt)], BeatSelection
    )
    selection.beat_name = beat.name

    # Deterministic guarantee: only offered candidates may be chosen.
    valid_ids = {c.segment_id for c in candidates}
    selection.choices = [c for c in selection.choices if c.segment_id in valid_ids][
        :max_choices
    ]
    return selection
