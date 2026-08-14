"""Scene-change caption generation and rendering filters."""

from pathlib import Path

import pytest

from aidirector.director.orchestrator import build_caption, sequence_to_edit_plan
from aidirector.director.schemas import (
    ClipCaption,
    EditPlanIntent,
    SequenceClip,
    SequencePlan,
    StoryPlan,
)
from aidirector.perception.interpretation import SegmentUnderstanding
from aidirector.timeline.captions import build_caption_overlay, find_caption_font


def make_understanding(seg_id: str, recorded_at: str | None) -> SegmentUnderstanding:
    return SegmentUnderstanding(
        segment_id=seg_id, asset_id="ast_x", asset_name="clip.mp4",
        start=0.0, end=10.0, duration=10.0, recorded_at=recorded_at,
    )


def make_clip(seg_id: str, beat: str, location: str | None = None) -> SequenceClip:
    return SequenceClip(
        segment_id=seg_id, source_in=0.0, source_out=5.0,
        story_beat=beat, location=location, reason="r",
    )


STORY = StoryPlan(concept="c", tone="calm", story_arc=["a"])
INTENT = EditPlanIntent(target_duration=30)


def test_build_caption_place_and_time():
    caption = build_caption(
        make_clip("s1", "hook", location="平等院"),
        make_understanding("s1", "2026-08-01T09:30:00+00:00"),
    )
    assert caption.text == "平等院"
    assert caption.secondary == "2026-08-01 09:30"


def test_build_caption_time_only_promotes_to_main():
    caption = build_caption(
        make_clip("s1", "hook"),
        make_understanding("s1", "2026-08-01T09:30:00+00:00"),
    )
    assert caption.text == "2026-08-01 09:30"
    assert caption.secondary == ""


def test_caption_format_user_template():
    clip = make_clip("s1", "hook", location="川越駅")
    u = make_understanding("s1", "2026-08-01T09:12:00+00:00")
    caption = build_caption(clip, u, "{HH}:{MM} {PLACE}")
    assert caption.text == "09:12 川越駅"
    assert caption.secondary == ""


def test_caption_format_missing_time_drops_orphan_punctuation():
    clip = make_clip("s1", "hook", location="川越駅")
    caption = build_caption(clip, make_understanding("s1", None), "{HH}:{MM} {PLACE}")
    # "{HH}:{MM}" collapses to ":" which is dropped, leaving only the place.
    assert caption.text == "川越駅"


def test_caption_format_two_lines_and_tokens():
    clip = make_clip("s1", "hook", location="平等院")
    u = make_understanding("s1", "2026-08-01T11:30:00+00:00")
    caption = build_caption(clip, u, "{PLACE}\\n{MO}/{DD} {HH}:{MM}")
    assert caption.text == "平等院"
    assert caption.secondary == "08/01 11:30"


def test_caption_format_no_facts_returns_none():
    clip = make_clip("s1", "hook")
    assert build_caption(clip, make_understanding("s1", None), "{HH}:{MM} {PLACE}") is None


def test_build_caption_no_facts_no_caption():
    assert build_caption(make_clip("s1", "hook"), make_understanding("s1", None)) is None
    assert build_caption(make_clip("s1", "hook"), None) is None


def test_captions_mode_beats_only_on_beat_change():
    plan = SequencePlan(clips=[
        make_clip("s1", "hook", "駅"),
        make_clip("s2", "hook", "駅前"),      # same beat -> no caption
        make_clip("s3", "walk", "川沿い"),     # new beat -> caption
    ])
    by_id = {f"s{i}": make_understanding(f"s{i}", None) for i in (1, 2, 3)}
    edit = sequence_to_edit_plan(plan, STORY, INTENT, captions="beats", segments_by_id=by_id)
    assert edit.clips[0].caption is not None and edit.clips[0].caption.text == "駅"
    assert edit.clips[1].caption is None
    assert edit.clips[2].caption is not None and edit.clips[2].caption.text == "川沿い"


def test_captions_mode_none_and_clips():
    plan = SequencePlan(clips=[make_clip("s1", "hook", "駅"), make_clip("s2", "hook", "町")])
    by_id = {f"s{i}": make_understanding(f"s{i}", None) for i in (1, 2)}
    off = sequence_to_edit_plan(plan, STORY, INTENT, captions="none", segments_by_id=by_id)
    assert all(c.caption is None for c in off.clips)
    every = sequence_to_edit_plan(plan, STORY, INTENT, captions="clips", segments_by_id=by_id)
    assert all(c.caption is not None for c in every.clips)


def test_caption_overlay_renders_png(tmp_path):
    font = find_caption_font(needs_cjk=True)
    if font is None:
        pytest.skip("no font available via fontconfig")
    caption = ClipCaption(text="平等院", secondary="2026-08-01 09:30")
    overlay = build_caption_overlay(
        caption, 1280, 720, clip_duration=6.0, work_dir=tmp_path, clip_index=0,
    )
    assert overlay is not None
    assert overlay.png_path.is_file()

    from PIL import Image

    image = Image.open(overlay.png_path)
    assert image.size == (1280, 720)
    assert image.mode == "RGBA"
    # Text was actually drawn: some pixels are non-transparent.
    assert image.getextrema()[3][1] > 0

    assert "fade=t=in" in overlay.filter_snippet
    assert "fade=t=out" in overlay.filter_snippet
    assert overlay.enable_expr.startswith("between(t,")
    # show fits inside the clip
    assert overlay.show_seconds <= 6.0


def test_caption_overlay_skip_short_clip_and_empty(tmp_path):
    font = find_caption_font(needs_cjk=False) or "/nonexistent.ttf"
    caption = ClipCaption(text="eki")
    assert build_caption_overlay(
        caption, 1280, 720, 1.0, tmp_path, 0, font_file=font
    ) is None
    empty = ClipCaption(text="  ", secondary="")
    assert build_caption_overlay(
        empty, 1280, 720, 6.0, tmp_path, 1, font_file=font
    ) is None


def test_caption_validation(memory):
    from aidirector.director.schemas import EditClip, EditPlan
    from aidirector.errors import ValidationError
    from aidirector.memory.models import SegmentRecord
    from aidirector.timeline.validate import validate_edit_plan
    from tests.unit.test_memory import make_asset

    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)
    memory.replace_segments(
        asset.id,
        [SegmentRecord(id="seg_ok", asset_id=asset.id, idx=0, start=0.0, end=10.0)],
    )
    plan = EditPlan(intent=INTENT, clips=[
        EditClip(segment_id="seg_ok", source_in=0.0, source_out=4.0,
                 caption=ClipCaption(text="  ", secondary="")),
    ])
    with pytest.raises(ValidationError, match="caption present but empty"):
        validate_edit_plan(plan, memory)
