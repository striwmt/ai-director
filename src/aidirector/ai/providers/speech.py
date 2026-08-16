"""Speech (ASR) providers.

faster-whisper internals never leave this module; the rest of the system
sees only the standard Transcript schema (AGENT.md §31-§33).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ...config import ModelEndpointConfig
from ...errors import ProviderError
from ...logging import get_logger
from ..schemas import Transcript, TranscriptSegment, TranscriptWord, TranscriptionOptions

log = get_logger("provider.speech")


def _preload_cuda12_libs() -> None:
    """Make pip-shipped CUDA 12 libs (cuBLAS/cuDNN) visible to CTranslate2.

    CTranslate2 links against CUDA 12; when the venv's primary CUDA runtime
    is newer (torch cu13+), loading by library name fails unless the cu12
    libs are already loaded. Linux wheels ship ``lib/*.so*``, Windows wheels
    ``bin/*.dll``. Failures here are fine — CPU fallback still works.
    """
    import ctypes
    import importlib
    from pathlib import Path as _Path

    lib_dirs: list[_Path] = []
    for name in (
        "nvidia.cublas.lib", "nvidia.cublas.bin",
        "nvidia.cudnn.lib", "nvidia.cudnn.bin",
    ):
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        lib_dirs.append(_Path(module.__path__[0]))

    libs: list[_Path] = []
    for directory in lib_dirs:
        for pattern in ("*.so*", "*.dll"):
            libs.extend(sorted(directory.glob(pattern)))

    load_mode = getattr(ctypes, "RTLD_GLOBAL", ctypes.DEFAULT_MODE)
    # Two passes: inter-library dependencies resolve on the second attempt.
    for _ in range(2):
        for lib in libs:
            try:
                ctypes.CDLL(str(lib), mode=load_mode)
            except OSError:
                continue


class FasterWhisperProvider:
    """Local faster-whisper with CUDA and CPU fallback."""

    def __init__(self, cfg: ModelEndpointConfig) -> None:
        self._cfg = cfg
        self._model: Any = None
        self.name = f"faster-whisper:{cfg.model}"

    async def load(self) -> None:
        if self._model is not None:
            return
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ProviderError(
                "faster-whisper is not installed. Install with: uv sync --extra speech"
            ) from exc

        device = self._cfg.device
        compute_type = self._cfg.compute_type or "default"
        attempts: list[tuple[str, str]] = []
        if device in ("auto", "cuda"):
            _preload_cuda12_libs()
            attempts.append(("cuda", compute_type))
        attempts.append(("cpu", "int8"))

        last_error: Exception | None = None
        for dev, ct in attempts:
            try:
                self._model = WhisperModel(self._cfg.model, device=dev, compute_type=ct)
                log.info("loaded whisper model %s on %s (%s)", self._cfg.model, dev, ct)
                return
            except Exception as exc:  # CUDA missing / OOM -> CPU fallback
                last_error = exc
                log.warning("whisper load failed on %s: %s", dev, exc)
        raise ProviderError(f"could not load whisper model: {last_error}")

    async def unload(self) -> None:
        from ._cuda import free_cuda_memory

        self._model = None
        free_cuda_memory()

    async def transcribe(self, audio: Path, options: TranscriptionOptions) -> Transcript:
        if self._model is None:
            await self.load()
        return await asyncio.to_thread(self._transcribe_sync, audio, options)

    def _transcribe_sync(self, audio: Path, options: TranscriptionOptions) -> Transcript:
        segments_iter, info = self._model.transcribe(
            str(audio),
            language=options.language,
            word_timestamps=options.word_timestamps,
            vad_filter=options.vad,
            beam_size=options.beam_size,
            condition_on_previous_text=options.condition_on_previous_text,
        )
        segments: list[TranscriptSegment] = []
        for seg in segments_iter:
            words = [
                TranscriptWord(
                    start=w.start, end=w.end, text=w.word, probability=w.probability
                )
                for w in (seg.words or [])
            ]
            segments.append(
                TranscriptSegment(start=seg.start, end=seg.end, text=seg.text, words=words)
            )
        return Transcript(
            language=info.language or "unknown",
            duration=float(info.duration or 0.0),
            segments=segments,
        )
