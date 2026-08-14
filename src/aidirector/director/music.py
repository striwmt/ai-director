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
from .prompts import load_prompt
from .schemas import MusicChoice, PlanMusic, StoryPlan

log = get_logger("director.music")

MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a"}
_MAX_TRACKS_IN_PROMPT = 60


class MusicTrack(BaseModel):
    """One candidate file (deterministic facts only)."""

    path: Path
    file_name: str
    duration: float | None = None


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


def _format_track_list(tracks: list[MusicTrack]) -> str:
    lines = []
    for track in tracks[:_MAX_TRACKS_IN_PROMPT]:
        if track.duration:
            lines.append(f"- {track.file_name} ({track.duration:.0f}s)")
        else:
            lines.append(f"- {track.file_name}")
    return "\n".join(lines)


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
