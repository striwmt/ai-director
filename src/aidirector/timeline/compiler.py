"""Edit Plan -> Timeline compilation (AGENT.md §57).

Purely mechanical: no editorial decisions are made or changed here.
"""

from __future__ import annotations

from ..director.schemas import EditPlan
from ..errors import ValidationError
from ..memory.repository import MediaMemory
from .model import Timeline, TimelineClip


def choose_canvas(
    sized_durations: list[tuple[tuple[int, int] | None, float]],
    *,
    canvas: str = "auto",
    default: tuple[int, int] = (1920, 1080),
) -> tuple[int, int]:
    """Pick the timeline canvas from clip display sizes.

    ``canvas``: "auto" (duration-weighted majority orientation),
    "landscape", "portrait", or an explicit "1920x1080". Resolution is the
    largest display size among clips of the chosen orientation — never
    upscaled beyond the best source.
    """
    canvas = (canvas or "auto").lower()
    if "x" in canvas:
        try:
            width, height = (int(p) for p in canvas.split("x"))
            return width // 2 * 2, height // 2 * 2
        except ValueError as exc:
            raise ValidationError(f"invalid canvas '{canvas}' (expected WxH)") from exc

    if canvas == "portrait":
        portrait_wins = True
    elif canvas == "landscape":
        portrait_wins = False
    else:
        portrait_seconds = 0.0
        landscape_seconds = 0.0
        for size, duration in sized_durations:
            if size is None:
                continue
            if size[1] > size[0]:
                portrait_seconds += duration
            else:
                landscape_seconds += duration
        portrait_wins = portrait_seconds > landscape_seconds

    best: tuple[int, int] | None = None
    for size, _duration in sized_durations:
        if size is None or (size[1] > size[0]) != portrait_wins:
            continue
        if best is None or size[0] * size[1] > best[0] * best[1]:
            best = size
    if best is None:
        return (default[1], default[0]) if portrait_wins else default
    # Even dimensions for h264.
    return (best[0] // 2 * 2, best[1] // 2 * 2)


def compile_timeline(
    plan: EditPlan, memory: MediaMemory, *, canvas: str = "auto"
) -> Timeline:
    clips: list[TimelineClip] = []
    cursor = 0.0
    fps: float | None = None
    sized_durations: list[tuple[tuple[int, int] | None, float]] = []

    for i, clip in enumerate(plan.clips):
        segment = memory.get_segment(clip.segment_id)
        if segment is None:
            raise ValidationError(f"segment not found: {clip.segment_id}")
        asset = memory.get_asset(segment.asset_id)
        if asset is None:
            raise ValidationError(f"asset not found: {segment.asset_id}")
        if fps is None and asset.metadata.fps:
            fps = asset.metadata.fps
        sized_durations.append((asset.metadata.display_size, clip.duration))

        clips.append(
            TimelineClip(
                index=i,
                segment_id=clip.segment_id,
                original_path=asset.path,
                proxy_path=memory.get_analysis_proxy(asset.id),
                source_in=clip.source_in,
                source_out=clip.source_out,
                timeline_start=round(cursor, 3),
                audio=clip.audio,
                transition=clip.transition,
                caption=clip.caption,
                subtitles=clip.subtitles,
                story_beat=clip.story_beat,
                reason=clip.reason,
            )
        )
        cursor += clip.duration

    width, height = choose_canvas(sized_durations, canvas=canvas)
    return Timeline(
        name=f"AI Director — {plan.story.concept or plan.intent.user_prompt or 'untitled'}"[:80],
        fps=fps or 30.0,
        width=width,
        height=height,
        clips=clips,
    )
