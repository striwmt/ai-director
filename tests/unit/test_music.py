"""Music selection: deterministic scan/resolve + AI choice via mock."""

from __future__ import annotations

from pathlib import Path

from aidirector.director.music import (
    MusicTrack,
    list_music_tracks,
    resolve_choice,
    select_music,
)
from aidirector.director.schemas import MusicChoice, StoryPlan


def _tracks() -> list[MusicTrack]:
    return [
        MusicTrack(path=Path("/music/calm_theme.wav"),
                   file_name="calm_theme.wav", duration=120.0),
        MusicTrack(path=Path("/music/Upbeat_Energy.mp3"),
                   file_name="Upbeat_Energy.mp3", duration=90.0),
    ]


def test_list_music_tracks(music_dir: Path):
    tracks = list_music_tracks(music_dir)
    names = [t.file_name for t in tracks]
    assert names == ["calm_theme.wav", "upbeat_energy.wav"]  # sorted, no .txt
    assert all(t.duration and t.duration > 1 for t in tracks)
    assert all(t.path.is_absolute() for t in tracks)


def test_list_music_tracks_empty(tmp_path: Path):
    assert list_music_tracks(tmp_path) == []


def test_resolve_choice_exact_match():
    music = resolve_choice(
        MusicChoice(file_name="calm_theme.wav", reason="calm", confidence=0.8),
        _tracks(), default_gain_db=-20.0,
    )
    assert music is not None
    assert music.path == "/music/calm_theme.wav"
    assert music.duration == 120.0
    assert music.gain_db == -20.0
    assert music.reason == "calm"
    assert music.enabled and music.ducking


def test_resolve_choice_case_insensitive():
    music = resolve_choice(
        MusicChoice(file_name="upbeat_energy.mp3"), _tracks()
    )
    assert music is not None
    assert music.file_name == "Upbeat_Energy.mp3"


def test_resolve_choice_rejects_unknown_and_none():
    assert resolve_choice(MusicChoice(file_name="hallucinated.mp3"), _tracks()) is None
    assert resolve_choice(MusicChoice(file_name=None), _tracks()) is None


async def test_select_music_with_mock_ai(mock_ai, music_dir: Path):
    story = StoryPlan(
        concept="a quiet walk", tone="calm", pace="slow", story_arc=["hook"]
    )
    tracks = list_music_tracks(music_dir)
    choice = await select_music(
        mock_ai, story=story, user_prompt="静かな旅Vlog",
        target_duration=30.0, tracks=tracks,
    )
    # The mock picks the first offered file; resolve must accept it.
    music = resolve_choice(choice, tracks)
    assert music is not None
    assert music.file_name == "calm_theme.wav"
    assert music.reason
