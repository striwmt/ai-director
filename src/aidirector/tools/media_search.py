"""Director tool: search_media(query) (AGENT.md §61)."""

from __future__ import annotations

from ..ai.services import AIServices
from ..memory.repository import MediaMemory
from ..memory.search import MediaSearch, SearchHit


async def search_media(
    memory: MediaMemory,
    ai: AIServices,
    project_id: str,
    query: str,
    *,
    limit: int = 10,
) -> list[SearchHit]:
    return await MediaSearch(memory, ai).search(project_id, query, limit=limit)
