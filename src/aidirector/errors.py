"""Project-wide exception types.

One failing asset must never break a whole project run (AGENT.md §66), so
callers are expected to catch the asset-scoped errors and continue.
"""

from __future__ import annotations


class AIDirectorError(Exception):
    """Base class for all project errors."""


class ConfigError(AIDirectorError):
    """Invalid or missing configuration."""


class ExternalToolError(AIDirectorError):
    """An external command (ffmpeg, ffprobe, ...) failed."""

    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"command failed ({returncode}): {' '.join(command[:4])}...\n{stderr[-2000:]}"
        )


class MediaError(AIDirectorError):
    """A single media asset could not be processed (corrupt file, probe failure...)."""


class ColorError(AIDirectorError):
    """Color profile / transform problem (missing LUT, unresolvable transform...)."""


class ProviderError(AIDirectorError):
    """An AI provider failed (model unavailable, malformed output after retries...)."""


class StructuredOutputError(ProviderError):
    """Model output failed schema validation even after repair attempts."""


class ValidationError(AIDirectorError):
    """An edit plan or timeline failed deterministic validation."""


class MemoryError_(AIDirectorError):
    """Media Memory (database) failure."""
