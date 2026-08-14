from pathlib import Path

import pytest

from aidirector.config import load_config
from aidirector.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_defaults_without_files(tmp_path):
    config = load_config(project_root=tmp_path)
    assert config.proxy.height == 540
    assert config.models.speech.provider == "faster-whisper"


def test_repo_config_loads():
    config = load_config(project_root=PROJECT_ROOT)
    assert config.director.default_profile == "travel_vlog"
    assert config.models.director.provider == "openai-compatible"
    assert config.models.director.context_length == 16384


def test_explicit_file_overrides(tmp_path):
    override = tmp_path / "override.yaml"
    override.write_text("proxy:\n  height: 720\nlog_level: DEBUG\n")
    config = load_config(override, project_root=PROJECT_ROOT)
    assert config.proxy.height == 720
    assert config.log_level == "DEBUG"
    # untouched values survive the merge
    assert config.segmentation.scene_threshold == 0.4


def test_cli_overrides_win(tmp_path):
    config = load_config(
        project_root=tmp_path, overrides={"proxy": {"height": 1080}}
    )
    assert config.proxy.height == 1080


def test_env_config_file(tmp_path, monkeypatch):
    override = tmp_path / "env.yaml"
    override.write_text("proxy:\n  height: 640\n")
    monkeypatch.setenv("AIDIRECTOR_CONFIG", str(override))
    config = load_config(project_root=tmp_path)
    assert config.proxy.height == 640
    # explicit file still wins over the environment variable
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("proxy:\n  height: 900\n")
    assert load_config(explicit, project_root=tmp_path).proxy.height == 900


def test_missing_config_file_errors(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml", project_root=tmp_path)


def test_invalid_yaml_errors(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("proxy: [unclosed\n")
    with pytest.raises(ConfigError):
        load_config(bad, project_root=tmp_path)
