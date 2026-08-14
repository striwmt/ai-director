"""Audio helpers for perception and talk editing tools."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from ..process import run_command

_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


class SilenceSpan(BaseModel):
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def find_silences(
    media: Path,
    *,
    noise_db: float = -35.0,
    min_seconds: float = 0.5,
) -> list[SilenceSpan]:
    """Silence spans over the whole file (talk editing tool, AGENT.md §60)."""
    result = run_command(
        [
            "ffmpeg", "-hide_banner", "-i", str(media),
            "-af", f"silencedetect=noise={noise_db}dB:d={min_seconds}",
            "-f", "null", "-",
        ],
        timeout=1800.0,
        check=False,
    )
    starts = [float(m.group(1)) for m in _SILENCE_START_RE.finditer(result.stderr)]
    ends = [float(m.group(1)) for m in _SILENCE_END_RE.finditer(result.stderr)]
    spans = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else start
        if end > start:
            spans.append(SilenceSpan(start=start, end=end))
    return spans


def extract_audio_wav(media: Path, output: Path, sample_rate: int = 16000) -> Path:
    """Extract mono WAV for ASR."""
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(media),
            "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-c:a", "pcm_s16le",
            str(output),
        ],
        timeout=1800.0,
    )
    return output
