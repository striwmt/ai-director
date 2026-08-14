"""LUT file helpers.

Vendor LUTs are user-supplied under assets/luts (AGENT.md §16); we record
their hash so cached analysis results can be invalidated when a LUT changes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def lut_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lut_available(path: Path | None) -> bool:
    return path is not None and path.is_file()
