"""Music-library analysis: features, tags, cache, migration, selection."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aidirector.director.music import (
    MusicTrack,
    _format_track_line,
    annotate_tracks,
    list_music_tracks,
)
from aidirector.memory.database import connect
from aidirector.memory.repository import MediaMemory
from aidirector.perception.music import (
    TAG_VOCABULARY,
    analyze_music_library,
    music_track_id,
    score_tags,
)

# ---------------------------------------------------------------------------
# Deterministic features


def _write_click_track(path: Path, bpm: float = 120.0, seconds: float = 15.0):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    sr = 22050
    samples = np.zeros(int(sr * seconds), dtype=np.float32)
    interval = 60.0 / bpm
    beat = 0.0
    while beat < seconds - 0.1:
        i = int(beat * sr)
        samples[i:i + 800] = (
            np.sin(2 * np.pi * 1000 * np.arange(800) / sr).astype(np.float32) * 0.8
        )
        beat += interval
    sf.write(str(path), samples, sr)


def test_bpm_detection(tmp_path):
    pytest.importorskip("librosa")
    from aidirector.perception.music_features import extract_music_features

    wav = tmp_path / "click.wav"
    _write_click_track(wav, bpm=120.0)
    features = extract_music_features(wav)
    bpm = features["bpm"]
    # Accept the true tempo or a half/double harmonic.
    assert any(abs(bpm - target) <= 5 for target in (60, 120, 240)), features
    assert features["backend"] in ("essentia", "librosa")
    assert features["energy"] in ("low", "medium", "high")


def test_key_detection_on_pure_tone(tmp_path):
    pytest.importorskip("librosa")
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    from aidirector.perception.music_features import _extract_librosa

    sr = 22050
    t = np.arange(sr * 8) / sr
    y = (np.sin(2 * np.pi * 440.0 * t) * 0.5).astype(np.float32)  # A4
    wav = tmp_path / "a440.wav"
    sf.write(str(wav), y, sr)
    features = _extract_librosa(wav)
    assert features["key"] == "A", features


def test_energy_buckets_monotonic(tmp_path):
    pytest.importorskip("librosa")
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    from aidirector.perception.music_features import _extract_librosa

    sr = 22050
    t = np.arange(sr * 4) / sr
    order = {"low": 0, "medium": 1, "high": 2}
    levels = []
    for name, amp in (("quiet", 0.01), ("loud", 0.9)):
        wav = tmp_path / f"{name}.wav"
        sf.write(str(wav), (np.sin(2 * np.pi * 220 * t) * amp).astype("float32"), sr)
        levels.append(order[_extract_librosa(wav)["energy"]])
    assert levels[0] < levels[1]


# ---------------------------------------------------------------------------
# Tag scoring (pure function)


def test_score_tags_picks_most_similar():
    flat = [("mood", "calm relaxing"), ("mood", "energetic driving"),
            ("genre", "ambient")]
    audio = [1.0, 0.0, 0.0]
    vectors = [[0.9, 0.1, 0.0], [-0.5, 0.8, 0.0], [0.7, 0.7, 0.0]]
    tags = score_tags(audio, flat, vectors, per_category=1)
    by_category = {t["category"]: t["tag"] for t in tags}
    assert by_category["mood"] == "calm relaxing"
    assert by_category["genre"] == "ambient"


# ---------------------------------------------------------------------------
# Migration v3


def test_migration_creates_music_tracks(tmp_path):
    conn = connect(tmp_path / "fresh.sqlite3")
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "music_tracks" in tables
    conn.close()


def test_migration_upgrades_v2_database(tmp_path):
    # Simulate a pre-v3 DB: apply only the first two migrations.
    from aidirector.memory.migrations import _MIGRATIONS

    db = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(db)
    raw.executescript("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for version, script in enumerate(_MIGRATIONS[:2], start=1):
        raw.executescript(script)
        raw.execute("INSERT INTO schema_version VALUES (?)", (version,))
    raw.commit()
    raw.close()

    conn = connect(db)  # runs remaining migrations
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "music_tracks" in tables
    conn.close()


# ---------------------------------------------------------------------------
# Analysis pipeline with mocks (cache behavior)


async def test_analyze_library_and_cache(music_dir, config, memory, mock_ai):
    count = await analyze_music_library(music_dir, config, memory, mock_ai)
    assert count == 2

    calm = memory.get_music_track(music_track_id(music_dir / "calm_theme.wav"))
    assert calm is not None and calm.analyzed_at
    assert calm.features.get("bpm") is not None or calm.features == {}
    assert calm.tags, "zero-shot tags stored"
    categories = {t["category"] for t in calm.tags}
    assert categories <= set(TAG_VOCABULARY)
    assert "is_vocal" in calm.lyrics
    assert calm.description.startswith("A calm ambient")
    assert memory.get_embedding("music", calm.id, "audio", "mock-clap")

    # Second run: fully cached — zero provider calls.
    clap = mock_ai.runtime._overrides["music_embedding"]
    omni = mock_ai.runtime._overrides["music_understanding"]
    clap.audio_calls = clap.text_calls = omni.calls = 0
    count2 = await analyze_music_library(music_dir, config, memory, mock_ai)
    assert count2 == 0
    assert clap.audio_calls == 0 and clap.text_calls == 0 and omni.calls == 0


async def test_cache_survives_rename(music_dir, config, memory, mock_ai, tmp_path):
    await analyze_music_library(music_dir, config, memory, mock_ai)
    renamed_dir = tmp_path / "moved"
    renamed_dir.mkdir()
    target = renamed_dir / "renamed_song.wav"
    target.write_bytes((music_dir / "calm_theme.wav").read_bytes())

    clap = mock_ai.runtime._overrides["music_embedding"]
    clap.audio_calls = 0
    count = await analyze_music_library(renamed_dir, config, memory, mock_ai)
    assert count == 0, "content key keeps the cache valid across renames"
    assert clap.audio_calls == 0
    record = memory.get_music_track(music_track_id(target))
    assert record.file_name == "renamed_song.wav"  # path metadata refreshed


# ---------------------------------------------------------------------------
# Selection integration


async def test_annotated_selection(music_dir, config, memory, mock_ai):
    await analyze_music_library(music_dir, config, memory, mock_ai)
    tracks = list_music_tracks(music_dir)
    annotate_tracks(tracks, memory)
    calm = next(t for t in tracks if t.file_name == "calm_theme.wav")
    assert calm.tags and calm.description
    assert calm.is_vocal is not None

    line = _format_track_line(calm)
    assert line.startswith("- calm_theme.wav")
    assert "tags:" in line and '"A calm ambient' in line

    # conftest's MusicChoice mock extracts the first filename from the
    # prompt — the annotated line format must keep that working.
    import re

    assert re.search(r"- (\S+\.(?:mp3|wav|m4a))", line).group(1) == "calm_theme.wav"

    from aidirector.director.music import resolve_choice, select_music
    from aidirector.director.schemas import StoryPlan

    story = StoryPlan(concept="quiet walk", tone="calm", pace="slow",
                      story_arc=["hook"])
    choice = await select_music(
        mock_ai, story=story, user_prompt="静かな散歩", target_duration=30,
        tracks=tracks,
    )
    music = resolve_choice(choice, tracks)
    assert music is not None and music.file_name == "calm_theme.wav"


def test_format_line_without_analysis():
    track = MusicTrack(path=Path("/m/x.mp3"), file_name="x.mp3", duration=90.0)
    assert _format_track_line(track) == "- x.mp3 (90s)"
