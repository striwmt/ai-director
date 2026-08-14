"""Director tool: technical quality lookups.

Scores exclude obvious failures and provide technical information — they
never decide the edit (AGENT.md §3).
"""

from __future__ import annotations

from ..memory.models import TechnicalFeatures
from ..memory.repository import MediaMemory


def get_quality(memory: MediaMemory, segment_id: str) -> TechnicalFeatures | None:
    return memory.get_technical_features(segment_id)


def find_bad_segments(
    memory: MediaMemory, project_id: str, flags: set[str] | None = None
) -> dict[str, list[str]]:
    """Map of segment_id -> quality flags, for segments with problems."""
    watched = flags or {"over_exposed", "under_exposed", "soft_focus"}
    result: dict[str, list[str]] = {}
    for segment in memory.list_project_segments(project_id):
        features = memory.get_technical_features(segment.id)
        if features and (watched & set(features.quality_flags)):
            result[segment.id] = features.quality_flags
    return result
