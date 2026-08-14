"""Director tool: find_similar / duplicate detection via embeddings."""

from __future__ import annotations

from ..memory.repository import MediaMemory
from ..memory.search import cosine


def find_similar(
    memory: MediaMemory,
    project_id: str,
    segment_id: str,
    *,
    model: str,
    threshold: float = 0.85,
    limit: int = 10,
) -> list[tuple[str, float]]:
    """Segments whose text embedding is close to the given segment's."""
    anchor = memory.get_embedding("segment", segment_id, "text", model)
    if anchor is None:
        return []
    results: list[tuple[str, float]] = []
    for other_id, vector in memory.iter_segment_embeddings(project_id, kind="text"):
        if other_id == segment_id:
            continue
        score = cosine(anchor, vector)
        if score >= threshold:
            results.append((other_id, score))
    results.sort(key=lambda r: r[1], reverse=True)
    return results[:limit]
