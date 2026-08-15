"""Integration: Ingest -> Color -> Analyze -> Media Memory -> Director ->
Edit Plan -> Preview, on generated fixture footage with mock AI providers
(AGENT.md §68 Integration).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aidirector.director.orchestrator import run_director
from aidirector.pipeline import run_analyze, run_ingest
from aidirector.process import tool_available
from aidirector.timeline.compiler import compile_timeline
from aidirector.timeline.preview import render_preview
from aidirector.timeline.validate import validate_edit_plan

pytestmark = pytest.mark.skipif(
    not tool_available("ffmpeg") or not tool_available("ffprobe"),
    reason="ffmpeg/ffprobe required",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_ingest_only(footage_dir, config, memory):
    report = run_ingest(footage_dir, config, memory, PROJECT_ROOT)
    assert len(report.ingested) == 1
    assert not report.failed
    assert len(report.sidecars) == 1  # the .LRF twin

    project = memory.get_or_create_project(footage_dir.name, footage_dir)
    assets = memory.list_assets(project.id, kind="video")
    assert len(assets) == 1
    asset = assets[0]
    assert asset.duration is not None and asset.duration > 7
    assert asset.metadata.has_audio
    # LRF is linked to the main clip, not ingested as primary footage.
    assert any(p.endswith(".LRF") for p in asset.sidecar_paths)
    # Re-ingest is incremental.
    report2 = run_ingest(footage_dir, config, memory, PROJECT_ROOT)
    assert len(report2.skipped_unchanged) == 1
    assert not report2.ingested


async def test_full_pipeline(footage_dir, config, memory, mock_ai):
    project_id = await run_analyze(
        footage_dir, config, memory, mock_ai, PROJECT_ROOT
    )

    # Media Memory has segments, frames, features, transcript, annotations.
    segments = memory.list_project_segments(project_id)
    assert len(segments) >= 2, "scene change at 4s should split the clip"
    for segment in segments:
        assert memory.list_frames(segment.id), "each segment has frames"
        assert memory.get_technical_features(segment.id) is not None
        annotation = memory.get_semantic_annotation(segment.id)
        assert annotation is not None and annotation.description

    asset = memory.list_assets(project_id, kind="video")[0]
    assert asset.status == "analyzed"
    assert memory.get_transcript(asset.id) is not None
    assert memory.get_analysis_proxy(asset.id) is not None
    assert memory.iter_segment_embeddings(project_id), "embeddings stored"

    # Director produces a valid Edit Plan.
    plan_id, plan = await run_director(
        project_id, config, memory, mock_ai,
        user_prompt="a calm walk", target_duration=10.0,
        profile_name="travel_vlog",
    )
    assert plan.clips, "plan has clips"
    assert all(c.reason for c in plan.clips), "every decision has a reason"
    validate_edit_plan(plan, memory)

    # Plan is persisted and diffable JSON.
    stored = memory.get_edit_plan(plan_id)
    assert json.loads(stored)["intent"]["target_duration"] == 10.0

    # Preview renders from proxies.
    timeline = compile_timeline(plan, memory)
    assert timeline.clips[0].proxy_path is not None
    output = render_preview(timeline, config)
    assert output.is_file() and output.stat().st_size > 10_000


async def test_director_with_user_outline(footage_dir, config, memory, mock_ai):
    project_id = await run_analyze(
        footage_dir, config, memory, mock_ai, PROJECT_ROOT
    )
    outline = ["出発", "電車移動", "レストラン"]
    plan_id, plan = await run_director(
        project_id, config, memory, mock_ai,
        user_prompt="旅の一日", target_duration=10.0,
        profile_name="travel_vlog", outline=outline,
    )
    assert plan.intent.outline == outline
    assert plan.clips, "plan has clips"
    # Every clip serves one of the user's flow sections, in flow order.
    beats_used = [c.story_beat for c in plan.clips]
    assert set(beats_used) <= set(outline)
    positions = [outline.index(b) for b in beats_used]
    assert positions == sorted(positions), "clips follow the user's flow order"
    assert json.loads(memory.get_edit_plan(plan_id))["intent"]["outline"] == outline
    validate_edit_plan(plan, memory)


async def test_full_pipeline_with_music(footage_dir, music_dir, config, memory, mock_ai):
    from aidirector.media.probe import probe_file

    project_id = await run_analyze(
        footage_dir, config, memory, mock_ai, PROJECT_ROOT
    )
    plan_id, plan = await run_director(
        project_id, config, memory, mock_ai,
        user_prompt="a calm walk", target_duration=10.0,
        profile_name="travel_vlog", music_dir=music_dir,
    )
    assert plan.music is not None, "AI picked a track"
    assert plan.music.file_name == "calm_theme.wav"
    assert plan.music.reason and plan.music.duration
    assert Path(plan.music.path).is_file()
    validate_edit_plan(plan, memory)
    assert json.loads(memory.get_edit_plan(plan_id))["music"]["file_name"] == (
        "calm_theme.wav"
    )

    # Music is mixed into the preview; duration stays that of the program.
    timeline = compile_timeline(plan, memory)
    output = render_preview(timeline, config)
    info = probe_file(output)
    assert info.audio_streams, "preview has an audio stream"
    assert info.duration == pytest.approx(timeline.duration, abs=0.5)

    # Disabled music falls back to the plain render path.
    timeline.music.enabled = False
    output2 = render_preview(
        timeline, config, config.paths.renders_dir / "no_music.mp4"
    )
    assert output2.is_file() and output2.stat().st_size > 10_000
