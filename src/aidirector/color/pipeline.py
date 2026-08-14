"""Build the color part of ffmpeg filter chains.

Two purposes are distinguished (AGENT.md §14):

    analysis — neutral, consistent representation for VLM / embedding / CV
    preview  — human-friendly look for UI

Normalization only — creative grading belongs to the NLE (AGENT.md §59).
The original media is never modified (AGENT.md §7).
"""

from __future__ import annotations

from pydantic import BaseModel

from ..logging import get_logger
from .lut import lut_hash
from .profile import ColorProfile
from .registry import ColorTransformRegistry

log = get_logger("color.pipeline")

# Gentle neutral fallback when a vendor LUT is not installed: raise contrast
# and saturation of log footage so the VLM at least sees a plausible image.
_LOG_FALLBACK_FILTER = "eq=contrast=1.35:saturation=1.5,format=yuv420p"


class ColorPipelineResult(BaseModel):
    """The resolved color step for one asset."""

    filter_expr: str | None  # None = no color filter needed
    transform_id: str | None  # registry id, or synthetic fallback id
    lut_hash: str | None = None
    is_fallback: bool = False


def _escape_lut_path(path: str) -> str:
    # ffmpeg filter args need ':' and '\' escaped.
    return path.replace("\\", "\\\\").replace(":", "\\:")


def build_color_filter(
    profile: ColorProfile,
    registry: ColorTransformRegistry,
    purpose: str = "analysis",
) -> ColorPipelineResult:
    transform = registry.resolve(profile, ColorProfile.REC709, purpose)

    if transform is not None:
        if transform.type == "passthrough":
            return ColorPipelineResult(filter_expr=None, transform_id=transform.id)
        if transform.type == "lut3d" and transform.path is not None:
            expr = f"lut3d=file='{_escape_lut_path(str(transform.path))}',format=yuv420p"
            return ColorPipelineResult(
                filter_expr=expr,
                transform_id=transform.id,
                lut_hash=lut_hash(transform.path),
            )
        if transform.type == "ffmpeg_filter" and transform.filter_expr:
            return ColorPipelineResult(
                filter_expr=transform.filter_expr, transform_id=transform.id
            )

    if profile.is_log:
        log.warning(
            "no transform available for %s -> rec709 (%s); using neutral fallback "
            "(install the vendor LUT under assets/luts for accurate analysis)",
            profile.value, purpose,
        )
        return ColorPipelineResult(
            filter_expr=_LOG_FALLBACK_FILTER,
            transform_id=f"{profile.value}_fallback_v1",
            is_fallback=True,
        )

    if profile.is_hdr:
        log.warning("no HDR transform for %s; using neutral fallback", profile.value)
        return ColorPipelineResult(
            filter_expr=_LOG_FALLBACK_FILTER,
            transform_id=f"{profile.value}_fallback_v1",
            is_fallback=True,
        )

    # Rec.709 / unknown SDR: pass through untouched.
    return ColorPipelineResult(filter_expr=None, transform_id=None)
