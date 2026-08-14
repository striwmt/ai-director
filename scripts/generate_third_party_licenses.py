#!/usr/bin/env python3
"""Generate THIRD_PARTY_LICENSES.md for distribution.

Collects license metadata and texts from every installed distribution in
the current environment, then appends static sections for components that
are not pip packages (models, external binaries) and for the things that
must NOT be bundled (vendor LUTs, Windows system fonts).

Usage:
    uv run python scripts/generate_third_party_licenses.py [output.md]
"""

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

STATIC_SECTIONS = """\
## AI models (downloaded at first use, redistributable)

| Model | License |
|---|---|
| Qwen/Qwen3-VL-4B-Instruct | Apache-2.0 |
| Qwen/Qwen3-8B (incl. GGUF) | Apache-2.0 |
| Qwen/Qwen3-VL-Embedding-2B | Apache-2.0 |
| Whisper large-v3-turbo (faster-whisper CT2 conversion) | MIT |
| Silero VAD (bundled with faster-whisper) | MIT |

## External programs (invoked as separate processes)

- **FFmpeg** — LGPL-2.1+; common static builds enable GPL components
  (libx264/libx265) and are therefore **GPL-2.0+ as a whole**. AI Director
  invokes ffmpeg as a separate process (mere aggregation); if you bundle an
  FFmpeg binary, include its license text and the source offer / source URL
  of the exact build (e.g. https://ffmpeg.org/download.html,
  https://github.com/BtbN/FFmpeg-Builds).
- **llama.cpp (llama-server)** — MIT. CUDA-enabled builds additionally
  contain NVIDIA CUDA runtime components under the NVIDIA EULA
  redistribution terms.
- **uv** — MIT OR Apache-2.0.
- **CPython** — PSF License.

## NVIDIA components

`torch`, `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` and related wheels
contain NVIDIA proprietary runtime libraries, redistributed under the
NVIDIA CUDA Toolkit EULA / cuDNN SLA redistribution terms
(https://docs.nvidia.com/cuda/eula/). This installer does not bundle them;
they are fetched from PyPI at setup time.

## Fonts

Caption rendering uses fonts found on the host system. On Windows this
includes Meiryo / Yu Gothic / MS Gothic — **Microsoft system fonts are
used in place and are never bundled**. When bundling a font, use
Noto Sans CJK (SIL Open Font License 1.1).

## Explicitly NOT distributed

- Camera-vendor LUTs (DJI D-Log/D-Log2/D-Log M, Canon, Sony, Panasonic):
  no redistribution license. Users place them under `assets/luts/`.
"""


def collect_environment_licenses() -> list[tuple[str, str, str, str]]:
    """(name, version, license-id, license-text) for installed packages."""
    rows = []
    for dist in metadata.distributions():
        name = dist.metadata.get("Name") or "unknown"
        if name.lower() in ("aidirector", "pip", "setuptools", "wheel", "unknown"):
            continue
        version = dist.version or ""
        license_id = (dist.metadata.get("License-Expression") or "").strip()
        if not license_id:
            raw = (dist.metadata.get("License") or "").strip()
            if raw and len(raw) < 60 and "\n" not in raw:
                license_id = raw
        if not license_id:
            for classifier in dist.metadata.get_all("Classifier") or []:
                if classifier.startswith("License ::"):
                    license_id = classifier.split("::")[-1].strip()
                    break
        texts = []
        try:
            for lf in dist.metadata.get_all("License-File") or []:
                try:
                    content = dist.read_text(f"licenses/{lf}") or dist.read_text(lf)
                except Exception:
                    content = None
                if content:
                    texts.append(content.strip())
        except Exception:
            pass
        rows.append((name, version, license_id or "(see project page)",
                     "\n\n".join(texts)))
    rows.sort(key=lambda r: r[0].lower())
    return rows


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("THIRD_PARTY_LICENSES.md")
    rows = collect_environment_licenses()

    parts = [
        "# Third-party licenses\n",
        "This distribution contains or downloads the following third-party "
        "software.\n",
        "## Python packages\n",
        "| Package | Version | License |",
        "|---|---|---|",
    ]
    for name, version, license_id, _text in rows:
        parts.append(f"| {name} | {version} | {license_id} |")
    parts.append("")
    parts.append(STATIC_SECTIONS)
    parts.append("## License texts (Python packages)\n")
    for name, version, _license_id, text in rows:
        if text:
            parts.append(f"### {name} {version}\n")
            parts.append("```")
            parts.append(text)
            parts.append("```\n")

    output.write_text("\n".join(parts), encoding="utf-8")
    print(f"{output}: {len(rows)} packages, {output.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
