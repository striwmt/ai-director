"""Candidate diversification: unused footage keeps entering the pool.

diversify_candidates is a pure re-rank (no AI); asset_usage_counts reads
saved plans back from Media Memory.
"""

from __future__ import annotations

from pathlib import Path

from aidirector.director.selector import diversify_candidates
from aidirector.media.metadata import MediaMetadata
from aidirector.memory.models import AssetRecord, SegmentRecord


def make_segment(segment_id: str, asset_id: str) -> SegmentRecord:
    return SegmentRecord(
        id=segment_id, asset_id=asset_id, idx=0, start=0.0, end=5.0
    )


def test_diversify_reserves_slots_for_least_used_assets():
    # Semantic ranking: a..f. Assets a-d appear in every past plan; e/f never.
    segments = [make_segment(f"seg_{x}", f"ast_{x}") for x in "abcdef"]
    counts = {"ast_a": 3, "ast_b": 3, "ast_c": 2, "ast_d": 1}
    chosen = diversify_candidates(segments, counts, limit=4)
    # Top half stays semantic (a, b); reserved half goes to fresh assets.
    assert [s.id for s in chosen] == ["seg_a", "seg_b", "seg_e", "seg_f"]


def test_diversify_keeps_semantic_order_on_ties():
    segments = [make_segment(f"seg_{x}", f"ast_{x}") for x in "abcd"]
    chosen = diversify_candidates(segments, {}, limit=2)
    # All unused: nothing to promote, semantic order wins throughout.
    assert [s.id for s in chosen] == ["seg_a", "seg_b"]


def test_diversify_short_list_passthrough():
    segments = [make_segment("seg_a", "ast_a")]
    assert diversify_candidates(segments, {"ast_a": 5}, limit=8) == segments


def test_asset_usage_counts(memory):
    project = memory.get_or_create_project("trip", Path("/footage"))
    for name in ("one.mp4", "two.mp4", "unused.mp4"):
        memory.upsert_asset(AssetRecord(
            id=f"ast_{name.split('.')[0]}",
            project_id=project.id,
            path=f"/footage/{name}", file_name=name, kind="video",
            size=1000, mtime=1723600000.0, partial_hash=f"hash_{name}",
            duration=12.5, metadata=MediaMetadata(duration=12.5),
        ))
        memory.replace_segments(f"ast_{name.split('.')[0]}", [
            make_segment(f"seg_{name.split('.')[0]}", f"ast_{name.split('.')[0]}"),
        ])

    run_id = memory.create_director_run(project.id, {"target_duration": 60})
    # Plan 1 uses one+two; plan 2 uses one twice (still one plan = count 1).
    memory.save_edit_plan(
        run_id,
        '{"version": 1, "clips": ['
        '{"segment_id": "seg_one"}, {"segment_id": "seg_two"}]}',
    )
    memory.save_edit_plan(
        run_id,
        '{"version": 2, "clips": ['
        '{"segment_id": "seg_one"}, {"segment_id": "seg_one"}]}',
        version=2,
    )

    counts = memory.asset_usage_counts(project.id)
    assert counts == {"ast_one": 2, "ast_two": 1}
    assert "ast_unused" not in counts


def test_backfill_candidates_from_window(memory):
    from aidirector.director.selector import backfill_candidates_from_window
    from aidirector.media.metadata import MediaMetadata

    project = memory.get_or_create_project("trip", Path("/footage"))
    times = [
        ("morning.mp4", "2026-08-15T09:00:00"),
        ("noon.mp4", "2026-08-15T12:00:00"),
        ("evening.mp4", "2026-08-15T18:00:00"),
    ]
    for i, (name, ct) in enumerate(times):
        asset = AssetRecord(
            id=f"ast_{i}", project_id=project.id, path=f"/footage/{name}",
            file_name=name, kind="video", size=1000, mtime=1723600000.0,
            partial_hash=f"hash_{name}", duration=12.5,
            metadata=MediaMetadata(duration=12.5, creation_time=ct),
        )
        memory.upsert_asset(asset)
        memory.replace_segments(asset.id, [make_segment(f"seg_{i}", asset.id)])

    got = backfill_candidates_from_window(
        memory, project.id, "2026-08-15T10:00:00", "2026-08-15T13:00:00",
        set(), 8,
    )
    assert [u.segment_id for u in got] == ["seg_1"]

    got = backfill_candidates_from_window(
        memory, project.id, None, None, {"seg_0"}, 8,
    )
    assert [u.segment_id for u in got] == ["seg_1", "seg_2"]
