"""Music Selector — the AI picks a BGM track from a local folder.

The AI judges meaning (which track fits the story); code guarantees the
facts: the folder scan, track durations, and that the chosen file really
exists (AGENT.md §2). Music files are not ingested into media memory —
the folder is scanned deterministically at director time.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ..ai.schemas import Message
from ..ai.services import AIServices
from ..logging import get_logger
from ..media.probe import probe_file
from ..memory.repository import MediaMemory
from .prompts import load_prompt
from .schemas import MusicChoice, PlanMusic, StoryPlan

log = get_logger("director.music")

MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a"}
_MAX_TRACKS_IN_PROMPT = 60


class MusicTrack(BaseModel):
    """One candidate file. Analysis fields are filled from the cached
    music-library analysis when available (annotate_tracks); otherwise the
    track is judged by name and duration alone."""

    path: Path
    file_name: str
    duration: float | None = None
    # From the analysis cache (perception/music.py); None/empty = unknown.
    bpm: float | None = None
    key: str | None = None                 # e.g. "A minor"
    energy: str | None = None              # low | medium | high
    tags: list[str] = []
    is_vocal: bool | None = None
    lyrics_language: str | None = None
    description: str = ""


def list_music_tracks(music_dir: Path) -> list[MusicTrack]:
    """Scan a folder for music candidates, probing each for duration."""
    tracks: list[MusicTrack] = []
    for path in sorted(music_dir.rglob("*")):
        if path.name.startswith(".") or not path.is_file():
            continue
        if path.suffix.lower() not in MUSIC_EXTENSIONS:
            continue
        duration: float | None = None
        try:
            duration = probe_file(path).duration
        except Exception as exc:
            log.warning("skipping unreadable music file %s: %s", path, exc)
            continue
        tracks.append(
            MusicTrack(path=path.resolve(), file_name=path.name, duration=duration)
        )
    return tracks


def annotate_tracks(tracks: list[MusicTrack], memory: "MediaMemory") -> None:
    """Fill analysis facts from the global music cache (no model calls).

    Tracks without a cached row keep name/duration only — mixed states are
    fine, the prompt tolerates fact-less lines.
    """
    from ..media.ingest import compute_partial_hash, make_music_id

    for track in tracks:
        try:
            track_id = make_music_id(
                compute_partial_hash(track.path), track.path.stat().st_size
            )
            record = memory.get_music_track(track_id)
        except Exception as exc:
            log.warning("music annotation failed for %s: %s", track.file_name, exc)
            continue
        if record is None:
            continue
        features = record.features or {}
        if features.get("bpm"):
            track.bpm = features["bpm"]
        if features.get("key"):
            track.key = f"{features['key']} {features.get('scale', '')}".strip()
        if features.get("energy"):
            track.energy = features["energy"]
        track.tags = [t["tag"] for t in (record.tags or [])]
        lyrics = record.lyrics or {}
        if "is_vocal" in lyrics:
            track.is_vocal = bool(lyrics["is_vocal"])
            track.lyrics_language = lyrics.get("language")
        track.description = record.description or ""


def _format_track_line(track: MusicTrack) -> str:
    # The file name must stay the first token after "- " (resolve_choice
    # matches on it verbatim).
    line = f"- {track.file_name}"
    if track.duration:
        line += f" ({track.duration:.0f}s)"
    facts = []
    if track.bpm:
        facts.append(f"{track.bpm:.0f} BPM")
    if track.key:
        facts.append(track.key)
    if track.energy:
        facts.append(f"energy {track.energy}")
    if facts:
        line += " | " + ", ".join(facts)
    if track.tags:
        line += " | tags: " + ", ".join(track.tags[:6])
    if track.is_vocal is not None:
        vocal = "vocals" + (
            f" ({track.lyrics_language})" if track.lyrics_language else ""
        )
        line += " | " + (vocal if track.is_vocal else "instrumental")
    if track.description:
        line += f' | "{track.description[:160]}"'
    return line


def _format_track_list(tracks: list[MusicTrack]) -> str:
    return "\n".join(
        _format_track_line(track) for track in tracks[:_MAX_TRACKS_IN_PROMPT]
    )


async def select_music(
    ai: AIServices,
    *,
    story: StoryPlan,
    user_prompt: str,
    target_duration: float,
    tracks: list[MusicTrack],
) -> MusicChoice:
    prompt = load_prompt("music").format(
        user_prompt=user_prompt or "(none)",
        concept=story.concept,
        tone=story.tone,
        pace=story.pace,
        target_duration=int(target_duration),
        track_list=_format_track_list(tracks),
    )
    return await ai.generate_structured(
        [Message(role="user", content=prompt)], MusicChoice
    )


def resolve_choice(
    choice: MusicChoice,
    tracks: list[MusicTrack],
    *,
    default_gain_db: float = -18.0,
) -> PlanMusic | None:
    """Deterministic guard: the AI's pick must be a real offered file."""
    if choice.file_name is None:
        log.info("music: AI found no suitable track")
        return None
    match = next((t for t in tracks if t.file_name == choice.file_name), None)
    if match is None:
        wanted = choice.file_name.lower()
        match = next((t for t in tracks if t.file_name.lower() == wanted), None)
    if match is None:
        log.warning("music: AI chose unknown file %r; skipping", choice.file_name)
        return None
    return PlanMusic(
        path=str(match.path),
        file_name=match.file_name,
        duration=match.duration,
        gain_db=default_gain_db,
        reason=choice.reason,
        confidence=choice.confidence,
    )


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    den = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return num / den if den else 0.0


def rank_tracks_for_prompt(
    tracks: list[MusicTrack],
    memory: MediaMemory,
    *,
    story: StoryPlan,
    user_prompt: str,
    clap_model: str,
) -> list[MusicTrack]:
    """Order a >60-track library by CLAP similarity to the story.

    The single query embedding runs on the CPU (small text tower), so this
    never contends with the director LLM for the GPU. Falls back to the
    incoming order (head truncation) when CLAP or embeddings are missing.
    """
    if len(tracks) <= _MAX_TRACKS_IN_PROMPT:
        return tracks
    try:
        from ..ai.providers.music import clap_text_embedding_cpu
        from ..media.ingest import compute_partial_hash, make_music_id

        stored = dict(memory.iter_music_embeddings("audio"))
        if not stored:
            raise ValueError("no stored music embeddings")
        query = " ".join(
            part for part in (
                story.concept, story.tone, f"{story.pace} pace", user_prompt
            ) if part
        )
        query_vector = clap_text_embedding_cpu(clap_model, query)

        def sort_key(track: MusicTrack) -> float:
            track_id = make_music_id(
                compute_partial_hash(track.path), track.path.stat().st_size
            )
            vector = stored.get(track_id)
            return _cosine(query_vector, vector) if vector else -2.0

        return sorted(tracks, key=sort_key, reverse=True)
    except Exception as exc:
        log.warning("music ranking skipped (%s); truncating list", exc)
        return tracks
