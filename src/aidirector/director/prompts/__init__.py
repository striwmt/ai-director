"""Prompt templates for director stages.

Templates are versioned; the version string is stored with every AI result
for provenance and cache invalidation (AGENT.md §44/§45).
"""

from __future__ import annotations

from pathlib import Path

PROMPT_VERSION = "director-v1"

_PROMPT_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
