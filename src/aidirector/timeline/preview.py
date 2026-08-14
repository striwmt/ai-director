"""FFmpeg preview rendering (AGENT.md §57/§70).

Preview uses proxies — the original camera files stay untouched (§58).
Each clip is rendered to a normalized intermediate, then concatenated.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..config import AppConfig
from ..logging import get_logger
from ..process import run_command
from .captions import build_caption_overlay, build_subtitle_overlays
from .model import Timeline

log = get_logger("timeline.preview")

_PREVIEW_LONG_EDGE = 1280
_PREVIEW_FPS = 30
_AUDIO_RATE = 48000


def _preview_canvas(timeline: Timeline) -> tuple[int, int]:
    """Scale the timeline canvas down to preview size, keeping its aspect."""
    width, height = timeline.width, timeline.height
    long_edge = max(width, height)
    if long_edge > _PREVIEW_LONG_EDGE:
        scale = _PREVIEW_LONG_EDGE / long_edge
        width, height = round(width * scale), round(height * scale)
    return width // 2 * 2, height // 2 * 2


def _fit_filter(width: int, height: int) -> str:
    """Scale to fit the canvas and pad the rest (letterbox/pillarbox).

    Mixed portrait/landscape footage must never be stretched, and every
    concat part must share one exact frame size.
    """
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={_PREVIEW_FPS},format=yuv420p,setsar=1"
    )


def render_preview(
    timeline: Timeline,
    config: AppConfig,
    output: Path | None = None,
) -> Path:
    if not timeline.clips:
        raise ValueError("cannot render an empty timeline")

    canvas_w, canvas_h = _preview_canvas(timeline)
    log.info("preview canvas: %dx%d (timeline %dx%d)",
             canvas_w, canvas_h, timeline.width, timeline.height)

    output = output or (config.paths.renders_dir / "preview.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="aidirector_preview_") as tmp:
        tmp_dir = Path(tmp)
        parts: list[Path] = []
        for clip in timeline.clips:
            source = clip.proxy_path or clip.original_path
            part = tmp_dir / f"part_{clip.index:04d}.mp4"

            # Text overlays: scene caption (faded) + spoken-word subtitles,
            # each a transparent PNG chained through `overlay`.
            overlays = []
            if clip.caption is not None:
                caption_overlay = build_caption_overlay(
                    clip.caption, canvas_w, canvas_h, clip.duration,
                    tmp_dir, clip.index,
                )
                if caption_overlay is not None:
                    overlays.append(caption_overlay)
            if clip.subtitles:
                overlays.extend(
                    build_subtitle_overlays(
                        clip.subtitles, clip.source_in, clip.duration,
                        canvas_w, canvas_h, tmp_dir, clip.index,
                    )
                )

            caption_inputs: list[str] = []
            if overlays:
                graph_parts = [f"[0:v]{_fit_filter(canvas_w, canvas_h)}[v0]"]
                previous = "v0"
                for k, item in enumerate(overlays):
                    input_index = 2 + k
                    caption_inputs += [
                        "-loop", "1", "-t", f"{clip.duration:.3f}",
                        "-i", str(item.png_path),
                    ]
                    graph_parts.append(f"[{input_index}:v]{item.filter_snippet}[ov{k}]")
                    out_label = "v" if k == len(overlays) - 1 else f"v{k + 1}"
                    graph_parts.append(
                        f"[{previous}][ov{k}]overlay=0:0:"
                        f"enable='{item.enable_expr}'[{out_label}]"
                    )
                    previous = f"v{k + 1}"
                video_graph = ";".join(graph_parts)
            else:
                video_graph = f"[0:v]{_fit_filter(canvas_w, canvas_h)}[v]"

            if clip.audio.mode == "muted":
                volume = 0.0
            elif clip.audio.mode == "ducked":
                volume = 10 ** ((clip.audio.gain_db - 12.0) / 20.0)
            else:
                volume = 10 ** (clip.audio.gain_db / 20.0)

            command = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{clip.source_in:.3f}",
                "-t", f"{clip.duration:.3f}",
                "-i", str(source),
                # Normalize so every part concatenates cleanly. Missing audio
                # is padded with silence.
                "-f", "lavfi", "-t", f"{clip.duration:.3f}",
                "-i", f"anullsrc=channel_layout=stereo:sample_rate={_AUDIO_RATE}",
                *caption_inputs,
                "-filter_complex",
                (
                    f"{video_graph};"
                    f"[0:a]volume={volume:.4f},aresample={_AUDIO_RATE}[a0];"
                    f"[a0][1:a]amix=inputs=2:duration=first:dropout_transition=0[a]"
                ),
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "160k",
                str(part),
            ]
            try:
                run_command(command, timeout=600.0)
            except Exception:
                # Source may have no audio stream — retry mapping silence only.
                command_noaudio = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{clip.source_in:.3f}",
                    "-t", f"{clip.duration:.3f}",
                    "-i", str(source),
                    "-f", "lavfi", "-t", f"{clip.duration:.3f}",
                    "-i", f"anullsrc=channel_layout=stereo:sample_rate={_AUDIO_RATE}",
                    *caption_inputs,
                    "-filter_complex", video_graph,
                    "-map", "[v]", "-map", "1:a",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "160k",
                    "-shortest",
                    str(part),
                ]
                run_command(command_noaudio, timeout=600.0)
            parts.append(part)

        concat_list = tmp_dir / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{p}'\n" for p in parts), encoding="utf-8"
        )
        run_command(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                str(output),
            ],
            timeout=600.0,
        )

    log.info("preview rendered: %s (%.1fs, %d clips)", output, timeline.duration, len(timeline.clips))
    return output
