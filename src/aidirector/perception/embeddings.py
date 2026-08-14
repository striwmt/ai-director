"""Embedding generation for Media Memory retrieval."""

from __future__ import annotations

from ..ai.services import AIServices
from ..logging import get_logger
from ..memory.repository import MediaMemory
from .interpretation import SegmentUnderstanding

log = get_logger("perception.embeddings")


async def embed_segments(
    understandings: list[SegmentUnderstanding],
    ai: AIServices,
    memory: MediaMemory,
    *,
    force: bool = False,
) -> int:
    """Embed each segment's search text; reuse stored vectors (AGENT.md §45/§69)."""
    model = ai.runtime.provider_label("embedding")

    pending: list[SegmentUnderstanding] = []
    for u in understandings:
        text = u.to_search_text()
        if not text:
            continue
        if not force and memory.get_embedding("segment", u.segment_id, "text", model):
            continue
        pending.append(u)

    if not pending:
        return 0

    batch_size = 16
    embedded = 0
    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        vectors = await ai.embed_text([u.to_search_text() for u in batch])
        for u, emb in zip(batch, vectors):
            memory.save_embedding("segment", u.segment_id, "text", model, emb.vector)
            embedded += 1
    log.info("embedded %d segments", embedded)
    return embedded
