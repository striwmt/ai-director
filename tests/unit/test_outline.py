"""User-specified story flow: parsing, beat enforcement, order authority."""

from __future__ import annotations

from aidirector.director.beat_planner import (
    enforce_outline,
    normalize_beats,
    parse_outline,
)
from aidirector.director.editor import sort_chronologically
from aidirector.director.schemas import Beat, BeatPlan, SequenceClip, SequencePlan
from aidirector.perception.interpretation import SegmentUnderstanding


def test_parse_outline_separators():
    expected = ["出発", "電車移動", "レストラン"]
    assert parse_outline("出発, 電車移動, レストラン") == expected
    assert parse_outline("出発、電車移動、レストラン") == expected
    assert parse_outline("出発 → 電車移動 → レストラン") == expected
    assert parse_outline("出発\n電車移動\nレストラン") == expected
    assert parse_outline("  ") == []
    assert parse_outline(None) == []


def test_enforce_outline_keeps_matching_plan():
    outline = ["a", "b"]
    plan = BeatPlan(
        target_duration=60,
        beats=[Beat(name="a", duration=20), Beat(name="b", duration=40)],
    )
    assert enforce_outline(plan, outline, 60) is plan


def test_enforce_outline_renames_same_count():
    plan = BeatPlan(
        target_duration=60,
        beats=[
            Beat(name="hook", duration=10, purpose="p1"),
            Beat(name="middle", duration=40, purpose="p2"),
            Beat(name="ending", duration=10, purpose="p3"),
        ],
    )
    fixed = enforce_outline(plan, ["出発", "電車移動", "レストラン"], 60)
    assert [b.name for b in fixed.beats] == ["出発", "電車移動", "レストラン"]
    # The LLM's proportions and purposes survive.
    assert [b.duration for b in fixed.beats] == [10, 40, 10]
    assert fixed.beats[1].purpose == "p2"


def test_enforce_outline_rebuilds_on_count_mismatch():
    plan = BeatPlan(target_duration=60, beats=[Beat(name="only", duration=60)])
    fixed = normalize_beats(
        enforce_outline(plan, ["出発", "電車移動", "レストラン", "徒歩"], 60), 60
    )
    assert [b.name for b in fixed.beats] == ["出発", "電車移動", "レストラン", "徒歩"]
    assert sum(b.duration for b in fixed.beats) == 60


def make_segment(segment_id: str, recorded_at: str) -> SegmentUnderstanding:
    return SegmentUnderstanding(
        segment_id=segment_id, asset_id=f"ast_{segment_id}",
        asset_name=f"{segment_id}.mp4", start=0.0, end=30.0, duration=30.0,
        recorded_at=recorded_at,
    )


def make_clip(segment_id: str, beat: str) -> SequenceClip:
    return SequenceClip(
        segment_id=segment_id, source_in=0.0, source_out=4.0,
        story_beat=beat, reason="test",
    )


def test_outline_order_beats_global_chronology():
    # User flow: 徒歩 then 電車移動 — even though the train clip was shot
    # between the two walk clips, the flow order is authoritative.
    segments = {
        "walk_late": make_segment("walk_late", "2026-08-10T17:00:00"),
        "walk_early": make_segment("walk_early", "2026-08-10T15:00:00"),
        "train": make_segment("train", "2026-08-10T16:00:00"),
    }
    plan = SequencePlan(clips=[
        make_clip("walk_late", "徒歩"),
        make_clip("train", "電車移動"),
        make_clip("walk_early", "徒歩"),
    ])
    sorted_plan = sort_chronologically(
        plan, segments, group_order=["徒歩", "電車移動"]
    )
    # Groups follow the user's flow; chronology applies within each group,
    # so the 16:00 train clip is NOT pulled between the two walk clips.
    assert [c.segment_id for c in sorted_plan.clips] == [
        "walk_early", "walk_late", "train",
    ]


def test_repeated_flow_section_does_not_duplicate_clips():
    # Regression: 出発→電車移動→…→電車移動 — a section name appearing
    # twice in the flow must not emit its group's clips twice.
    segments = {
        "train": make_segment("train", "2026-08-15T13:06:00"),
        "start": make_segment("start", "2026-08-15T11:00:00"),
    }
    plan = SequencePlan(clips=[
        make_clip("start", "出発"), make_clip("train", "電車移動"),
    ])
    sorted_plan = sort_chronologically(
        plan, segments,
        group_order=["出発", "電車移動", "帰り道", "電車移動"],
    )
    assert [c.segment_id for c in sorted_plan.clips] == ["start", "train"]


def test_group_order_keeps_unknown_beats_at_end():
    segments = {
        "a": make_segment("a", "2026-08-10T10:00:00"),
        "b": make_segment("b", "2026-08-10T09:00:00"),
    }
    plan = SequencePlan(clips=[
        make_clip("a", "typo_beat"), make_clip("b", "出発"),
    ])
    sorted_plan = sort_chronologically(plan, segments, group_order=["出発"])
    assert [c.segment_id for c in sorted_plan.clips] == ["b", "a"]


def test_time_frontier_filtering():
    from aidirector.director.selector import (
        advance_time_frontier,
        filter_candidates_by_time,
    )

    morning = make_segment("morning", "2026-08-15T09:00:00")
    noon = make_segment("noon", "2026-08-15T12:00:00")
    evening = make_segment("evening", "2026-08-15T18:00:00")
    undated = make_segment("undated", None)
    undated.recorded_at = None

    # No frontier yet: everything passes.
    assert filter_candidates_by_time([morning, noon], None) == [morning, noon]

    frontier = advance_time_frontier(None, [noon])
    assert frontier == "2026-08-15T12:00:00"
    # Earlier footage is excluded for later beats; undated stays.
    kept = filter_candidates_by_time([morning, evening, undated], frontier)
    assert [c.segment_id for c in kept] == ["evening", "undated"]
    # Never empty a beat: all-earlier candidates fall back unfiltered.
    assert filter_candidates_by_time([morning], frontier) == [morning]
    # The frontier only moves forward.
    assert advance_time_frontier(frontier, [morning]) == frontier
    assert advance_time_frontier(frontier, [evening]) == "2026-08-15T18:00:00"
