"""Music-library analysis phase (BGM feature).

Observe -> Understand -> Persist -> Retrieve (AGENT.md §78): every track in
the user's music folder is analyzed once, cached globally by content hash,
and the director later retrieves plain facts — no model runs at selection
time. Each component degrades gracefully; a partially analyzed row is
resumed on the next run (analyzed_at is only set when everything enabled
succeeded).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..ai.schemas import TranscriptionOptions
from ..ai.services import AIServices
from ..config import AppConfig
from ..director.music import MusicTrack, list_music_tracks
from ..logging import get_logger
from ..media.ingest import compute_partial_hash, make_music_id
from ..memory.models import MusicTrackRecord
from ..memory.repository import MediaMemory
from ..process import run_command

log = get_logger("perception.music")

ANALYSIS_VERSION = 1

# Excerpt windows per component (seconds / sample rate).
_DSP_SECONDS, _DSP_RATE = 120.0, 22050
_CLAP_SECONDS, _CLAP_RATE = 120.0, 48000

_DESCRIBE_PROMPT = (
    "Describe this music track in 2-3 sentences for a video editor: "
    "genre, prominent instruments, mood/energy, and vocal style if any."
)

# Zero-shot tag vocabulary. Scored via CLAP audio-text similarity.
TAG_VOCABULARY: dict[str, list[str]] = {
    "genre": [
        "rock", "pop", "electronic dance", "hip-hop", "jazz",
        "classical orchestral", "acoustic folk", "ambient",
        "cinematic epic", "lo-fi chill", "funk", "metal",
    ],
    "mood": [
        "happy uplifting", "sad melancholic", "calm relaxing",
        "energetic driving", "dramatic tense", "romantic",
        "playful", "nostalgic", "dark", "mysterious",
    ],
    "instrument": [
        "piano", "acoustic guitar", "electric guitar", "strings",
        "synthesizer", "heavy drums", "brass", "solo vocals", "choir",
    ],
}
_TAG_TEMPLATE = "This audio is {tag} music."


def _decode_excerpt(
    source: Path, output: Path, *, sample_rate: int,
    start: float = 0.0, duration: float | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    command += ["-i", str(source)]
    if duration is not None:
        command += ["-t", f"{duration:.3f}"]
    command += ["-vn", "-ac", "1", "-ar", str(sample_rate),
                "-c:a", "pcm_s16le", str(output)]
    run_command(command, timeout=600.0)
    return output


def _excerpt_window(track_duration: float | None, want: float) -> tuple[float, float | None]:
    """(start, duration) of a centered excerpt; whole file when short."""
    if not track_duration or track_duration <= want:
        return 0.0, None
    return (track_duration - want) / 2.0, want


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    den = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return num / den if den else 0.0


def music_track_id(path: Path) -> str:
    return make_music_id(compute_partial_hash(path), path.stat().st_size)


async def analyze_music_library(
    music_dir: Path,
    config: AppConfig,
    memory: MediaMemory,
    ai: AIServices,
    progress: Callable[[str], None] | None = None,
) -> int:
    """Analyze every uncached track in music_dir. Returns #tracks analyzed."""
    tracks = list_music_tracks(music_dir)
    if not tracks:
        log.info("music: no candidate files in %s", music_dir)
        return 0

    # 1. Hash + cache check (a fully cached library never loads a model).
    pending: list[tuple[MusicTrack, MusicTrackRecord]] = []
    for track in tracks:
        track_id = music_track_id(track.path)
        record = memory.get_music_track(track_id)
        if record is not None:
            if record.path != str(track.path) or record.file_name != track.file_name:
                record.path = str(track.path)
                record.file_name = track.file_name
                memory.save_music_track(record)
            if record.analyzed_at and record.analysis_version == ANALYSIS_VERSION:
                continue
        else:
            record = MusicTrackRecord(
                id=track_id, path=str(track.path), file_name=track.file_name,
                duration=track.duration, analysis_version=ANALYSIS_VERSION,
            )
            memory.save_music_track(record)
        pending.append((track, record))

    if not pending:
        log.info("music: all %d tracks already analyzed", len(tracks))
        return 0
    log.info("music: analyzing %d of %d tracks", len(pending), len(tracks))
    cache_dir = config.paths.cache_dir / "music"
    # analyzed_at is only set when every enabled component succeeded for
    # that record — a failed component is retried on the next run.
    completed: dict[str, int] = {record.id: 0 for _t, record in pending}
    describe_enabled = config.models.music_understanding.provider != "none"
    required = 3 + (1 if describe_enabled else 0)

    # 2. M1 — deterministic DSP features (CPU).
    try:
        from .music_features import extract_music_features

        for track, record in pending:
            start, dur = _excerpt_window(track.duration, _DSP_SECONDS)
            wav = _decode_excerpt(
                track.path, cache_dir / f"{record.id}_dsp.wav",
                sample_rate=_DSP_RATE, start=start, duration=dur,
            )
            try:
                record.features = extract_music_features(wav)
                record.provenance["features"] = record.features.get("backend", "")
                memory.save_music_track(record)
                completed[record.id] += 1
            finally:
                wav.unlink(missing_ok=True)
    except Exception as exc:
        log.warning("music feature extraction skipped: %s", exc)

    # 3. M2 — CLAP embedding + zero-shot tags.
    try:
        flat_tags = [
            (category, tag)
            for category, tags in TAG_VOCABULARY.items()
            for tag in tags
        ]
        tag_vectors = await ai.music_text_embed(
            [_TAG_TEMPLATE.format(tag=tag) for _, tag in flat_tags]
        )
        label = ai.provider_name("music_embedding")
        for track, record in pending:
            start, dur = _excerpt_window(track.duration, _CLAP_SECONDS)
            wav = _decode_excerpt(
                track.path, cache_dir / f"{record.id}_clap.wav",
                sample_rate=_CLAP_RATE, start=start, duration=dur,
            )
            try:
                audio_embedding = await ai.embed_audio(wav)
                memory.save_embedding(
                    "music", record.id, "audio",
                    audio_embedding.model, audio_embedding.vector,
                )
                record.tags = score_tags(
                    audio_embedding.vector, flat_tags,
                    [e.vector for e in tag_vectors],
                )
                record.provenance["tags"] = label
                memory.save_music_track(record)
                completed[record.id] += 1
            finally:
                wav.unlink(missing_ok=True)
    except Exception as exc:
        log.warning("music tagging (CLAP) skipped: %s", exc)
    finally:
        await ai.runtime.release("music_embedding")

    # 4. M3 — lyrics / vocal detection (whisper reads the file directly).
    try:
        options = TranscriptionOptions(
            word_timestamps=False, vad=True, condition_on_previous_text=False,
        )
        for track, record in pending:
            transcript = await ai.transcribe(track.path, options)
            text = transcript.text.strip()
            spoken = sum(s.end - s.start for s in transcript.segments)
            ratio = spoken / track.duration if track.duration else 0.0
            record.lyrics = {
                "language": transcript.language,
                "is_vocal": ratio > 0.2 and len(text) > 40,
                "speech_ratio": round(ratio, 2),
                "excerpt": text[:300],
            }
            record.provenance["lyrics"] = ai.provider_name("speech")
            memory.save_music_track(record)
            completed[record.id] += 1
    except Exception as exc:
        log.warning("music lyrics detection skipped: %s", exc)
    finally:
        await ai.runtime.release("speech")

    # 5. M4 — audio-LLM description (optional).
    understanding_cfg = config.models.music_understanding
    if describe_enabled:
        try:
            for track, record in pending:
                max_seconds = float(
                    understanding_cfg.extra.get("max_audio_seconds", 30)
                )
                start, dur = _excerpt_window(track.duration, max_seconds)
                wav = _decode_excerpt(
                    track.path, cache_dir / f"{record.id}_llm.wav",
                    sample_rate=16000, start=start,
                    duration=dur if dur is not None else max_seconds,
                )
                try:
                    record.description = await ai.describe_audio(
                        wav, _DESCRIBE_PROMPT
                    )
                    record.provenance["description"] = ai.provider_name(
                        "music_understanding"
                    )
                    memory.save_music_track(record)
                    completed[record.id] += 1
                finally:
                    wav.unlink(missing_ok=True)
        except Exception as exc:
            log.warning("music description skipped: %s", exc)
        finally:
            await ai.runtime.release("music_understanding")

    # 6. Mark complete (only fully successful records; others retry later).
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    done = 0
    for _track, record in pending:
        if completed[record.id] >= required:
            record.analyzed_at = now
            memory.save_music_track(record)
            done += 1
    if done < len(pending):
        log.warning(
            "music: %d of %d tracks only partially analyzed (will retry)",
            len(pending) - done, len(pending),
        )
    log.info("music: analyzed %d tracks", done)
    return len(pending)


def score_tags(
    audio_vector: list[float],
    flat_tags: list[tuple[str, str]],
    tag_vectors: list[list[float]],
    *,
    per_category: int = 2,
) -> list[dict]:
    """Top tags per category by cosine similarity (pure function)."""
    scored: dict[str, list[dict]] = {}
    for (category, tag), vector in zip(flat_tags, tag_vectors):
        scored.setdefault(category, []).append(
            {"tag": tag, "category": category,
             "score": round(_cosine(audio_vector, vector), 4)}
        )
    result: list[dict] = []
    for category, entries in scored.items():
        entries.sort(key=lambda e: e["score"], reverse=True)
        result.extend(entries[:per_category])
    return result
