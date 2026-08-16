"""Semantic segmentation (AGENT.md §19).

Boundary candidates: hard cuts (ffmpeg scene detection), silence
boundaries, long-take subdivision, recording boundaries. Deterministic
signal work stays out of AI (§21).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from ..config import SegmentationConfig
from ..logging import get_logger
from ..memory.models import SegmentRecord
from ..process import run_command

log = get_logger("media.segment")

_SCENE_RE = re.compile(r"pts_time[:=]\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


def detect_scene_changes(video: Path, threshold: float) -> list[float]:
    """Timestamps (seconds) of hard cuts via ffmpeg scene-change scores."""
    result = run_command(
        [
            "ffmpeg", "-hide_banner", "-i", str(video),
            "-vf", f"select='gt(scene,{threshold})',showinfo",
            "-f", "null", "-",
        ],
        timeout=1800.0,
        check=False,  # ffmpeg exits non-zero on some containers; parse anyway
    )
    times = [float(m.group(1)) for m in _SCENE_RE.finditer(result.stderr)]
    return sorted(set(times))


def detect_silences(video: Path, noise_db: float, min_seconds: float) -> list[float]:
    """Silence-end timestamps — natural speech boundaries."""
    result = run_command(
        [
            "ffmpeg", "-hide_banner", "-i", str(video),
            "-af", f"silencedetect=noise={noise_db}dB:d={min_seconds}",
            "-f", "null", "-",
        ],
        timeout=1800.0,
        check=False,
    )
    if "Stream mapping" not in result.stderr and "silence" not in result.stderr:
        return []
    return sorted({float(m.group(1)) for m in _SILENCE_END_RE.finditer(result.stderr)})


def build_segments(
    asset_id: str,
    duration: float,
    boundaries: dict[str, list[float]],
    config: SegmentationConfig,
) -> list[SegmentRecord]:
    """Merge boundary candidates into clean segments.

    Pure function (unit-testable): takes boundary timestamps per source,
    returns ordered segments obeying min/max length constraints.
    """
    if duration <= 0:
        return []

    # Collect candidate cut points with their reasons.
    candidates: dict[float, set[str]] = {}
    for reason, times in boundaries.items():
        for t in times:
            if 0.0 < t < duration:
                key = round(t, 3)
                candidates.setdefault(key, set()).add(reason)

    # Drop candidates too close to a neighbor (keep the earlier one).
    cuts: list[tuple[float, set[str]]] = []
    for t in sorted(candidates):
        if cuts and t - cuts[-1][0] < config.min_segment_seconds:
            cuts[-1][1].update(candidates[t])
            continue
        cuts.append((t, set(candidates[t])))

    # Build raw spans between cuts.
    spans: list[tuple[float, float, list[str]]] = []
    prev = 0.0
    prev_reasons = ["recording_boundary"]
    for t, reasons in cuts:
        spans.append((prev, t, prev_reasons))
        prev = t
        prev_reasons = sorted(reasons)
    spans.append((prev, duration, prev_reasons))

    # Subdivide long takes; merge spans shorter than min into the previous.
    final: list[tuple[float, float, list[str]]] = []
    for start, end, reasons in spans:
        length = end - start
        if length < config.min_segment_seconds and final:
            fs, fe, fr = final[-1]
            final[-1] = (fs, end, fr)
            continue
        if length > config.max_segment_seconds:
            n_parts = int(length // config.max_segment_seconds) + 1
            part = length / n_parts
            for i in range(n_parts):
                sub_start = start + i * part
                sub_end = min(end, sub_start + part)
                sub_reasons = reasons if i == 0 else ["long_take_subdivision"]
                final.append((sub_start, sub_end, sub_reasons))
        else:
            final.append((start, end, reasons))

    if not final:
        final = [(0.0, duration, ["recording_boundary"])]

    return [
        SegmentRecord(
            id=f"seg_{uuid.uuid4().hex[:12]}",
            asset_id=asset_id,
            idx=i,
            start=round(start, 3),
            end=round(end, 3),
            boundary_reasons=reasons,
        )
        for i, (start, end, reasons) in enumerate(final)
    ]


def segment_video(
    asset_id: str,
    video: Path,
    duration: float,
    config: SegmentationConfig,
) -> list[SegmentRecord]:
    scenes = detect_scene_changes(video, config.scene_threshold)
    silences = detect_silences(video, config.silence_noise_db, config.silence_min_seconds)
    segments = build_segments(
        asset_id,
        duration,
        {"hard_cut": scenes, "silence": silences},
        config,
    )
    log.info(
        "segmented %s: %d segments (%d scene changes, %d silence boundaries"
        " — 0 of each is normal for a short single-shot clip)",
        video.name, len(segments), len(scenes), len(silences),
    )
    return segments
