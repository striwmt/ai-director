"""Shared fixtures: temp media memory, mock AI providers, generated footage.

AI natural-language content is never exact-match tested (AGENT.md §68);
mocks return deterministic, schema-valid objects.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from aidirector.ai.runtime import ModelRuntimeManager
from aidirector.ai.schemas import (
    Embedding,
    Transcript,
    TranscriptSegment,
    VisionAnalysis,
)
from aidirector.ai.services import AIServices
from aidirector.config import AppConfig, PathsConfig, ensure_dirs
from aidirector.memory.database import connect
from aidirector.memory.repository import MediaMemory


# ---------------------------------------------------------------------------
# Config / memory


@pytest.fixture()
def config(tmp_path: Path) -> AppConfig:
    cfg = AppConfig(paths=PathsConfig(data_dir=tmp_path / ".aidirector"))
    cfg.segmentation.max_segment_seconds = 5.0
    cfg.segmentation.min_segment_seconds = 1.0
    cfg.segmentation.frames_per_segment = 1
    ensure_dirs(cfg)
    return cfg


@pytest.fixture()
def memory(config: AppConfig) -> MediaMemory:
    return MediaMemory(connect(config.paths.db_path))


# ---------------------------------------------------------------------------
# Mock providers


class MockDirectorProvider:
    """Deterministic structured outputs driven by the requested schema."""

    name = "mock:director"

    async def load(self) -> None: ...
    async def unload(self) -> None: ...

    async def generate_structured(self, messages, response_model, *, thinking=None):
        from aidirector.director.schemas import (
            Beat,
            BeatPlan,
            BeatSelection,
            ClipChoice,
            Critique,
            MusicChoice,
            SequenceClip,
            SequencePlan,
            StoryPlan,
        )

        prompt = "\n".join(m.content for m in messages)

        if response_model is StoryPlan:
            return StoryPlan(
                concept="a quiet walk through the test footage",
                tone="calm",
                pace="slow",
                story_arc=["hook", "exploration", "ending"],
            )
        if response_model is BeatPlan:
            return BeatPlan(
                target_duration=30,
                beats=[
                    Beat(name="hook", duration=5, purpose="set the mood"),
                    Beat(name="exploration", duration=20, purpose="show the place"),
                    Beat(name="ending", duration=5, purpose="quiet close"),
                ],
            )
        if response_model is BeatSelection:
            ids = re.findall(r"\[(seg_[0-9a-f]+)\]", prompt)
            beat = re.search(r"name: (\S+)", prompt)
            return BeatSelection(
                beat_name=beat.group(1) if beat else "beat",
                choices=[
                    ClipChoice(segment_id=sid, reason="fits the beat")
                    for sid in dict.fromkeys(ids)  # unique, ordered
                ][:2],
            )
        if response_model is SequencePlan:
            clips = []
            # Parse segment blocks with their positions relative to beat headers.
            blocks = []
            beat = "hook"
            for match in re.finditer(
                r"(### beat: (\S+))|(- id: (seg_[0-9a-f]+)\n\s+source range: "
                r"([0-9.]+) - ([0-9.]+))",
                prompt,
            ):
                if match.group(2):
                    beat = match.group(2)
                elif match.group(4):
                    blocks.append((beat, match.group(4), float(match.group(5)), float(match.group(6))))
            seen = set()
            for beat, sid, start, end in blocks:
                if sid in seen:
                    continue
                seen.add(sid)
                clips.append(
                    SequenceClip(
                        segment_id=sid,
                        source_in=start,
                        source_out=min(end, start + 4.0),
                        story_beat=beat,
                        audio_intent="preserve_ambient",
                        transition="cut",
                        reason="mock pick",
                    )
                )
            if not clips:
                clips.append(
                    SequenceClip(
                        segment_id="seg_missing",
                        source_in=0.0,
                        source_out=2.0,
                        story_beat="hook",
                        reason="fallback",
                    )
                )
            return SequencePlan(clips=clips)
        if response_model is Critique:
            return Critique(score=85, issues=[], revision_required=False)
        if response_model is MusicChoice:
            first = re.search(r"- (\S+\.(?:mp3|wav|m4a))", prompt)
            return MusicChoice(
                file_name=first.group(1) if first else None,
                reason="matches the calm tone",
                confidence=0.9,
            )
        raise AssertionError(f"unexpected schema: {response_model}")


class MockEmbeddingProvider:
    """Deterministic pseudo-embeddings from text hashes."""

    name = "mock:embedding"

    async def load(self) -> None: ...
    async def unload(self) -> None: ...

    async def embed_text(self, texts):
        result = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            vector = [(b - 128) / 128.0 for b in digest[:32]]
            result.append(Embedding(vector=vector, model="mock"))
        return result

    async def embed_images(self, images):
        raise NotImplementedError


class MockVisionProvider:
    name = "mock:vision"
    prompt_version = "mock-v1"

    async def load(self) -> None: ...
    async def unload(self) -> None: ...

    async def analyze_segment(self, images, context):
        return VisionAnalysis(
            description=f"test pattern footage ({context.segment_id})",
            subjects=["test pattern"],
            actions=["displaying colors"],
            mood=["neutral"],
            camera_motion="static",
            story_roles=["b-roll"],
        )


class MockSpeechProvider:
    name = "mock:speech"

    async def load(self) -> None: ...
    async def unload(self) -> None: ...

    async def transcribe(self, audio, options):
        return Transcript(
            language="en",
            duration=1.0,
            segments=[TranscriptSegment(start=0.0, end=1.0, text="test tone")],
        )


@pytest.fixture()
def mock_ai(config: AppConfig) -> AIServices:
    runtime = ModelRuntimeManager(config.models, exclusive=False)
    runtime.override("director", MockDirectorProvider())
    runtime.override("embedding", MockEmbeddingProvider())
    runtime.override("vision", MockVisionProvider())
    runtime.override("speech", MockSpeechProvider())
    return AIServices(runtime)


# ---------------------------------------------------------------------------
# Generated footage fixture (two visually distinct halves => scene change)


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        check=True,
        timeout=120,
    )


@pytest.fixture(scope="session")
def footage_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("footage")
    part1 = root / "_part1.mp4"
    part2 = root / "_part2.mp4"
    clip = root / "DJI_0001.MP4"

    common = [
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
    ]
    _ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=duration=4:size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-shortest", *common, str(part1),
    ])
    _ffmpeg([
        "-f", "lavfi", "-i", "smptebars=duration=4:size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=4",
        "-shortest", *common, str(part2),
    ])
    concat = root / "_concat.txt"
    concat.write_text(f"file '{part1}'\nfile '{part2}'\n")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(clip)])
    part1.unlink()
    part2.unlink()
    concat.unlink()
    # DJI-style LRF sidecar (low-res twin, ignored as primary footage)
    (root / "DJI_0001.LRF").write_bytes(b"\x00" * 128)
    return root


@pytest.fixture(scope="session")
def music_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("music")
    _ffmpeg([
        "-f", "lavfi", "-i", "sine=frequency=220:duration=10",
        "-c:a", "pcm_s16le", str(root / "calm_theme.wav"),
    ])
    _ffmpeg([
        "-f", "lavfi", "-i", "sine=frequency=660:duration=6",
        "-c:a", "pcm_s16le", str(root / "upbeat_energy.wav"),
    ])
    (root / "notes.txt").write_text("not music")
    return root
