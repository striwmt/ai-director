"""Director tool: search_transcript(query) — keyword search over speech."""

from __future__ import annotations

from pydantic import BaseModel

from ..memory.repository import MediaMemory


class TranscriptHit(BaseModel):
    asset_id: str
    segment_start: float
    segment_end: float
    text: str


def search_transcript(
    memory: MediaMemory, project_id: str, query: str, *, limit: int = 20
) -> list[TranscriptHit]:
    keywords = [w.lower() for w in query.split() if w]
    hits: list[TranscriptHit] = []
    for asset in memory.list_assets(project_id):
        transcript = memory.get_transcript(asset.id)
        if transcript is None:
            continue
        for seg in transcript.segments:
            text = seg.text.lower()
            if any(kw in text for kw in keywords):
                hits.append(
                    TranscriptHit(
                        asset_id=asset.id,
                        segment_start=seg.start,
                        segment_end=seg.end,
                        text=seg.text.strip(),
                    )
                )
                if len(hits) >= limit:
                    return hits
    return hits
