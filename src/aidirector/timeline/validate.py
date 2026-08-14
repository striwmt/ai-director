"""Deterministic Edit Plan validation (AGENT.md §56).

AI output is never trusted blindly; validation failures raise
ValidationError with every problem listed.
"""

from __future__ import annotations

import math

from ..director.schemas import EditPlan
from ..errors import ValidationError
from ..memory.repository import MediaMemory

_BOUNDS_TOLERANCE = 0.05  # seconds of slack against probed durations


def validate_edit_plan(plan: EditPlan, memory: MediaMemory) -> list[str]:
    """Return a list of problems; raise ValidationError if any are fatal."""
    problems: list[str] = []

    if not plan.clips:
        problems.append("edit plan has no clips")

    for i, clip in enumerate(plan.clips):
        label = f"clip {i} ({clip.segment_id})"

        if not (math.isfinite(clip.source_in) and math.isfinite(clip.source_out)):
            problems.append(f"{label}: non-finite source times")
            continue
        if clip.source_in < 0:
            problems.append(f"{label}: source_in < 0")
        if clip.source_in >= clip.source_out:
            problems.append(f"{label}: source_in >= source_out")

        segment = memory.get_segment(clip.segment_id)
        if segment is None:
            problems.append(f"{label}: segment does not exist")
            continue
        asset = memory.get_asset(segment.asset_id)
        if asset is None:
            problems.append(f"{label}: asset {segment.asset_id} does not exist")
            continue
        if asset.duration is not None:
            if clip.source_out > asset.duration + _BOUNDS_TOLERANCE:
                problems.append(
                    f"{label}: source_out {clip.source_out:.2f} exceeds asset "
                    f"duration {asset.duration:.2f}"
                )
        if clip.transition.duration < 0 or not math.isfinite(clip.transition.duration):
            problems.append(f"{label}: invalid transition duration")
        if clip.caption is not None:
            if not clip.caption.text.strip() and not clip.caption.secondary.strip():
                problems.append(f"{label}: caption present but empty")
            if len(clip.caption.text) > 80 or len(clip.caption.secondary) > 80:
                problems.append(f"{label}: caption text too long (max 80 chars)")
        for line_no, line in enumerate(clip.subtitles):
            if not (math.isfinite(line.start) and math.isfinite(line.end)):
                problems.append(f"{label}: subtitle {line_no} has non-finite times")
            elif line.start >= line.end:
                problems.append(f"{label}: subtitle {line_no} start >= end")
            if len(line.text) > 200:
                problems.append(f"{label}: subtitle {line_no} text too long")
        if not math.isfinite(clip.audio.gain_db) or abs(clip.audio.gain_db) > 30:
            problems.append(f"{label}: implausible audio gain {clip.audio.gain_db}")

    total = plan.total_duration
    if plan.clips and not math.isfinite(total):
        problems.append("timeline duration is not finite")
    if plan.clips and total <= 0:
        problems.append("timeline duration is zero")
    if plan.clips and total > plan.intent.target_duration * 3:
        problems.append(
            f"timeline duration {total:.1f}s wildly exceeds target "
            f"{plan.intent.target_duration:.1f}s"
        )

    if problems:
        raise ValidationError(
            "edit plan validation failed:\n" + "\n".join(f"- {p}" for p in problems)
        )
    return problems
