"""Deterministic sequence guarantees: chronology sort and one-clip-per-video.

AI judges meaning; these passes guarantee facts (AGENT.md §2).
"""

from __future__ import annotations

from aidirector.director.editor import dedupe_assets, sort_chronologically
from aidirector.director.schemas import SequenceClip, SequencePlan
from aidirector.perception.interpretation import SegmentUnderstanding


def make_segment(
    segment_id: str,
    asset_id: str,
    *,
    start: float = 0.0,
    end: float = 30.0,
    recorded_at: str | None = None,
) -> SegmentUnderstanding:
    return SegmentUnderstanding(
        segment_id=segment_id,
        asset_id=asset_id,
        asset_name=f"{asset_id}.mp4",
        start=start,
        end=end,
        duration=end - start,
        recorded_at=recorded_at,
    )


def make_clip(segment_id: str, source_in: float = 0.0) -> SequenceClip:
    return SequenceClip(
        segment_id=segment_id,
        source_in=source_in,
        source_out=source_in + 4.0,
        story_beat="beat",
        reason="test",
    )


def test_sort_chronologically_orders_by_recording_time():
    segments = {
        "seg_morning": make_segment(
            "seg_morning", "a1", recorded_at="2026-08-10T08:00:00"
        ),
        "seg_noon": make_segment(
            "seg_noon", "a2", recorded_at="2026-08-10T12:00:00"
        ),
        "seg_evening": make_segment(
            "seg_evening", "a3", recorded_at="2026-08-10T18:30:00"
        ),
    }
    plan = SequencePlan(
        clips=[make_clip("seg_evening"), make_clip("seg_morning"), make_clip("seg_noon")]
    )
    sorted_plan = sort_chronologically(plan, segments)
    assert [c.segment_id for c in sorted_plan.clips] == [
        "seg_morning", "seg_noon", "seg_evening",
    ]


def test_sort_uses_clip_offset_within_one_file():
    # Two clips from the same dated file: the later source span is later
    # wall-clock time, whatever order the AI produced.
    segments = {
        "seg_talk": make_segment(
            "seg_talk", "a1", start=0.0, end=120.0,
            recorded_at="2026-08-10T09:00:00",
        ),
    }
    plan = SequencePlan(
        clips=[make_clip("seg_talk", source_in=60.0), make_clip("seg_talk", source_in=5.0)]
    )
    sorted_plan = sort_chronologically(plan, segments)
    assert [c.source_in for c in sorted_plan.clips] == [5.0, 60.0]


def test_sort_keeps_untimed_clips_in_place():
    segments = {
        "seg_late": make_segment("seg_late", "a1", recorded_at="2026-08-10T18:00:00"),
        "seg_untimed": make_segment("seg_untimed", "a2"),
        "seg_early": make_segment("seg_early", "a3", recorded_at="2026-08-10T07:00:00"),
    }
    plan = SequencePlan(
        clips=[make_clip("seg_late"), make_clip("seg_untimed"), make_clip("seg_early")]
    )
    sorted_plan = sort_chronologically(plan, segments)
    # Timed clips swap into chronological order; the undated clip keeps
    # its slot (there is no fact to sort it by).
    assert [c.segment_id for c in sorted_plan.clips] == [
        "seg_early", "seg_untimed", "seg_late",
    ]


def test_sort_without_timestamps_is_a_no_op():
    segments = {
        "seg_a": make_segment("seg_a", "a1"),
        "seg_b": make_segment("seg_b", "a2"),
    }
    plan = SequencePlan(clips=[make_clip("seg_b"), make_clip("seg_a")])
    assert sort_chronologically(plan, segments) is plan


def test_dedupe_assets_keeps_first_clip_per_video():
    segments = {
        "seg_a1": make_segment("seg_a1", "asset_a", start=0.0, end=10.0),
        "seg_a2": make_segment("seg_a2", "asset_a", start=20.0, end=30.0),
        "seg_b1": make_segment("seg_b1", "asset_b"),
    }
    plan = SequencePlan(
        clips=[make_clip("seg_a1"), make_clip("seg_b1"), make_clip("seg_a2", 20.0)]
    )
    deduped = dedupe_assets(plan, segments)
    assert [c.segment_id for c in deduped.clips] == ["seg_a1", "seg_b1"]


def test_dedupe_assets_no_duplicates_is_a_no_op():
    segments = {
        "seg_a": make_segment("seg_a", "asset_a"),
        "seg_b": make_segment("seg_b", "asset_b"),
    }
    plan = SequencePlan(clips=[make_clip("seg_a"), make_clip("seg_b")])
    assert [c.segment_id for c in dedupe_assets(plan, segments).clips] == [
        "seg_a", "seg_b",
    ]
