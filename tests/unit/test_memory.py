from pathlib import Path

from aidirector.ai.schemas import (
    Provenance,
    Transcript,
    TranscriptSegment,
    VisionAnalysis,
)
from aidirector.color.profile import ColorProfile, ColorProfileDetection
from aidirector.media.metadata import MediaMetadata
from aidirector.memory.models import AssetRecord, SegmentRecord, TechnicalFeatures


def make_asset(project_id: str, name: str = "clip.mp4") -> AssetRecord:
    return AssetRecord(
        id=f"ast_{name.replace('.', '_')}",
        project_id=project_id,
        path=f"/footage/{name}",
        file_name=name,
        kind="video",
        size=1000,
        mtime=1723600000.0,
        partial_hash=f"hash_{name}",
        duration=12.5,
        metadata=MediaMetadata(duration=12.5, camera_make="DJI", audio_stream_count=1),
    )


def test_project_idempotent(memory):
    p1 = memory.get_or_create_project("trip", Path("/footage"))
    p2 = memory.get_or_create_project("trip", Path("/footage"))
    assert p1.id == p2.id


def test_asset_roundtrip(memory):
    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)

    loaded = memory.get_asset(asset.id)
    assert loaded is not None
    assert loaded.metadata.camera_make == "DJI"
    assert loaded.duration == 12.5

    found = memory.find_asset_by_identity(project.id, "hash_clip.mp4", 1000)
    assert found is not None and found.id == asset.id


def test_color_profile_roundtrip(memory):
    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)
    memory.save_color_profile(
        asset.id,
        ColorProfileDetection(profile=ColorProfile.DJI_DLOG2, confidence=0.94, source="auto"),
    )
    detection = memory.get_color_profile(asset.id)
    assert detection.profile == ColorProfile.DJI_DLOG2
    assert detection.confidence == 0.94


def test_segments_and_features(memory):
    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)
    segments = [
        SegmentRecord(id="seg_aaa", asset_id=asset.id, idx=0, start=0.0, end=6.0,
                      boundary_reasons=["recording_boundary"]),
        SegmentRecord(id="seg_bbb", asset_id=asset.id, idx=1, start=6.0, end=12.5,
                      boundary_reasons=["hard_cut"]),
    ]
    memory.replace_segments(asset.id, segments)
    assert [s.id for s in memory.list_segments(asset.id)] == ["seg_aaa", "seg_bbb"]
    assert len(memory.list_project_segments(project.id)) == 2

    features = TechnicalFeatures(mean_luma=120.0, quality_flags=["soft_focus"])
    memory.save_technical_features("seg_aaa", features)
    assert memory.get_technical_features("seg_aaa").quality_flags == ["soft_focus"]

    # replace_segments cleans dependents
    memory.replace_segments(asset.id, segments)
    assert memory.get_technical_features("seg_aaa") is None


def test_transcript_and_annotation(memory):
    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)
    transcript = Transcript(
        language="ja", duration=12.5,
        segments=[TranscriptSegment(start=0.0, end=2.0, text="こんにちは")],
    )
    provenance = Provenance(provider="mock", model="mock")
    memory.save_transcript(asset.id, transcript, provenance)
    assert memory.get_transcript(asset.id).segments[0].text == "こんにちは"

    memory.replace_segments(
        asset.id,
        [SegmentRecord(id="seg_ccc", asset_id=asset.id, idx=0, start=0.0, end=5.0)],
    )
    memory.save_semantic_annotation(
        "seg_ccc", VisionAnalysis(description="a street"), provenance
    )
    assert memory.get_semantic_annotation("seg_ccc").description == "a street"


def test_embeddings_roundtrip(memory):
    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)
    memory.replace_segments(
        asset.id,
        [SegmentRecord(id="seg_emb", asset_id=asset.id, idx=0, start=0.0, end=5.0)],
    )
    vector = [0.1, -0.2, 0.3]
    memory.save_embedding("segment", "seg_emb", "text", "mock", vector)
    loaded = memory.get_embedding("segment", "seg_emb", "text", "mock")
    assert loaded is not None
    assert all(abs(a - b) < 1e-6 for a, b in zip(loaded, vector))
    assert memory.iter_segment_embeddings(project.id)[0][0] == "seg_emb"


def test_edit_plan_and_feedback(memory):
    project = memory.get_or_create_project("trip", Path("/footage"))
    run_id = memory.create_director_run(project.id, {"target_duration": 60})
    plan_json = '{"version": 1, "clips": [{"segment_id": "seg_x"}]}'
    plan_id = memory.save_edit_plan(run_id, plan_json)
    assert memory.get_edit_plan(plan_id) == plan_json
    latest = memory.latest_edit_plan(project.id)
    assert latest is not None and latest[0] == plan_id
    memory.add_user_feedback(plan_id, "reject", decision_idx=0, reason="too long")
    memory.finish_director_run(run_id, "done")
