"""Golden tests: Edit Plan -> FCPXML / EDL / OTIO (AGENT.md §68)."""

from __future__ import annotations

import json
from pathlib import Path

from aidirector.director.schemas import ClipAudio, ClipCaption, ClipTransition
from aidirector.timeline.edl import timeline_to_edl
from aidirector.timeline.fcpxml import timeline_to_fcpxml
from aidirector.timeline.model import Timeline, TimelineClip
from aidirector.timeline.otio import timeline_to_otio

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"


def fixed_timeline() -> Timeline:
    return Timeline(
        name="Golden Timeline",
        fps=29.97,
        clips=[
            TimelineClip(
                index=0,
                segment_id="seg_0042_03",
                original_path="/footage/DJI_0042.MP4",
                proxy_path="/cache/ast1_analysis.mp4",
                source_in=12.4,
                source_out=17.1,
                timeline_start=0.0,
                story_beat="opening",
                caption=ClipCaption(text="佐原", secondary="2026-08-01 09:30"),
                reason="rain and townscape together",
            ),
            TimelineClip(
                index=1,
                segment_id="seg_0043_01",
                original_path="/footage/DJI_0043.MP4",
                source_in=2.0,
                source_out=6.5,
                timeline_start=4.7,
                audio=ClipAudio(mode="muted"),
                transition=ClipTransition(type="crossfade", duration=0.5),
                story_beat="exploration",
                reason="entering the shop",
            ),
        ],
    )


def _check_golden(name: str, actual: str) -> None:
    golden_path = GOLDEN_DIR / name
    if not golden_path.exists():  # first run generates the golden file
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(actual, encoding="utf-8")
    expected = golden_path.read_text(encoding="utf-8")
    assert actual == expected, f"{name} drifted from golden output"


def test_fcpxml_golden():
    _check_golden("golden_timeline.fcpxml", timeline_to_fcpxml(fixed_timeline()))


def test_edl_golden():
    _check_golden("golden_timeline.edl", timeline_to_edl(fixed_timeline()))


def test_otio_golden():
    actual = json.dumps(timeline_to_otio(fixed_timeline()), indent=2, ensure_ascii=False)
    _check_golden("golden_timeline.otio", actual)


def test_fcpxml_references_original_media():
    xml = timeline_to_fcpxml(fixed_timeline())
    # NLE export must reference the ORIGINAL camera file, never the proxy.
    assert "DJI_0042.MP4" in xml
    assert "ast1_analysis" not in xml


def test_edl_timecodes():
    edl = timeline_to_edl(fixed_timeline())
    assert "TITLE: Golden Timeline" in edl
    assert "* FROM CLIP NAME: DJI_0042.MP4" in edl
    # 12.4s @30fps -> 00:00:12:12
    assert "00:00:12:12" in edl


def test_captions_carried_into_exports():
    timeline = fixed_timeline()
    xml = timeline_to_fcpxml(timeline)
    # Editable connected title, not burned-in text.
    assert "<title" in xml and 'lane="1"' in xml
    assert "佐原" in xml and "2026-08-01 09:30" in xml
    assert "Basic Title" in xml
    # Second clip has no caption -> exactly one title element.
    assert xml.count("<title") == 1

    edl = timeline_to_edl(timeline)
    assert "* CAPTION: 佐原 / 2026-08-01 09:30" in edl

    otio = timeline_to_otio(timeline)
    first = otio["tracks"]["children"][0]["children"][0]
    assert first["metadata"]["aidirector"]["caption"]["text"] == "佐原"
    assert first["markers"][0]["name"].startswith("CAPTION: 佐原")
    second = otio["tracks"]["children"][0]["children"][1]
    assert second["markers"] == []
