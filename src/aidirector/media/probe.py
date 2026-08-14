"""ffprobe wrapper.

Missing metadata is a normal state (AGENT.md §9) — every field is optional
and parsing never fails on absent tags.
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..errors import MediaError
from ..process import run_command


class StreamInfo(BaseModel):
    index: int
    codec_type: str = ""
    codec_name: str | None = None
    profile: str | None = None
    width: int | None = None
    height: int | None = None
    pix_fmt: str | None = None
    bits_per_raw_sample: int | None = None
    avg_frame_rate: str | None = None
    r_frame_rate: str | None = None
    time_base: str | None = None
    duration: float | None = None
    nb_frames: int | None = None
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    color_range: str | None = None
    sample_aspect_ratio: str | None = None
    display_aspect_ratio: str | None = None
    rotation: int = 0  # degrees from the display matrix (phone footage)
    channels: int | None = None
    sample_rate: int | None = None
    tags: dict[str, str] = Field(default_factory=dict)

    @property
    def fps(self) -> float | None:
        for rate in (self.avg_frame_rate, self.r_frame_rate):
            if rate and rate not in ("0/0", "0"):
                try:
                    return float(Fraction(rate))
                except (ValueError, ZeroDivisionError):
                    continue
        return None

    @property
    def bit_depth(self) -> int | None:
        if self.bits_per_raw_sample:
            return self.bits_per_raw_sample
        if self.pix_fmt:
            if "16le" in self.pix_fmt or "16be" in self.pix_fmt:
                return 16
            if "12le" in self.pix_fmt or "12be" in self.pix_fmt:
                return 12
            if "10le" in self.pix_fmt or "10be" in self.pix_fmt:
                return 10
            return 8
        return None


class FormatInfo(BaseModel):
    filename: str = ""
    format_name: str = ""
    duration: float | None = None
    size: int | None = None
    bit_rate: int | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class ProbeResult(BaseModel):
    streams: list[StreamInfo] = Field(default_factory=list)
    format: FormatInfo = FormatInfo()

    @property
    def video_stream(self) -> StreamInfo | None:
        for s in self.streams:
            # Skip attached pictures (cover art) that probe as video.
            if s.codec_type == "video" and s.codec_name not in ("mjpeg", "png"):
                return s
        for s in self.streams:
            if s.codec_type == "video":
                return s
        return None

    @property
    def audio_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "audio"]

    @property
    def duration(self) -> float | None:
        if self.format.duration:
            return self.format.duration
        for s in self.streams:
            if s.duration:
                return s.duration
        return None

    def _tag(self, *names: str) -> str | None:
        """Case-insensitive tag lookup across format and stream tags."""
        pools = [self.format.tags] + [s.tags for s in self.streams]
        lowered_names = [n.lower() for n in names]
        for pool in pools:
            lowered = {k.lower(): v for k, v in pool.items()}
            for name in lowered_names:
                if name in lowered and lowered[name]:
                    return lowered[name]
        return None

    @property
    def creation_time(self) -> str | None:
        return self._tag("creation_time", "com.apple.quicktime.creationdate")

    @property
    def camera_make(self) -> str | None:
        return self._tag("make", "manufacturer", "com.apple.quicktime.make")

    @property
    def camera_model(self) -> str | None:
        return self._tag("model", "com.apple.quicktime.model")

    @property
    def gps(self) -> str | None:
        return self._tag("location", "com.apple.quicktime.location.ISO6709")


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rotation_from_side_data(raw: dict[str, Any]) -> int:
    for side in raw.get("side_data_list") or []:
        if "rotation" in side:
            rotation = _to_int(side["rotation"]) or 0
            return rotation % 360
    return 0


def parse_ffprobe_json(data: dict[str, Any]) -> ProbeResult:
    streams: list[StreamInfo] = []
    for raw in data.get("streams", []):
        streams.append(
            StreamInfo(
                index=raw.get("index", 0),
                codec_type=raw.get("codec_type", ""),
                codec_name=raw.get("codec_name"),
                profile=raw.get("profile"),
                width=_to_int(raw.get("width")),
                height=_to_int(raw.get("height")),
                pix_fmt=raw.get("pix_fmt"),
                bits_per_raw_sample=_to_int(raw.get("bits_per_raw_sample")),
                avg_frame_rate=raw.get("avg_frame_rate"),
                r_frame_rate=raw.get("r_frame_rate"),
                time_base=raw.get("time_base"),
                duration=_to_float(raw.get("duration")),
                nb_frames=_to_int(raw.get("nb_frames")),
                color_primaries=raw.get("color_primaries"),
                color_transfer=raw.get("color_transfer"),
                color_space=raw.get("color_space"),
                color_range=raw.get("color_range"),
                sample_aspect_ratio=raw.get("sample_aspect_ratio"),
                display_aspect_ratio=raw.get("display_aspect_ratio"),
                rotation=_rotation_from_side_data(raw),
                channels=_to_int(raw.get("channels")),
                sample_rate=_to_int(raw.get("sample_rate")),
                tags={k: str(v) for k, v in (raw.get("tags") or {}).items()},
            )
        )
    fmt_raw = data.get("format", {})
    fmt = FormatInfo(
        filename=fmt_raw.get("filename", ""),
        format_name=fmt_raw.get("format_name", ""),
        duration=_to_float(fmt_raw.get("duration")),
        size=_to_int(fmt_raw.get("size")),
        bit_rate=_to_int(fmt_raw.get("bit_rate")),
        tags={k: str(v) for k, v in (fmt_raw.get("tags") or {}).items()},
    )
    return ProbeResult(streams=streams, format=fmt)


def probe_file(path: Path) -> ProbeResult:
    result = run_command(
        [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=60.0,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe returned invalid JSON for {path}") from exc
    return parse_ffprobe_json(data)
