"""Deterministic music features: BPM, key, energy (AGENT.md §2 — facts).

Essentia (Linux x86_64 wheel) is preferred for its multifeature rhythm
extractor and key profile; librosa is the portable fallback (all OS).
Callers get a plain dict either way, with `backend` recording which ran.
"""

from __future__ import annotations

import math
from pathlib import Path

from ..logging import get_logger

log = get_logger("perception.music")

# Krumhansl-Schmuckler key profiles (major/minor), C-rooted.
_MAJOR_PROFILE = [
    6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88,
]
_MINOR_PROFILE = [
    6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17,
]
_PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _energy_bucket(rms_db: float) -> str:
    if rms_db > -14.0:
        return "high"
    if rms_db > -25.0:
        return "medium"
    return "low"


def _extract_essentia(wav: Path) -> dict:
    import essentia.standard as es

    audio = es.MonoLoader(filename=str(wav))()
    bpm, _beats, confidence, _, _ = es.RhythmExtractor2013(
        method="multifeature"
    )(audio)
    key, scale, strength = es.KeyExtractor()(audio)
    rms = float(es.RMS()(audio))
    rms_db = 20 * math.log10(rms) if rms > 0 else -120.0
    return {
        "bpm": round(float(bpm), 1),
        "bpm_confidence": round(float(confidence), 2),
        "key": key,
        "scale": scale,
        "key_strength": round(float(strength), 2),
        "rms_db": round(rms_db, 1),
        "energy": _energy_bucket(rms_db),
        "backend": "essentia",
    }


def _correlate(a: list[float], b: list[float]) -> float:
    n = len(a)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    den = math.sqrt(
        sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b)
    )
    return num / den if den else 0.0


def _estimate_key_librosa(chroma_mean: list[float]) -> tuple[str, str, float]:
    """Krumhansl-Schmuckler correlation over all 24 rotations."""
    best = ("C", "major", -2.0)
    for shift in range(12):
        rotated = chroma_mean[shift:] + chroma_mean[:shift]
        for profile, scale in ((_MAJOR_PROFILE, "major"), (_MINOR_PROFILE, "minor")):
            score = _correlate(rotated, profile)
            if score > best[2]:
                best = (_PITCHES[shift], scale, score)
    return best


def _extract_librosa(wav: Path) -> dict:
    import librosa
    import numpy as np

    y, sr = librosa.load(str(wav), sr=None, mono=True)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0])
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    key, scale, strength = _estimate_key_librosa(
        [float(x) for x in chroma.mean(axis=1)]
    )
    rms = float(np.mean(librosa.feature.rms(y=y)))
    rms_db = 20 * math.log10(rms) if rms > 0 else -120.0
    return {
        "bpm": round(tempo, 1),
        "bpm_confidence": None,
        "key": key,
        "scale": scale,
        "key_strength": round(strength, 2),
        "rms_db": round(rms_db, 1),
        "energy": _energy_bucket(rms_db),
        "backend": "librosa",
    }


def extract_music_features(wav: Path) -> dict:
    """BPM/key/energy from a decoded mono wav. Raises ImportError only when
    neither backend is installed."""
    try:
        import essentia.standard  # noqa: F401

        return _extract_essentia(wav)
    except ImportError:
        pass
    except Exception as exc:  # essentia present but failed — fall through
        log.warning("essentia failed (%s); falling back to librosa", exc)
    return _extract_librosa(wav)
