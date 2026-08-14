"""FCPXML export.

References ORIGINAL camera media — a D-Log2 clip points at the D-Log2 file
(AGENT.md §7/§58). Times are expressed as frame-aligned rationals.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape  # noqa: F401  (kept for callers)

from .model import Timeline


def _frame_rate_fraction(fps: float) -> Fraction:
    """Map float fps to the exact broadcast rational."""
    ntsc = {
        23.976: Fraction(24000, 1001),
        29.97: Fraction(30000, 1001),
        59.94: Fraction(60000, 1001),
    }
    for approx, exact in ntsc.items():
        if abs(fps - approx) < 0.01:
            return exact
    return Fraction(round(fps), 1)


def _rational_time(seconds: float, rate: Fraction) -> str:
    """Frame-aligned rational time string like '3003/30000s'."""
    frames = round(seconds * rate)
    value = frames * rate.denominator
    return f"{value}/{rate.numerator}s" if rate.numerator != 1 else f"{value}s"


def timeline_to_fcpxml(timeline: Timeline) -> str:
    rate = _frame_rate_fraction(timeline.fps)
    frame_duration = f"{rate.denominator}/{rate.numerator}s"

    root = ET.Element("fcpxml", version="1.10")
    resources = ET.SubElement(root, "resources")

    fmt = ET.SubElement(
        resources, "format",
        id="r1", name=f"FFVideoFormat_{rate}", frameDuration=frame_duration,
        width=str(timeline.width), height=str(timeline.height),
    )
    del fmt

    asset_ids: dict[str, str] = {}
    for clip in timeline.clips:
        if clip.original_path not in asset_ids:
            asset_id = f"r{len(asset_ids) + 2}"
            asset_ids[clip.original_path] = asset_id
            asset = ET.SubElement(
                resources, "asset",
                id=asset_id,
                name=Path(clip.original_path).name,
                start="0s",
                hasVideo="1",
                hasAudio="1",
                format="r1",
            )
            ET.SubElement(
                asset, "media-rep",
                kind="original-media",
                src=Path(clip.original_path).resolve().as_uri(),
            )

    # Captions become editable connected titles in the NLE — the burned-in
    # preview look is a draft; final typography belongs to the human editor.
    title_effect_id: str | None = None
    if any(c.caption is not None for c in timeline.clips):
        title_effect_id = f"r{len(asset_ids) + 2}"
        ET.SubElement(
            resources, "effect",
            id=title_effect_id,
            name="Basic Title",
            uid=".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti",
        )

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="AI Director")
    project = ET.SubElement(event, "project", name=timeline.name)
    sequence = ET.SubElement(
        project, "sequence",
        format="r1",
        duration=_rational_time(timeline.duration, rate),
        tcStart="0s",
    )
    spine = ET.SubElement(sequence, "spine")

    for i, clip in enumerate(timeline.clips):
        element = ET.SubElement(
            spine, "asset-clip",
            ref=asset_ids[clip.original_path],
            name=Path(clip.original_path).stem,
            offset=_rational_time(clip.timeline_start, rate),
            start=_rational_time(clip.source_in, rate),
            duration=_rational_time(clip.duration, rate),
            format="r1",
        )
        if clip.caption is not None and title_effect_id is not None:
            caption_text = "\n".join(
                line for line in (clip.caption.text, clip.caption.secondary) if line
            )
            title = ET.SubElement(
                element, "title",
                ref=title_effect_id,
                name=caption_text.splitlines()[0][:40],
                lane="1",
                # Connected clips use the parent's source time base; aligning
                # with the parent's `start` puts the title at the cut.
                offset=_rational_time(clip.source_in, rate),
                duration=_rational_time(
                    min(clip.caption.duration, clip.duration), rate
                ),
            )
            text_el = ET.SubElement(title, "text")
            style_ref = f"ts{i}"
            style_run = ET.SubElement(text_el, "text-style", ref=style_ref)
            style_run.text = caption_text
            style_def = ET.SubElement(title, "text-style-def", id=style_ref)
            ET.SubElement(
                style_def, "text-style",
                font="Helvetica",
                fontSize="60",
                fontColor="1 1 1 1",
                alignment="center",
            )
        if clip.reason:
            note = ET.SubElement(element, "note")
            note.text = f"[{clip.story_beat}] {clip.reason}"

    ET.indent(root)
    body = ET.tostring(root, encoding="unicode")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n" + body + "\n"
    )


def export_fcpxml(timeline: Timeline, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(timeline_to_fcpxml(timeline), encoding="utf-8")
    return output
