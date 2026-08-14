"""Native-feeling app window via a Chromium-family browser's app mode.

``chrome --app=URL`` opens a standalone window without browser chrome —
the pragmatic desktop shell for a local web UI on Windows (Edge is always
present), Linux and macOS, with zero extra runtime dependencies. A
dedicated user-data-dir keeps the window app-like and isolated from the
user's browsing profile.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..logging import get_logger

log = get_logger("web.window")

_LINUX_CANDIDATES = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "microsoft-edge-stable", "brave-browser", "vivaldi",
)
_WINDOWS_CANDIDATES = ("msedge", "chrome", "brave")
_WINDOWS_KNOWN_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_MACOS_KNOWN_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


def find_app_browser() -> str | None:
    """Executable of a Chromium-family browser that supports --app mode."""
    if sys.platform == "win32":
        names, known = _WINDOWS_CANDIDATES, _WINDOWS_KNOWN_PATHS
    elif sys.platform == "darwin":
        names, known = _LINUX_CANDIDATES, _MACOS_KNOWN_PATHS
    else:
        names, known = _LINUX_CANDIDATES, ()
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for path in known:
        if Path(path).is_file():
            return path
    return None


def launch_app_window(
    url: str, profile_dir: Path, *, browser: str | None = None
) -> subprocess.Popen | None:
    """Open URL in a standalone app window; returns the window process.

    None means no suitable browser was found (callers fall back to the
    default browser tab).
    """
    executable = browser or find_app_browser()
    if executable is None:
        return None
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        log.warning("no display available; falling back to browser mode")
        return None
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--window-size=1380,920",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    log.info("opening app window via %s", Path(executable).name)
    return subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
