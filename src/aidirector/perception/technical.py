"""Deterministic technical analysis of segments (signal layer, AGENT.md §21).

Everything here is measured with ffmpeg filters on the analysis proxy —
no AI involved. Sampled at low fps to stay cheap (§69).
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path

from ..logging import get_logger
from ..memory.models import SegmentRecord, TechnicalFeatures
from ..process import run_command

log = get_logger("perception.technical")

_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")
_LOUDNESS_RE = re.compile(r"I:\s*(-?[0-9.]+)\s*LUFS")
_SILENCE_DUR_RE = re.compile(r"silence_duration:\s*([0-9.]+)")

_SAMPLE_FPS = 2


def _signalstats(video: Path, start: float, duration: float) -> list[float]:
    result = run_command(
        [
            "ffmpeg", "-hide_banner",
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
            "-i", str(video),
            "-vf", f"fps={_SAMPLE_FPS},signalstats,metadata=print",
            "-f", "null", "-",
        ],
        timeout=300.0,
        check=False,
    )
    return [float(m.group(1)) for m in _YAVG_RE.finditer(result.stderr)]


def _sharpness(video: Path, start: float, duration: float) -> float | None:
    """Mean edge energy: luma average of Sobel-filtered sampled frames."""
    result = run_command(
        [
            "ffmpeg", "-hide_banner",
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
            "-i", str(video),
            "-vf", f"fps={_SAMPLE_FPS},format=gray,sobel,signalstats,metadata=print",
            "-f", "null", "-",
        ],
        timeout=300.0,
        check=False,
    )
    values = [float(m.group(1)) for m in _YAVG_RE.finditer(result.stderr)]
    return round(statistics.fmean(values), 3) if values else None


def _audio_stats(
    video: Path, start: float, duration: float,
    noise_db: float, min_silence: float,
) -> tuple[float | None, float | None]:
    """Return (integrated loudness LUFS, silence ratio 0..1)."""
    result = run_command(
        [
            "ffmpeg", "-hide_banner",
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
            "-i", str(video),
            "-af", f"silencedetect=noise={noise_db}dB:d={min_silence},ebur128",
            "-f", "null", "-",
        ],
        timeout=300.0,
        check=False,
    )
    loudness = None
    matches = _LOUDNESS_RE.findall(result.stderr)
    if matches:
        loudness = float(matches[-1])
    silence_total = sum(float(m) for m in _SILENCE_DUR_RE.findall(result.stderr))
    silence_ratio = min(1.0, silence_total / duration) if duration > 0 else None
    return loudness, silence_ratio


def analyze_segment_technical(
    segment: SegmentRecord,
    video: Path,
    *,
    has_audio: bool,
    silence_noise_db: float = -35.0,
    silence_min_seconds: float = 0.3,
) -> TechnicalFeatures:
    duration = max(segment.duration, 0.1)
    yavg = _signalstats(video, segment.start, duration)

    mean_luma = round(statistics.fmean(yavg), 2) if yavg else None
    luma_stddev = round(statistics.pstdev(yavg), 2) if len(yavg) > 1 else 0.0 if yavg else None
    clipped_high = round(sum(1 for v in yavg if v > 230) / len(yavg), 3) if yavg else None
    clipped_low = round(sum(1 for v in yavg if v < 16) / len(yavg), 3) if yavg else None

    sharpness = _sharpness(video, segment.start, duration)

    loudness = silence_ratio = None
    if has_audio:
        loudness, silence_ratio = _audio_stats(
            video, segment.start, duration, silence_noise_db, silence_min_seconds
        )

    flags: list[str] = []
    if clipped_high is not None and clipped_high > 0.5:
        flags.append("over_exposed")
    if clipped_low is not None and clipped_low > 0.5:
        flags.append("under_exposed")
    if sharpness is not None and sharpness < 4.0:
        flags.append("soft_focus")
    if silence_ratio is not None and silence_ratio > 0.9:
        flags.append("silent")
    if loudness is not None and loudness > -8.0:
        flags.append("loud_audio")

    return TechnicalFeatures(
        mean_luma=mean_luma,
        luma_stddev=luma_stddev,
        clipped_highlight_ratio=clipped_high,
        clipped_shadow_ratio=clipped_low,
        sharpness=sharpness,
        loudness_lufs=loudness,
        silence_ratio=silence_ratio,
        speech_likely=(silence_ratio is not None and silence_ratio < 0.5) if has_audio else False,
        quality_flags=flags,
    )
