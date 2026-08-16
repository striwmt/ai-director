"""Timezone handling: UTC camera timestamps are shown/ordered in local time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aidirector.director.editor import sort_chronologically
from aidirector.director.orchestrator import _caption_token_values
from aidirector.director.schemas import SequenceClip, SequencePlan
from aidirector.perception.interpretation import (
    SegmentUnderstanding,
    segment_recorded_at,
    to_local_time,
)

# The machine's UTC offset at this date (DST-safe way to compute expectations).
_SAMPLE_UTC = datetime(2026, 8, 15, 2, 23, 27, tzinfo=timezone.utc)
_LOCAL = _SAMPLE_UTC.astimezone()


def test_segment_recorded_at_converts_utc_to_local():
    out = segment_recorded_at("2026-08-15T02:23:27.000000Z", 0.0)
    assert out is not None
    parsed = datetime.fromisoformat(out)
    assert parsed == _SAMPLE_UTC, "same instant"
    assert parsed.utcoffset() == _LOCAL.utcoffset(), "expressed in local time"
    assert parsed.hour == _LOCAL.hour


def test_segment_recorded_at_naive_passthrough():
    # No timezone info = camera-local already; must not be shifted.
    assert segment_recorded_at("2026-08-15T11:23:27", 10.0) == "2026-08-15T11:23:37"


def test_to_local_time_none():
    assert to_local_time(None) is None


def test_caption_time_tokens_use_local_time():
    values = _caption_token_values("駅", "2026-08-15T02:23:27+00:00")
    assert values["HH"] == f"{_LOCAL.hour:02d}"
    assert values["MM"] == "23"
    assert values["DATE"] == _LOCAL.strftime("%Y-%m-%d")


def test_sort_handles_mixed_aware_and_naive_timestamps():
    # One camera stamps UTC, another camera-local — sorting must not
    # raise and must order by actual local instants.
    naive_local = _LOCAL.replace(tzinfo=None)
    later_naive = naive_local + timedelta(hours=1)
    segments = {
        "utc_clip": SegmentUnderstanding(
            segment_id="utc_clip", asset_id="a1", asset_name="a1.mp4",
            start=0.0, end=30.0, duration=30.0,
            recorded_at="2026-08-15T02:23:27+00:00",
        ),
        "naive_clip": SegmentUnderstanding(
            segment_id="naive_clip", asset_id="a2", asset_name="a2.mp4",
            start=0.0, end=30.0, duration=30.0,
            recorded_at=later_naive.isoformat(),
        ),
    }
    plan = SequencePlan(clips=[
        SequenceClip(segment_id="naive_clip", source_in=0.0, source_out=4.0,
                     story_beat="b", reason="r"),
        SequenceClip(segment_id="utc_clip", source_in=0.0, source_out=4.0,
                     story_beat="b", reason="r"),
    ])
    ordered = sort_chronologically(plan, segments)
    assert [c.segment_id for c in ordered.clips] == ["utc_clip", "naive_clip"]
