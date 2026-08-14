#!/usr/bin/env python3
"""AI Director desktop launcher — standard library only.

Run with any Python 3.9+ (the app itself gets its own managed Python via uv):

    python desktop/bootstrap.py            # set up (first run) and launch
    python desktop/bootstrap.py --no-launch  # set up only
    python desktop/bootstrap.py -- --port 8484   # pass args to `aidirector app`

What it does:
  1. find `uv` (PATH → previously downloaded copy → download the official
     standalone binary for this platform)
  2. `uv sync` the project's locked environment with the local-model extras
  3. exec `aidirector app` (standalone app window / browser)

This is also the sidecar entry point for the Tauri shell (desktop/tauri).
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UV_DIR = REPO_ROOT / "desktop" / "bin"
DEFAULT_EXTRAS = ("speech", "vision", "embedding", "web")

UV_RELEASES = "https://github.com/astral-sh/uv/releases/latest/download"
UV_ARCHIVES = {
    ("Linux", "x86_64"): "uv-x86_64-unknown-linux-gnu.tar.gz",
    ("Linux", "aarch64"): "uv-aarch64-unknown-linux-gnu.tar.gz",
    ("Windows", "AMD64"): "uv-x86_64-pc-windows-msvc.zip",
    ("Darwin", "arm64"): "uv-aarch64-apple-darwin.tar.gz",
    ("Darwin", "x86_64"): "uv-x86_64-apple-darwin.tar.gz",
}


def log(message: str) -> None:
    print(f"[aidirector-setup] {message}", flush=True)


def uv_executable_name() -> str:
    return "uv.exe" if platform.system() == "Windows" else "uv"


def _uv_works(path: str) -> bool:
    """A found `uv` must actually run — version-manager shims can be broken."""
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, timeout=15
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_uv() -> str | None:
    candidates = []
    found = shutil.which("uv")
    if found:
        candidates.append(found)
    candidates.append(str(Path.home() / ".local" / "bin" / uv_executable_name()))
    candidates.append(str(UV_DIR / uv_executable_name()))
    for candidate in candidates:
        if Path(candidate).is_file() and _uv_works(candidate):
            return candidate
    return None


def download_uv() -> str:
    key = (platform.system(), platform.machine())
    archive_name = UV_ARCHIVES.get(key)
    if archive_name is None:
        raise SystemExit(
            f"unsupported platform {key}; install uv manually: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )
    url = f"{UV_RELEASES}/{archive_name}"
    log(f"downloading uv: {url}")
    UV_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / archive_name
        urllib.request.urlretrieve(url, archive_path)  # noqa: S310
        if archive_name.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(tmp)
        else:
            with tarfile.open(archive_path) as tf:
                tf.extractall(tmp)
        binary = next(Path(tmp).rglob(uv_executable_name()))
        target = UV_DIR / uv_executable_name()
        shutil.copy2(binary, target)
        target.chmod(0o755)
    log(f"uv installed to {target}")
    return str(target)


def sync_environment(uv: str, extras: tuple[str, ...]) -> None:
    command = [uv, "sync", "--frozen", "--no-dev"]
    for extra in extras:
        command += ["--extra", extra]
    log("setting up the Python environment (first run downloads several GB)")
    subprocess.run(command, cwd=REPO_ROOT, check=True)
    log("environment ready")


def launch(uv: str, app_args: list[str]) -> int:
    command = [uv, "run", "--no-sync", "aidirector", "app", *app_args]
    log("launching AI Director")
    return subprocess.run(command, cwd=REPO_ROOT).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-launch", action="store_true",
                        help="set up the environment without starting the app")
    parser.add_argument("--extras", default=",".join(DEFAULT_EXTRAS),
                        help="comma-separated extras (default: %(default)s)")
    parser.add_argument("app_args", nargs="*",
                        help="arguments passed through to `aidirector app`")
    args = parser.parse_args()

    os.chdir(REPO_ROOT)
    uv = find_uv() or download_uv()
    log(f"using uv: {uv}")
    extras = tuple(e for e in args.extras.split(",") if e)
    sync_environment(uv, extras)
    if args.no_launch:
        return 0
    return launch(uv, args.app_args)


if __name__ == "__main__":
    raise SystemExit(main())
