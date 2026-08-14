"""Spoken-word subtitles: extraction, plan fill, render, SRT/FCPXML export."""

from __future__ import annotations

from pathlib import Path

import pytest

from aidirector.ai.schemas import (
    Provenance,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from aidirector.director.orchestrator import fill_subtitles
from aidirector.director.schemas import (
    EditClip,
    EditPlan,
    EditPlanIntent,
    SubtitleLine,
)
from aidirector.memory.models import SegmentRecord
from aidirector.perception.speech import subtitle_lines_for_span
from aidirector.timeline.model import Timeline, TimelineClip
from aidirector.timeline.srt import timeline_to_srt
from tests.unit.test_memory import make_asset


def make_transcript() -> Transcript:
    return Transcript(
        language="ja", duration=20.0,
        segments=[
            TranscriptSegment(
                start=1.0, end=4.0, text="駅に着きました",
                words=[
                    TranscriptWord(start=1.0, end=2.0, text="駅に"),
                    TranscriptWord(start=2.0, end=3.0, text="着き"),
                    TranscriptWord(start=3.0, end=4.0, text="ました"),
                ],
            ),
            TranscriptSegment(start=8.0, end=10.0, text="いい天気です", words=[]),
            TranscriptSegment(start=15.0, end=15.1, text="あ", words=[]),  # too short
        ],
    )


def test_span_extraction_full_overlap():
    lines = subtitle_lines_for_span(make_transcript(), 0.0, 12.0)
    assert lines == [
        (1.0, 4.0, "駅に着きました"),
        (8.0, 10.0, "いい天気です"),
    ]


def test_span_extraction_partial_overlap_uses_words():
    # Clip covers only 2.5-12s: first word "駅に" (1-2s) is outside.
    lines = subtitle_lines_for_span(make_transcript(), 2.5, 12.0)
    assert lines[0] == (2.5, 4.0, "着きました")


def test_span_extraction_edges():
    assert subtitle_lines_for_span(None, 0, 10) == []
    assert subtitle_lines_for_span(make_transcript(), 5.0, 7.0) == []
    # too-short line dropped
    assert subtitle_lines_for_span(make_transcript(), 14.0, 16.0) == []


def test_fill_subtitles_from_memory(memory):
    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)
    memory.replace_segments(
        asset.id,
        [SegmentRecord(id="seg_s", asset_id=asset.id, idx=0, start=0.0, end=12.0)],
    )
    memory.save_transcript(
        asset.id, make_transcript(), Provenance(provider="mock", model="mock")
    )
    plan = EditPlan(
        intent=EditPlanIntent(target_duration=30),
        clips=[EditClip(segment_id="seg_s", source_in=0.0, source_out=6.0)],
    )
    plan = fill_subtitles(plan, memory)
    assert len(plan.clips[0].subtitles) == 1
    assert plan.clips[0].subtitles[0].text == "駅に着きました"


def make_subtitled_timeline() -> Timeline:
    return Timeline(
        fps=30.0, width=1280, height=720,
        clips=[
            TimelineClip(
                index=0, segment_id="s1", original_path="/f/a.mp4",
                source_in=10.0, source_out=16.0, timeline_start=0.0,
                subtitles=[SubtitleLine(start=11.0, end=13.5, text="駅に着きました")],
            ),
            TimelineClip(
                index=1, segment_id="s2", original_path="/f/b.mp4",
                source_in=0.0, source_out=4.0, timeline_start=6.0,
                subtitles=[SubtitleLine(start=1.0, end=3.0, text="いい天気です")],
            ),
        ],
    )


def test_srt_export_timeline_time():
    srt = timeline_to_srt(make_subtitled_timeline())
    blocks = srt.strip().split("\n\n")
    assert len(blocks) == 2
    # clip0: source 11.0 -> timeline 1.0
    assert "00:00:01,000 --> 00:00:03,500" in blocks[0]
    assert "駅に着きました" in blocks[0]
    # clip1: source 1.0 + timeline_start 6.0 -> 7.0
    assert "00:00:07,000 --> 00:00:09,000" in blocks[1]


def test_fcpxml_subtitles_on_lane2():
    from aidirector.timeline.fcpxml import timeline_to_fcpxml

    xml = timeline_to_fcpxml(make_subtitled_timeline())
    assert 'lane="2"' in xml
    assert "駅に着きました" in xml and "いい天気です" in xml


def test_subtitle_validation(memory):
    from aidirector.errors import ValidationError
    from aidirector.timeline.validate import validate_edit_plan

    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)
    memory.replace_segments(
        asset.id,
        [SegmentRecord(id="seg_v", asset_id=asset.id, idx=0, start=0.0, end=10.0)],
    )
    plan = EditPlan(
        intent=EditPlanIntent(target_duration=30),
        clips=[EditClip(
            segment_id="seg_v", source_in=0.0, source_out=5.0,
            subtitles=[SubtitleLine(start=3.0, end=2.0, text="x")],
        )],
    )
    with pytest.raises(ValidationError, match="subtitle 0 start >= end"):
        validate_edit_plan(plan, memory)


def test_subtitle_overlays(tmp_path):
    from aidirector.timeline.captions import build_subtitle_overlays, find_caption_font

    if find_caption_font() is None:
        pytest.skip("no font available")
    subtitles = [
        SubtitleLine(start=11.0, end=13.5, text="駅に着きました、いい天気ですね"),
        SubtitleLine(start=15.9, end=16.05, text="短すぎ"),  # < 0.2s after clamp
    ]
    overlays = build_subtitle_overlays(
        subtitles, source_in=10.0, clip_duration=6.0,
        canvas_w=1280, canvas_h=720, work_dir=tmp_path, clip_index=0,
    )
    assert len(overlays) == 1
    overlay = overlays[0]
    assert overlay.png_path.is_file()
    assert overlay.rel_start == pytest.approx(1.0)
    assert overlay.rel_end == pytest.approx(3.5)
    assert overlay.enable_expr == "between(t,1.000,3.500)"

    from PIL import Image

    image = Image.open(overlay.png_path)
    assert image.size == (1280, 720)
    assert image.getextrema()[3][1] > 0  # something was drawn
