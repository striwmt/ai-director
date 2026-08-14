"""Common subprocess wrapper.

All external commands (ffmpeg, ffprobe, exiftool, ...) go through here
(AGENT.md §75) so that timeouts, logging and error handling are uniform.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from .errors import ExternalToolError
from .logging import get_logger

log = get_logger("process")


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def run_command(
    command: list[str],
    *,
    timeout: float | None = 600.0,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> CommandResult:
    """Run an external command, capture output, raise ExternalToolError on failure."""
    log.debug("run: %s", " ".join(command))
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            input=input_bytes,
        )
    except FileNotFoundError as exc:
        raise ExternalToolError(command, -1, f"executable not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExternalToolError(command, -1, f"timeout after {timeout}s") from exc

    result = CommandResult(
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout.decode("utf-8", errors="replace"),
        stderr=proc.stderr.decode("utf-8", errors="replace"),
    )
    if check and proc.returncode != 0:
        raise ExternalToolError(command, proc.returncode, result.stderr)
    return result
