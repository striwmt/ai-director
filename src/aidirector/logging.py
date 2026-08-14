"""Central logging setup.

Rules (AGENT.md §67): log ingest, color decisions, model load/unload, AI
invocations, validation, renders — but never embed image/video payloads.
"""

from __future__ import annotations

import logging

from rich.logging import RichHandler

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger("aidirector").setLevel(level.upper())
        return
    handler = RichHandler(rich_tracebacks=False, show_path=False, markup=False)
    logging.basicConfig(
        level=level.upper(),
        format="%(name)s: %(message)s",
        datefmt="[%X]",
        handlers=[handler],
    )
    # Quiet noisy third-party loggers; our own namespace stays at `level`.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("aidirector").setLevel(level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"aidirector.{name}")
