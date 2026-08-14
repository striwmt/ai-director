"""SubRip (.srt) export of spoken-word subtitles in timeline time."""

from __future__ import annotations

from pathlib import Path

from .model import Timeline


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def timeline_to_srt(timeline: Timeline) -> str:
    entries: list[tuple[float, float, str]] = []
    for clip in timeline.clips:
        for line in clip.subtitles:
            start = clip.timeline_start + max(0.0, line.start - clip.source_in)
            end = clip.timeline_start + min(
                clip.duration, line.end - clip.source_in
            )
            if end - start >= 0.2 and line.text.strip():
                entries.append((start, end, line.text.strip()))
    entries.sort(key=lambda e: e[0])

    blocks = []
    for index, (start, end, text) in enumerate(entries, start=1):
        blocks.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
    return "\n".join(blocks)


def export_srt(timeline: Timeline, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(timeline_to_srt(timeline), encoding="utf-8")
    return output
