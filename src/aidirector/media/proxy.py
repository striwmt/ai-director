"""Analysis proxy generation.

The proxy is a color-managed, downscaled representation for AI analysis —
the original camera file is never touched (AGENT.md §7, §13, §14).
"""

from __future__ import annotations

from pathlib import Path

from ..color.pipeline import ColorPipelineResult, build_color_filter
from ..color.profile import ColorProfile
from ..color.registry import ColorTransformRegistry
from ..config import AppConfig
from ..logging import get_logger
from ..memory.models import AssetRecord
from ..memory.repository import MediaMemory
from ..process import run_command

log = get_logger("media.proxy")


def proxy_path_for(asset: AssetRecord, config: AppConfig) -> Path:
    return config.paths.proxy_dir / f"{asset.id}_analysis.mp4"


def generate_analysis_proxy(
    asset: AssetRecord,
    profile: ColorProfile,
    registry: ColorTransformRegistry,
    config: AppConfig,
    memory: MediaMemory,
    *,
    force: bool = False,
) -> tuple[Path, ColorPipelineResult]:
    """Create (or reuse) the analysis proxy for a video asset."""
    output = proxy_path_for(asset, config)
    color = build_color_filter(profile, registry, purpose="analysis")

    if output.is_file() and not force:
        stored = memory.get_color_transform_id(asset.id, "analysis")
        if stored == color.transform_id:
            log.debug("reusing analysis proxy for %s", asset.file_name)
            return output, color
        log.info("color transform changed for %s; regenerating proxy", asset.file_name)

    filters = [f"scale=-2:{config.proxy.height}"]
    if color.filter_expr:
        filters.append(color.filter_expr)
    if config.proxy.fps:
        filters.append(f"fps={config.proxy.fps}")

    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", asset.path,
        "-vf", ",".join(filters),
        "-c:v", config.proxy.video_codec,
        "-crf", str(config.proxy.crf),
        "-preset", config.proxy.preset,
        "-pix_fmt", "yuv420p",
    ]
    if asset.metadata.has_audio:
        command += ["-c:a", config.proxy.audio_codec, "-b:a", config.proxy.audio_bitrate]
    else:
        command += ["-an"]
    command.append(str(output))

    output.parent.mkdir(parents=True, exist_ok=True)
    log.info(
        "generating analysis proxy for %s (transform=%s%s)",
        asset.file_name, color.transform_id or "none",
        ", FALLBACK" if color.is_fallback else "",
    )
    run_command(command, timeout=1800.0)

    memory.save_color_transform(
        asset.id, "analysis", color.transform_id, color.lut_hash,
        color.is_fallback, str(output),
    )
    return output, color
