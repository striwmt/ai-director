"""Normalized media metadata extracted from probe results.

Missing values are normal (AGENT.md §9).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from .probe import ProbeResult


class MediaMetadata(BaseModel):
    duration: float | None = None
    container: str | None = None

    video_codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    bit_depth: int | None = None
    pix_fmt: str | None = None
    time_base: str | None = None

    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    color_range: str | None = None

    sample_aspect_ratio: str | None = None
    display_aspect_ratio: str | None = None
    rotation: int = 0

    audio_codec: str | None = None
    audio_channels: int | None = None
    audio_sample_rate: int | None = None
    audio_stream_count: int = 0

    creation_time: str | None = None
    # SMPTE start timecode from the tmcd track, verbatim (e.g. "14:23:05:11").
    # Whether it represents wall-clock time is decided by
    # refined_creation_time, never assumed here.
    timecode: str | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    gps: str | None = None

    # Manufacturer-specific / unmapped tags kept verbatim for later use.
    raw_tags: dict[str, str] = Field(default_factory=dict)

    @property
    def has_video(self) -> bool:
        return self.video_codec is not None

    @property
    def has_audio(self) -> bool:
        return self.audio_stream_count > 0

    @property
    def display_size(self) -> tuple[int, int] | None:
        """As-displayed pixel size: SAR-corrected and rotation-aware.

        Phone footage stores landscape pixels plus a 90° display matrix;
        anamorphic sources store a non-square SAR. Editing decisions must use
        the displayed geometry, not the stored one.
        """
        if not self.width or not self.height:
            return None
        width, height = self.width, self.height
        if self.sample_aspect_ratio:
            try:
                num, den = (int(p) for p in self.sample_aspect_ratio.split(":"))
                if num > 0 and den > 0 and num != den:
                    width = round(width * num / den)
            except ValueError:
                pass
        if self.rotation % 180 == 90:
            width, height = height, width
        return width, height

    @property
    def is_portrait(self) -> bool:
        size = self.display_size
        return size is not None and size[1] > size[0]


_TIMECODE_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[:;.](\d{1,3})$")
# creation_time may be the END of the recording on some cameras, plus a
# little clock skew — the timecode must agree within duration + this.
_TIMECODE_SLACK_SECONDS = 300.0


def timecode_to_seconds(timecode: str | None, fps: float | None) -> float | None:
    """SMPTE timecode -> seconds since midnight, or None if unparsable.

    Frames convert via fps (30 assumed when unknown). Drop-frame values
    (";" separator) are treated like non-drop: the label-vs-realtime gap
    (~3.6s/hour) is far inside the tolerance this value is gated by.
    """
    if not timecode:
        return None
    match = _TIMECODE_RE.match(timecode.strip())
    if not match:
        return None
    hours, minutes, seconds, frames = (int(g) for g in match.groups())
    if hours > 23 or minutes > 59 or seconds > 59:
        return None
    rate = fps if fps and fps > 0 else 30.0
    if frames >= max(rate, 1):
        return None
    return hours * 3600 + minutes * 60 + seconds + frames / rate


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def refined_creation_time(metadata: MediaMetadata) -> str | None:
    """creation_time refined to frame precision by the timecode — only when
    the two clocks agree, so record-run timecodes (00:00:00:00 per take)
    and timezone-shifted clocks are never mistaken for wall-clock time
    (AGENT.md §2: code guarantees facts, doubtful values are dropped).

    Cameras like the DJI Osmo Pocket 3 write an RTC-synced free-run
    timecode; there the result is the exact recording START time even
    when creation_time was stamped at the end of the recording.
    Returns None whenever the refinement cannot be trusted.
    """
    created = _parse_iso(metadata.creation_time)
    tc_seconds = timecode_to_seconds(metadata.timecode, metadata.fps)
    if created is None or tc_seconds is None:
        return None
    midnight = created.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = [
        midnight + timedelta(days=day, seconds=tc_seconds) for day in (-1, 0, 1)
    ]
    best = min(candidates, key=lambda c: abs((c - created).total_seconds()))
    tolerance = (metadata.duration or 0.0) + _TIMECODE_SLACK_SECONDS
    if abs((best - created).total_seconds()) > tolerance:
        return None
    return best.isoformat()


def extract_metadata(probe: ProbeResult, path: Path | None = None) -> MediaMetadata:
    video = probe.video_stream
    audios = probe.audio_streams
    first_audio = audios[0] if audios else None

    raw_tags = dict(probe.format.tags)
    for stream in probe.streams:
        for key, value in stream.tags.items():
            raw_tags.setdefault(f"stream{stream.index}.{key}", value)

    return MediaMetadata(
        duration=probe.duration,
        container=probe.format.format_name or None,
        video_codec=video.codec_name if video else None,
        width=video.width if video else None,
        height=video.height if video else None,
        fps=video.fps if video else None,
        bit_depth=video.bit_depth if video else None,
        pix_fmt=video.pix_fmt if video else None,
        time_base=video.time_base if video else None,
        color_primaries=video.color_primaries if video else None,
        color_transfer=video.color_transfer if video else None,
        color_space=video.color_space if video else None,
        color_range=video.color_range if video else None,
        sample_aspect_ratio=video.sample_aspect_ratio if video else None,
        display_aspect_ratio=video.display_aspect_ratio if video else None,
        rotation=video.rotation if video else 0,
        audio_codec=first_audio.codec_name if first_audio else None,
        audio_channels=first_audio.channels if first_audio else None,
        audio_sample_rate=first_audio.sample_rate if first_audio else None,
        audio_stream_count=len(audios),
        creation_time=probe.creation_time,
        timecode=probe.timecode,
        camera_make=probe.camera_make,
        camera_model=probe.camera_model,
        gps=probe.gps,
        raw_tags=raw_tags,
    )
