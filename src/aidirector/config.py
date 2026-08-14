"""Configuration loading.

Layering (later wins):

    built-in defaults  ->  config/default.yaml + models.yaml  ->  explicit --config file  ->  CLI overrides

Model names live in configuration only; business logic never hardcodes them
(AGENT.md §39/§40).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .errors import ConfigError


class PathsConfig(BaseModel):
    data_dir: Path = Path(".aidirector")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "media_memory.sqlite3"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def proxy_dir(self) -> Path:
        return self.cache_dir / "proxies"

    @property
    def frames_dir(self) -> Path:
        return self.cache_dir / "frames"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def plans_dir(self) -> Path:
        return self.data_dir / "plans"


class IngestConfig(BaseModel):
    video_extensions: list[str] = [".mp4", ".mov", ".mts", ".m2ts"]
    audio_extensions: list[str] = [".wav", ".mp3", ".m4a"]
    image_extensions: list[str] = [
        ".jpg", ".jpeg", ".png", ".heic", ".cr2", ".cr3", ".dng",
    ]
    # DJI .lrf low-res recordings are not primary footage but are linked to it.
    sidecar_extensions: list[str] = [".lrf"]
    partial_hash_bytes: int = 65536


class ProxyConfig(BaseModel):
    height: int = 540
    fps: float | None = None  # None = keep source fps
    video_codec: str = "libx264"
    crf: int = 23
    preset: str = "veryfast"
    audio_codec: str = "aac"
    audio_bitrate: str = "128k"


class SegmentationConfig(BaseModel):
    scene_threshold: float = 0.4
    min_segment_seconds: float = 1.5
    max_segment_seconds: float = 15.0
    silence_noise_db: float = -35.0
    silence_min_seconds: float = 0.6
    frames_per_segment: int = 3
    frame_height: int = 480


class ColorConfig(BaseModel):
    profiles_file: Path = Path("config/color_profiles.yaml")
    luts_dir: Path = Path("assets/luts")
    min_confidence: float = 0.5  # below this, detection yields UNKNOWN


class ModelEndpointConfig(BaseModel):
    provider: str
    model: str = ""
    device: str = "auto"
    compute_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    context_length: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ModelsConfig(BaseModel):
    """Reference configuration for RTX 5060 Ti 16GB (AGENT.md §39/§40).

    These are defaults, not hardcoded requirements — override in
    config/models.yaml.
    """

    vision: ModelEndpointConfig = ModelEndpointConfig(
        provider="transformers", model="Qwen/Qwen3-VL-4B-Instruct",
        device="auto",
    )
    director: ModelEndpointConfig = ModelEndpointConfig(
        provider="openai-compatible", model="qwen3-8b-q4",
        base_url="http://127.0.0.1:8102/v1", context_length=16384,
    )
    speech: ModelEndpointConfig = ModelEndpointConfig(
        provider="faster-whisper", model="large-v3-turbo",
        device="auto", compute_type="float16",
    )
    embedding: ModelEndpointConfig = ModelEndpointConfig(
        provider="sentence-transformers", model="Qwen/Qwen3-VL-Embedding-2B",
        device="auto",
    )


class OutputConfig(BaseModel):
    # Timeline canvas: auto (duration-weighted majority orientation of the
    # chosen clips), landscape, portrait, or explicit "1920x1080".
    canvas: str = "auto"
    # Scene-change captions (time/place, centered): none | beats | clips.
    captions: str = "none"
    # Caption layout. Tokens: {PLACE} {DATE} {TIME} {YYYY} {MO} {DD} {HH} {MM};
    # "\n" starts the smaller second line. Tokens without facts vanish cleanly.
    caption_format: str = "{PLACE}\n{DATE} {TIME}"
    # Burn spoken-word subtitles (from the transcript) into clips.
    subtitles: bool = False
    # Default BGM folder (.mp3/.wav/.m4a candidates); a per-run --music-dir
    # or web-form path overrides it. None disables music selection.
    music_dir: Path | None = None
    # Default music bed level baked into new plans.
    music_gain_db: float = -18.0


class DirectorConfig(BaseModel):
    profiles_dir: Path = Path("config/director_profiles")
    default_profile: str = "travel_vlog"
    max_revision_loops: int = 2
    candidates_per_beat: int = 8


class AppConfig(BaseModel):
    paths: PathsConfig = PathsConfig()
    ingest: IngestConfig = IngestConfig()
    proxy: ProxyConfig = ProxyConfig()
    segmentation: SegmentationConfig = SegmentationConfig()
    color: ColorConfig = ColorConfig()
    models: ModelsConfig = ModelsConfig()
    director: DirectorConfig = DirectorConfig()
    output: OutputConfig = OutputConfig()
    log_level: str = "INFO"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"top level of {path} must be a mapping")
    return data


def load_config(
    config_file: Path | None = None,
    *,
    project_root: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> AppConfig:
    """Load configuration with layering.

    ``project_root`` is where ``config/default.yaml`` and ``config/models.yaml``
    are searched (defaults to CWD). When no explicit file is given, the
    ``AIDIRECTOR_CONFIG`` environment variable supplies one (used by Docker).
    """
    if config_file is None:
        env_config = os.environ.get("AIDIRECTOR_CONFIG")
        if env_config:
            config_file = Path(env_config)
    root = project_root or Path.cwd()
    data: dict[str, Any] = {}

    for candidate in (root / "config" / "default.yaml", root / "config" / "models.yaml"):
        if candidate.is_file():
            data = _deep_merge(data, _load_yaml(candidate))

    if config_file is not None:
        if not config_file.is_file():
            raise ConfigError(f"config file not found: {config_file}")
        data = _deep_merge(data, _load_yaml(config_file))

    if overrides:
        data = _deep_merge(data, overrides)

    try:
        return AppConfig.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise ConfigError(f"invalid configuration: {exc}") from exc


def ensure_dirs(config: AppConfig) -> None:
    for path in (
        config.paths.data_dir,
        config.paths.cache_dir,
        config.paths.proxy_dir,
        config.paths.frames_dir,
        config.paths.renders_dir,
        config.paths.plans_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
