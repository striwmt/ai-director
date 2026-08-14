"""Color profile model (AGENT.md §12)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ColorProfile(str, Enum):
    REC709 = "rec709"

    DJI_DLOG2 = "dji_dlog2"
    DJI_DLOG = "dji_dlog"
    DJI_DLOG_M = "dji_dlog_m"

    HLG = "hlg"

    CANON_CLOG = "canon_clog"
    CANON_CLOG2 = "canon_clog2"
    CANON_CLOG3 = "canon_clog3"

    SONY_SLOG2 = "sony_slog2"
    SONY_SLOG3 = "sony_slog3"

    PANASONIC_VLOG = "panasonic_vlog"

    UNKNOWN = "unknown"

    @property
    def is_log(self) -> bool:
        return self in {
            ColorProfile.DJI_DLOG2,
            ColorProfile.DJI_DLOG,
            ColorProfile.DJI_DLOG_M,
            ColorProfile.CANON_CLOG,
            ColorProfile.CANON_CLOG2,
            ColorProfile.CANON_CLOG3,
            ColorProfile.SONY_SLOG2,
            ColorProfile.SONY_SLOG3,
            ColorProfile.PANASONIC_VLOG,
        }

    @property
    def is_hdr(self) -> bool:
        return self is ColorProfile.HLG


def parse_color_profile(value: str) -> ColorProfile:
    """Parse user-facing profile names, accepting `dji-dlog2` style dashes."""
    normalized = value.strip().lower().replace("-", "_")
    if normalized == "auto":
        return ColorProfile.UNKNOWN
    try:
        return ColorProfile(normalized)
    except ValueError as exc:
        valid = ", ".join(p.value for p in ColorProfile)
        raise ValueError(f"unknown color profile '{value}' (valid: {valid})") from exc


class ColorProfileDetection(BaseModel):
    """Automatic detection result with confidence (AGENT.md §17)."""

    profile: ColorProfile
    confidence: float
    source: str = "auto"  # auto | user | sidecar
