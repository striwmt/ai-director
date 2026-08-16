"""Candidate retrieval + clip selection per beat (AGENT.md §50/§51).

Retrieval searches Media Memory per beat — the whole library is never
stuffed into the LLM context (§61/§78).
"""

from __future__ import annotations

from typing import Any

from ..ai.schemas import Message
from ..ai.services import AIServices
from ..logging import get_logger
from ..memory.models import SegmentRecord
from ..memory.repository import MediaMemory
from ..memory.search import MediaSearch
from ..perception.interpretation import SegmentUnderstanding, build_understanding
from .prompts import load_prompt
from .schemas import Beat, BeatSelection, StoryPlan

log = get_logger("director.selector")

# Retrieval over-fetch factor: extra semantic hits give the freshness
# re-rank something to choose from.
_OVERFETCH = 3


def plan_time_windows(
    candidate_lists: list[list[SegmentUnderstanding]],
) -> list[tuple[str | None, str | None]]:
    """Assign each beat a capture-time window so beats consume the shoot
    in order — the chronology guarantee is structural, not advisory
    (AGENT.md §2).

    A small DP picks one dated "anchor" candidate per beat, minimizing
    summed semantic rank subject to anchor times being non-decreasing
    across beats; a beat whose footage cannot fit the order is skipped at
    a high penalty (it then inherits its neighbors' bounds). Beat i's
    window is [own anchor, next anchored beat's anchor]. Undated
    candidates are never window-filtered.
    """
    skip_penalty = 10_000  # worse than any achievable rank sum
    dated: list[list[tuple[str, int]]] = [
        sorted(
            (c.recorded_at, rank)
            for rank, c in enumerate(cands)
            if c.recorded_at is not None
        )
        for cands in candidate_lists
    ]
    # State: (last_anchor_time, cost, parent, this_beat_anchor)
    states: list[tuple[str, int, Any, str | None]] = [("", 0, None, None)]
    for options in dated:
        new_states: list[tuple[str, int, Any, str | None]] = []
        for prev in states:
            new_states.append((prev[0], prev[1] + skip_penalty, prev, None))
            for t, rank in options:
                if t >= prev[0]:
                    new_states.append((t, prev[1] + rank, prev, t))
        # Pareto prune: among states sorted by time, keep strictly
        # improving costs (a later time never helps unless it is cheaper).
        new_states.sort(key=lambda s: (s[0], s[1]))
        pruned: list[tuple[str, int, Any, str | None]] = []
        best_cost = None
        for s in new_states:
            if best_cost is None or s[1] < best_cost:
                pruned.append(s)
                best_cost = s[1]
        states = pruned
    final = min(states, key=lambda s: s[1])
    anchors: list[str | None] = []
    node = final
    while node is not None and node[2] is not None:
        anchors.append(node[3])
        node = node[2]
    anchors.reverse()

    windows: list[tuple[str | None, str | None]] = []
    for i in range(len(anchors)):
        lo = anchors[i]
        if lo is None:  # skipped beat: bounded by the previous anchor
            lo = next((anchors[j] for j in range(i - 1, -1, -1)
                       if anchors[j] is not None), None)
        hi = next((anchors[j] for j in range(i + 1, len(anchors))
                   if anchors[j] is not None), None)
        windows.append((lo, hi))
    return windows


def filter_candidates_by_window(
    candidates: list[SegmentUnderstanding],
    window: tuple[str | None, str | None],
) -> list[SegmentUnderstanding]:
    lo, hi = window
    return [
        c for c in candidates
        if c.recorded_at is None
        or ((lo is None or c.recorded_at >= lo)
            and (hi is None or c.recorded_at <= hi))
    ]


def filter_candidates_by_time(
    candidates: list[SegmentUnderstanding],
    frontier: str | None,
) -> list[SegmentUnderstanding]:
    """Chronology guarantee at selection time: once a beat used footage
    shot at time T, later beats only get candidates shot at >= T (undated
    candidates stay). Falls back to the unfiltered list rather than
    leaving a beat empty."""
    if frontier is None:
        return candidates
    kept = [
        c for c in candidates
        if c.recorded_at is None or c.recorded_at >= frontier
    ]
    if not kept and candidates:
        log.warning(
            "no candidates shot after %s; allowing earlier footage for this beat",
            frontier,
        )
        return candidates
    return kept


def advance_time_frontier(
    frontier: str | None,
    chosen: list[SegmentUnderstanding],
) -> str | None:
    """Latest capture time used so far (ISO strings compare correctly
    within one clock basis, which one camera's metadata is)."""
    for c in chosen:
        if c.recorded_at is not None and (frontier is None or c.recorded_at > frontier):
            frontier = c.recorded_at
    return frontier


def diversify_candidates(
    segments: list[SegmentRecord],
    usage_counts: dict[str, int],
    limit: int,
) -> list[SegmentRecord]:
    """Coverage guarantee across re-creations (deterministic, no AI).

    The best semantic matches keep the first half of the slots; the other
    half goes to the least-used source videos among the remaining hits
    (ties keep semantic order), so footage that no saved plan has used
    yet keeps entering the candidate pool.
    """
    if len(segments) <= limit:
        return segments[:limit]
    reserved = limit // 2
    top = segments[: limit - reserved]
    rest = sorted(
        segments[limit - reserved:],
        key=lambda s: usage_counts.get(s.asset_id, 0),
    )
    return top + rest[:reserved]


async def retrieve_candidates(
    search: MediaSearch,
    memory: MediaMemory,
    project_id: str,
    beat: Beat,
    story: StoryPlan,
    *,
    limit: int,
    exclude: set[str],
    usage_counts: dict[str, int] | None = None,
) -> list[SegmentUnderstanding]:
    query = " ".join(
        part for part in (beat.name, beat.purpose, story.concept, story.tone) if part
    )
    hits = await search.search(
        project_id, query, limit=limit * _OVERFETCH, exclude_segment_ids=exclude
    )
    segments = [memory.get_segment(hit.segment_id) for hit in hits]
    chosen = diversify_candidates(
        [s for s in segments if s is not None], usage_counts or {}, limit
    )
    candidates: list[SegmentUnderstanding] = [
        build_understanding(segment, memory) for segment in chosen
    ]

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
    guidance: str = "",
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
        guidance=guidance,
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
