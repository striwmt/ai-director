"""Representative frame extraction for segments (AGENT.md §69)."""

from __future__ import annotations

from pathlib import Path

from ..config import AppConfig
from ..logging import get_logger
from ..memory.models import FrameRecord, SegmentRecord
from ..process import run_command

log = get_logger("media.frames")


def representative_timestamps(segment: SegmentRecord, count: int) -> list[float]:
    """Evenly spaced timestamps inside the segment, avoiding the exact edges."""
    duration = segment.duration
    if duration <= 0:
        return [segment.start]
    count = max(1, count)
    step = duration / (count + 1)
    return [round(segment.start + step * (i + 1), 3) for i in range(count)]


def extract_frames(
    segment: SegmentRecord,
    video: Path,
    config: AppConfig,
) -> list[FrameRecord]:
    """Extract representative JPEG frames from the (color-managed) proxy."""
    frames: list[FrameRecord] = []
    out_dir = config.paths.frames_dir / segment.asset_id
    out_dir.mkdir(parents=True, exist_ok=True)
    height = config.segmentation.frame_height

    for i, ts in enumerate(
        representative_timestamps(segment, config.segmentation.frames_per_segment)
    ):
        out = out_dir / f"{segment.id}_{i}.jpg"
        if not out.is_file():
            run_command(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{ts:.3f}", "-i", str(video),
                    "-frames:v", "1",
                    "-vf", f"scale=-2:{height}",
                    "-q:v", "3",
                    str(out),
                ],
                timeout=120.0,
            )
        if out.is_file():
            frames.append(FrameRecord(segment_id=segment.id, timestamp=ts, path=str(out)))
    return frames
