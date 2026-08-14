"""CMX3600 EDL export. References original camera media by clip name."""

from __future__ import annotations

from pathlib import Path

from .model import Timeline


def _timecode(seconds: float, fps: float) -> str:
    fps_int = max(1, round(fps))
    total_frames = round(seconds * fps_int)
    frames = total_frames % fps_int
    total_seconds = total_frames // fps_int
    secs = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"


def timeline_to_edl(timeline: Timeline) -> str:
    fps = timeline.fps
    lines = [f"TITLE: {timeline.name}", "FCM: NON-DROP FRAME", ""]
    for clip in timeline.clips:
        number = f"{clip.index + 1:03d}"
        source_in = _timecode(clip.source_in, fps)
        source_out = _timecode(clip.source_out, fps)
        record_in = _timecode(clip.timeline_start, fps)
        record_out = _timecode(clip.timeline_end, fps)
        lines.append(
            f"{number}  AX       V     C        "
            f"{source_in} {source_out} {record_in} {record_out}"
        )
        lines.append(f"* FROM CLIP NAME: {Path(clip.original_path).name}")
        if clip.caption is not None:
            caption_text = " / ".join(
                line for line in (clip.caption.text, clip.caption.secondary) if line
            )
            # CMX3600 has no title concept; keep it as a comment so the
            # information survives the round trip.
            lines.append(f"* CAPTION: {caption_text}")
        if clip.reason:
            lines.append(f"* COMMENT: [{clip.story_beat}] {clip.reason}")
        lines.append("")
    return "\n".join(lines)


def export_edl(timeline: Timeline, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(timeline_to_edl(timeline), encoding="utf-8")
    return output
