from pathlib import Path

import pytest

from aidirector.ai.schemas import Provenance, VisionAnalysis
from aidirector.memory.models import SegmentRecord
from aidirector.memory.search import MediaSearch, cosine
from tests.unit.test_memory import make_asset


def test_cosine_basics():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert cosine([], [1.0]) == 0.0
    assert cosine([0, 0], [1, 1]) == 0.0


async def test_search_combines_embedding_and_keyword(memory, mock_ai):
    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)
    segments = [
        SegmentRecord(id="seg_sunset", asset_id=asset.id, idx=0, start=0.0, end=5.0),
        SegmentRecord(id="seg_food", asset_id=asset.id, idx=1, start=5.0, end=10.0),
    ]
    memory.replace_segments(asset.id, segments)
    provenance = Provenance(provider="mock", model="mock")
    memory.save_semantic_annotation(
        "seg_sunset",
        VisionAnalysis(description="beautiful sunset over the river", mood=["warm"]),
        provenance,
    )
    memory.save_semantic_annotation(
        "seg_food",
        VisionAnalysis(description="a bowl of ramen on the table", subjects=["ramen"]),
        provenance,
    )

    # Store embeddings computed by the same mock provider for consistency.
    provider = await mock_ai.runtime.acquire("embedding")
    for seg_id, text in [
        ("seg_sunset", "beautiful sunset over the river warm"),
        ("seg_food", "a bowl of ramen on the table ramen"),
    ]:
        emb = (await provider.embed_text([text]))[0]
        memory.save_embedding("segment", seg_id, "text", provider.name, emb.vector)

    search = MediaSearch(memory, mock_ai)
    hits = await search.search(project.id, "sunset", limit=5)
    assert hits, "expected at least one hit"
    assert hits[0].segment_id == "seg_sunset"
    assert "keyword" in hits[0].matched_by

    hits = await search.search(project.id, "ramen", limit=5)
    assert hits[0].segment_id == "seg_food"


async def test_search_excludes_ids(memory, mock_ai):
    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)
    memory.replace_segments(
        asset.id,
        [SegmentRecord(id="seg_only", asset_id=asset.id, idx=0, start=0.0, end=5.0)],
    )
    memory.save_semantic_annotation(
        "seg_only",
        VisionAnalysis(description="sunset"),
        Provenance(provider="mock", model="mock"),
    )
    search = MediaSearch(memory, mock_ai)
    hits = await search.search(project.id, "sunset", exclude_segment_ids={"seg_only"})
    assert hits == []
