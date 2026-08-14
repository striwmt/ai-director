"""App-window shell helpers."""

from __future__ import annotations

from pathlib import Path

import aidirector.web.window as window_mod


def test_find_app_browser_prefers_which(monkeypatch):
    monkeypatch.setattr(
        window_mod.shutil, "which",
        lambda name: "/usr/bin/google-chrome" if name == "google-chrome" else None,
    )
    assert window_mod.find_app_browser() == "/usr/bin/google-chrome"


def test_find_app_browser_none(monkeypatch):
    monkeypatch.setattr(window_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(window_mod, "_LINUX_CANDIDATES", ("nope",))
    assert window_mod.find_app_browser() is None


def test_launch_app_window_builds_app_command(monkeypatch, tmp_path):
    captured = {}

    class FakeProcess:
        pass

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(window_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("DISPLAY", ":0")
    process = window_mod.launch_app_window(
        "http://127.0.0.1:9999/", tmp_path / "webview", browser="/usr/bin/chromium"
    )
    assert isinstance(process, FakeProcess)
    command = captured["command"]
    assert command[0] == "/usr/bin/chromium"
    assert "--app=http://127.0.0.1:9999/" in command
    assert any(str(tmp_path / "webview") in part for part in command)
    assert (tmp_path / "webview").is_dir()


def test_launch_returns_none_without_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(window_mod, "find_app_browser", lambda: None)
    assert window_mod.launch_app_window("http://x/", tmp_path) is None


def test_launch_returns_none_without_display(monkeypatch, tmp_path):
    monkeypatch.setattr(window_mod.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert window_mod.launch_app_window(
        "http://x/", tmp_path, browser="/usr/bin/chromium"
    ) is None


def test_bootstrap_uv_resolution(monkeypatch, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bootstrap", Path(__file__).resolve().parents[2] / "desktop" / "bootstrap.py"
    )
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)

    monkeypatch.setattr(bootstrap.shutil, "which", lambda n: None)
    monkeypatch.setattr(bootstrap, "UV_DIR", tmp_path)
    monkeypatch.setattr(bootstrap.Path, "home", classmethod(lambda cls: tmp_path))
    assert bootstrap.find_uv() is None

    fake = tmp_path / bootstrap.uv_executable_name()
    fake.write_bytes(b"")
    # a candidate that exists but does not run (broken shim) is skipped
    monkeypatch.setattr(bootstrap, "_uv_works", lambda p: False)
    assert bootstrap.find_uv() is None
    # a working candidate is accepted
    monkeypatch.setattr(bootstrap, "_uv_works", lambda p: True)
    assert bootstrap.find_uv() == str(fake)
