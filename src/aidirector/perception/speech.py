"""Speech perception: transcribe assets and map words to segments."""

from __future__ import annotations

from pathlib import Path

from ..ai.schemas import (
    Provenance,
    Transcript,
    TranscriptSegment,
    TranscriptionOptions,
)
from ..ai.services import AIServices
from ..config import AppConfig
from ..logging import get_logger
from ..memory.models import AssetRecord, SegmentRecord
from ..memory.repository import MediaMemory
from .audio import extract_audio_wav

log = get_logger("perception.speech")


async def transcribe_asset(
    asset: AssetRecord,
    ai: AIServices,
    config: AppConfig,
    memory: MediaMemory,
    *,
    force: bool = False,
) -> Transcript | None:
    if not asset.metadata.has_audio and asset.kind != "audio":
        return None
    if not force:
        cached = memory.get_transcript(asset.id)
        if cached is not None:
            return cached

    wav = config.paths.cache_dir / "audio" / f"{asset.id}.wav"
    if not wav.is_file():
        extract_audio_wav(Path(asset.path), wav)

    options = TranscriptionOptions()
    transcript = await ai.transcribe(wav, options)
    provenance = Provenance(
        provider=ai.provider_name("speech"),
        model=ai.provider_name("speech").split(":", 1)[-1],
        options=options.model_dump(),
        language=transcript.language,
    )
    memory.save_transcript(asset.id, transcript, provenance)
    log.info(
        "transcribed %s: %s, %d segments",
        asset.file_name, transcript.language, len(transcript.segments),
    )
    return transcript


def transcript_for_span(
    transcript: Transcript | None, start: float, end: float
) -> str:
    """Text spoken within [start, end) of the source asset."""
    if transcript is None:
        return ""
    parts: list[str] = []
    for seg in transcript.segments:
        if seg.end <= start or seg.start >= end:
            continue
        parts.append(seg.text.strip())
    return " ".join(parts).strip()


def segment_transcripts(
    transcript: Transcript | None, segments: list[SegmentRecord]
) -> dict[str, str]:
    return {
        seg.id: transcript_for_span(transcript, seg.start, seg.end)
        for seg in segments
    }


_MIN_SUBTITLE_SECONDS = 0.3


def subtitle_lines_for_span(
    transcript: Transcript | None, start: float, end: float
) -> list[tuple[float, float, str]]:
    """Spoken lines overlapping [start, end), clamped to the span.

    Word timestamps give precise partial-overlap text; a segment without
    word data falls back to its full text. Returns (start, end, text) in
    SOURCE-time seconds. Deterministic — this is the fact layer for
    subtitles.
    """
    if transcript is None:
        return []
    lines: list[tuple[float, float, str]] = []
    for seg in transcript.segments:
        if seg.end <= start or seg.start >= end:
            continue
        if seg.words:
            words = [w for w in seg.words if w.end > start and w.start < end]
            if not words:
                continue
            text = "".join(w.text for w in words).strip()
            line_start, line_end = words[0].start, words[-1].end
        else:
            text = seg.text.strip()
            line_start, line_end = seg.start, seg.end
        line_start = max(line_start, start)
        line_end = min(line_end, end)
        if not text or line_end - line_start < _MIN_SUBTITLE_SECONDS:
            continue
        lines.append((round(line_start, 3), round(line_end, 3), text))
    return lines
