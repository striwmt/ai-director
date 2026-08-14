"""Normalized media metadata extracted from probe results.

Missing values are normal (AGENT.md §9).
"""

from __future__ import annotations

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
        camera_make=probe.camera_make,
        camera_model=probe.camera_model,
        gps=probe.gps,
        raw_tags=raw_tags,
    )
