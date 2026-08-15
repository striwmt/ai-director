"""SMPTE timecode: parsing and the trust-gated creation-time refinement."""

from __future__ import annotations

import pytest

from aidirector.media.metadata import (
    MediaMetadata,
    refined_creation_time,
    timecode_to_seconds,
)
from aidirector.media.probe import FormatInfo, ProbeResult, StreamInfo


def test_timecode_to_seconds():
    assert timecode_to_seconds("14:23:05:15", 30.0) == pytest.approx(
        14 * 3600 + 23 * 60 + 5 + 15 / 30.0
    )
    # Drop-frame separator parses the same way.
    assert timecode_to_seconds("14:23:05;15", 29.97) == pytest.approx(
        14 * 3600 + 23 * 60 + 5 + 15 / 29.97
    )
    # Unknown fps assumes 30.
    assert timecode_to_seconds("00:00:01:15", None) == pytest.approx(1.5)
    assert timecode_to_seconds(None, 30.0) is None
    assert timecode_to_seconds("not a timecode", 30.0) is None
    assert timecode_to_seconds("25:00:00:00", 30.0) is None
    assert timecode_to_seconds("10:00:00:45", 30.0) is None  # frames >= fps


def make_metadata(**kwargs) -> MediaMetadata:
    return MediaMetadata(duration=10.0, fps=30.0, **kwargs)


def test_refined_when_clocks_agree():
    # creation_time stamped at the end of a 10s clip; timecode has the start.
    metadata = make_metadata(
        creation_time="2026-08-10T14:23:15", timecode="14:23:05:15"
    )
    assert refined_creation_time(metadata) == "2026-08-10T14:23:05.500000"


def test_record_run_timecode_is_rejected():
    metadata = make_metadata(
        creation_time="2026-08-10T14:23:15", timecode="00:00:12:00"
    )
    assert refined_creation_time(metadata) is None


def test_midnight_wraparound():
    metadata = make_metadata(
        creation_time="2026-08-11T00:01:00", timecode="23:59:30:00"
    )
    assert refined_creation_time(metadata) == "2026-08-10T23:59:30"


def test_missing_pieces_return_none():
    assert refined_creation_time(make_metadata(timecode="14:00:00:00")) is None
    assert refined_creation_time(
        make_metadata(creation_time="2026-08-10T14:23:15")
    ) is None


def test_probe_timecode_tag_lookup():
    probe = ProbeResult(
        format=FormatInfo(format_name="mov"),
        streams=[
            StreamInfo(index=0, codec_type="video", codec_name="hevc"),
            StreamInfo(
                index=2, codec_type="data", codec_name="tmcd",
                tags={"timecode": "14:23:05:11"},
            ),
        ],
    )
    assert probe.timecode == "14:23:05:11"
