"""Semantic search over Media Memory (AGENT.md §46).

Combines embedding similarity, transcript keyword matching and metadata
filters. Pure Python cosine keeps the MVP dependency-free; the interface
stays the same when a vector index replaces it.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from ..ai.services import AIServices
from ..logging import get_logger
from .repository import MediaMemory

log = get_logger("memory.search")


class SearchHit(BaseModel):
    segment_id: str
    score: float
    matched_by: list[str] = Field(default_factory=list)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class MediaSearch:
    def __init__(self, memory: MediaMemory, ai: AIServices) -> None:
        self.memory = memory
        self.ai = ai

    async def search(
        self,
        project_id: str,
        query: str,
        *,
        limit: int = 10,
        exclude_segment_ids: set[str] | None = None,
    ) -> list[SearchHit]:
        exclude = exclude_segment_ids or set()
        scores: dict[str, SearchHit] = {}

        # 1. Embedding similarity (if an embedding provider is available).
        try:
            query_vecs = await self.ai.embed_text([query], prompt_name="query")
            if query_vecs:
                stored = self.memory.iter_segment_embeddings(project_id, kind="text")
                for seg_id, vector in stored:
                    if seg_id in exclude:
                        continue
                    score = cosine(query_vecs[0].vector, vector)
                    if score > 0:
                        scores[seg_id] = SearchHit(
                            segment_id=seg_id, score=score, matched_by=["embedding"]
                        )
        except Exception as exc:
            log.warning("embedding search unavailable (%s); keyword-only", exc)

        # 2. Keyword match over description + transcript text.
        keywords = [w.lower() for w in query.split() if len(w) >= 2]
        if keywords:
            for segment in self.memory.list_project_segments(project_id):
                if segment.id in exclude:
                    continue
                analysis = self.memory.get_semantic_annotation(segment.id)
                transcript = self.memory.get_transcript(segment.asset_id)
                haystack_parts: list[str] = []
                if analysis:
                    haystack_parts.append(analysis.description)
                    haystack_parts.extend(analysis.subjects + analysis.actions + analysis.mood)
                if transcript:
                    from ..perception.speech import transcript_for_span

                    haystack_parts.append(
                        transcript_for_span(transcript, segment.start, segment.end)
                    )
                haystack = " ".join(haystack_parts).lower()
                matched = sum(1 for kw in keywords if kw in haystack)
                if matched:
                    bonus = 0.15 * matched / len(keywords)
                    if segment.id in scores:
                        scores[segment.id].score += bonus
                        scores[segment.id].matched_by.append("keyword")
                    else:
                        scores[segment.id] = SearchHit(
                            segment_id=segment.id, score=bonus, matched_by=["keyword"]
                        )

        hits = sorted(scores.values(), key=lambda h: h.score, reverse=True)
        return hits[:limit]
