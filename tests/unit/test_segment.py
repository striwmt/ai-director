from aidirector.config import SegmentationConfig
from aidirector.media.segment import build_segments

CFG = SegmentationConfig(
    min_segment_seconds=1.5, max_segment_seconds=15.0
)


def test_no_boundaries_single_segment():
    segments = build_segments("ast_x", 10.0, {}, CFG)
    assert len(segments) == 1
    assert segments[0].start == 0.0
    assert segments[0].end == 10.0
    assert segments[0].idx == 0


def test_hard_cut_splits():
    segments = build_segments("ast_x", 10.0, {"hard_cut": [4.0]}, CFG)
    assert len(segments) == 2
    assert segments[0].end == 4.0
    assert segments[1].start == 4.0
    assert "hard_cut" in segments[1].boundary_reasons


def test_close_boundaries_merged():
    segments = build_segments(
        "ast_x", 10.0, {"hard_cut": [4.0, 4.5], "silence": [4.3]}, CFG
    )
    # 4.5 and 4.3 are within min_segment_seconds of 4.0 -> one cut.
    assert len(segments) == 2


def test_long_take_subdivided():
    segments = build_segments("ast_x", 40.0, {}, CFG)
    assert len(segments) == 3  # 40 / 15 -> ceil = 3 parts
    assert all(s.duration <= 15.0 + 0.001 for s in segments)
    assert segments[-1].end == 40.0
    assert "long_take_subdivision" in segments[1].boundary_reasons


def test_short_tail_merged():
    segments = build_segments("ast_x", 10.5, {"hard_cut": [9.5]}, CFG)
    # tail 9.5-10.5 is shorter than min -> merged into previous.
    assert len(segments) == 1
    assert segments[0].end == 10.5


def test_zero_duration():
    assert build_segments("ast_x", 0.0, {}, CFG) == []


def test_boundaries_outside_duration_ignored():
    segments = build_segments("ast_x", 10.0, {"hard_cut": [12.0, -1.0, 0.0]}, CFG)
    assert len(segments) == 1
