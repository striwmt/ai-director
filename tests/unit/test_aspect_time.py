"""Aspect ratio, rotation and chronology handling."""

from pathlib import Path

from aidirector.media.metadata import MediaMetadata
from aidirector.media.probe import parse_ffprobe_json
from aidirector.memory.models import SegmentRecord
from aidirector.perception.interpretation import (
    build_understanding,
    segment_recorded_at,
)
from aidirector.timeline.compiler import choose_canvas
from tests.unit.test_memory import make_asset


def test_probe_parses_sar_dar_rotation():
    data = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "sample_aspect_ratio": "1:1",
                "display_aspect_ratio": "16:9",
                "side_data_list": [
                    {"side_data_type": "Display Matrix", "rotation": -90}
                ],
            }
        ],
        "format": {},
    }
    stream = parse_ffprobe_json(data).video_stream
    assert stream.sample_aspect_ratio == "1:1"
    assert stream.display_aspect_ratio == "16:9"
    assert stream.rotation == 270  # normalized to [0, 360)


def test_display_size_rotation():
    # Phone clip: stored landscape + 90 degree display matrix -> portrait.
    meta = MediaMetadata(width=1920, height=1080, rotation=90)
    assert meta.display_size == (1080, 1920)
    assert meta.is_portrait


def test_display_size_sar():
    # Anamorphic: 1440x1080 with 4:3 SAR displays as 1920x1080.
    meta = MediaMetadata(width=1440, height=1080, sample_aspect_ratio="4:3")
    assert meta.display_size == (1920, 1080)
    assert not meta.is_portrait


def test_display_size_missing():
    assert MediaMetadata().display_size is None
    assert not MediaMetadata().is_portrait


def test_segment_recorded_at():
    assert (
        segment_recorded_at("2026-08-01T09:30:00.000000Z", 65.0)
        == "2026-08-01T09:31:05+00:00"
    )
    assert segment_recorded_at(None, 10.0) is None
    assert segment_recorded_at("not-a-date", 10.0) is None


def test_choose_canvas_majority_by_duration():
    landscape = (1920, 1080)
    portrait = (1080, 1920)
    # 25s landscape vs 12s portrait -> landscape canvas at best landscape size
    assert choose_canvas([(landscape, 20.0), (portrait, 12.0), ((1280, 720), 5.0)]) == landscape
    # portrait dominates -> portrait canvas
    assert choose_canvas([(landscape, 5.0), (portrait, 30.0)]) == portrait
    # unknown sizes -> default
    assert choose_canvas([(None, 10.0)]) == (1920, 1080)
    # odd dimensions are rounded down to even for h264
    assert choose_canvas([((405, 719), 10.0)]) == (404, 718)


def test_project_segments_chronological(memory):
    project = memory.get_or_create_project("trip", Path("/footage"))
    # b.mp4 recorded EARLIER than a.mp4; undated c.mp4 sorts last.
    early = make_asset(project.id, "b.mp4")
    early.metadata.creation_time = "2026-08-01T08:00:00Z"
    late = make_asset(project.id, "a.mp4")
    late.metadata.creation_time = "2026-08-01T09:00:00Z"
    undated = make_asset(project.id, "c.mp4")
    for asset in (early, late, undated):
        memory.upsert_asset(asset)
        memory.replace_segments(
            asset.id,
            [SegmentRecord(id=f"seg_{asset.file_name}", asset_id=asset.id,
                           idx=0, start=0.0, end=5.0)],
        )
    order = [s.id for s in memory.list_project_segments(project.id)]
    assert order == ["seg_b.mp4", "seg_a.mp4", "seg_c.mp4"]


def test_understanding_carries_time_and_orientation(memory):
    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id, "phone.mp4")
    asset.metadata.creation_time = "2026-08-01T09:30:00Z"
    asset.metadata.width = 1920
    asset.metadata.height = 1080
    asset.metadata.rotation = 90
    memory.upsert_asset(asset)
    segment = SegmentRecord(id="seg_p", asset_id=asset.id, idx=0, start=30.0, end=35.0)
    memory.replace_segments(asset.id, [segment])

    u = build_understanding(segment, memory)
    assert u.recorded_at.startswith("2026-08-01T09:30:30")
    assert u.orientation == "portrait"
    line = u.to_summary_line()
    assert "shot at 2026-08-01 09:30" in line
    assert "PORTRAIT" in line
