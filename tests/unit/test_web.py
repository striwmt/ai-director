"""Web UI API tests (FastAPI TestClient)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from aidirector.memory.models import SegmentRecord  # noqa: E402
from aidirector.web.app import create_app  # noqa: E402
from tests.unit.test_memory import make_asset  # noqa: E402


@pytest.fixture()
def populated(config, memory, tmp_path):
    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)
    memory.replace_segments(
        asset.id,
        [
            SegmentRecord(id="seg_a", asset_id=asset.id, idx=0, start=0.0, end=6.0),
            SegmentRecord(id="seg_b", asset_id=asset.id, idx=1, start=6.0, end=12.5),
        ],
    )
    # thumbnail file for seg_a
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(bytes.fromhex("ffd8ffdb") + b"\x00" * 16)  # fake jpg
    from aidirector.memory.models import FrameRecord

    memory.add_frames([FrameRecord(segment_id="seg_a", timestamp=1.0, path=str(thumb))])

    run_id = memory.create_director_run(project.id, {"target_duration": 20})
    plan_json = json.dumps({
        "version": 1,
        "intent": {"target_duration": 20, "profile": "travel_vlog", "user_prompt": "t"},
        "story": {"concept": "c", "tone": "calm"},
        "clips": [
            {"segment_id": "seg_a", "source_in": 1.0, "source_out": 5.0,
             "story_beat": "hook", "reason": "r1"},
            {"segment_id": "seg_b", "source_in": 6.0, "source_out": 10.0,
             "story_beat": "main", "reason": "r2",
             "caption": {"text": "駅", "secondary": "09:12", "duration": 3.0},
             "subtitles": [{"start": 7.0, "end": 9.0, "text": "こんにちは"}]},
        ],
    })
    plan_id = memory.save_edit_plan(run_id, plan_json)
    return {"project_id": project.id, "plan_id": plan_id}


@pytest.fixture()
def client(config):
    return TestClient(create_app(config))


def test_state_and_plans(client, populated):
    state = client.get("/api/state").json()
    assert state["projects"][0]["id"] == populated["project_id"]

    plans = client.get(f"/api/projects/{populated['project_id']}/plans").json()
    assert plans["plans"][0]["id"] == populated["plan_id"]
    assert plans["plans"][0]["intent"]["target_duration"] == 20


def test_get_plan_enriched(client, populated):
    plan = client.get(f"/api/plans/{populated['plan_id']}").json()
    assert len(plan["clips"]) == 2
    first = plan["clips"][0]
    assert first["segment"]["seg_end"] == 6.0
    assert first["segment"]["thumb"].endswith("/thumb.jpg")
    assert plan["clips"][1]["clip"]["caption"]["text"] == "駅"


def test_segments_and_thumb(client, populated):
    segs = client.get(f"/api/projects/{populated['project_id']}/segments").json()
    assert {s["segment_id"] for s in segs["segments"]} == {"seg_a", "seg_b"}
    ok = client.get("/api/segments/seg_a/thumb.jpg")
    assert ok.status_code == 200
    missing = client.get("/api/segments/seg_b/thumb.jpg")
    assert missing.status_code == 404


def test_segment_video_endpoint(client, populated, memory, tmp_path):
    # No proxy and the original path doesn't exist -> 404.
    missing = client.get("/api/segments/seg_a/video.mp4")
    assert missing.status_code == 404

    # With an analysis proxy on disk the segment becomes playable.
    proxy = tmp_path / "proxy.mp4"
    proxy.write_bytes(b"\x00" * 64)
    segment = memory.get_segment("seg_a")
    memory.save_color_transform(
        segment.asset_id, "analysis", None, None, True, str(proxy)
    )
    ok = client.get("/api/segments/seg_a/video.mp4")
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "video/mp4"

    # The segment listing links to the video.
    segs = client.get(f"/api/projects/{populated['project_id']}/segments").json()
    seg_a = next(s for s in segs["segments"] if s["segment_id"] == "seg_a")
    assert seg_a["video"] == "/api/segments/seg_a/video.mp4"


def test_segment_understanding_endpoint(client, populated, memory):
    from aidirector.ai.schemas import Provenance, VisionAnalysis

    memory.save_semantic_annotation(
        "seg_a",
        VisionAnalysis(description="駅に電車が到着する", mood=["calm"]),
        Provenance(provider="transformers", model="Qwen/Qwen3-VL-4B-Instruct",
                   prompt_version="vision-v1"),
    )
    memory.save_embedding("segment", "seg_a", "text", "test-embed", [0.1, 0.2])
    info = client.get("/api/segments/seg_a/understanding").json()
    assert info["understanding"]["description"] == "駅に電車が到着する"
    assert info["understanding"]["mood"] == ["calm"]
    assert info["vision_provenance"]["model"] == "Qwen/Qwen3-VL-4B-Instruct"
    assert info["embedding_models"] == ["test-embed"]
    assert client.get("/api/segments/seg_nope/understanding").status_code == 404


def test_asset_metadata_endpoint(client, populated, memory):
    segs = client.get(f"/api/projects/{populated['project_id']}/segments").json()
    asset_id = segs["segments"][0]["asset_id"]
    info = client.get(f"/api/assets/{asset_id}/metadata").json()
    assert info["file_name"] == "clip.mp4"
    assert info["metadata"]["camera_make"] == "DJI"
    assert "raw_tags" in info["metadata"]
    assert info["recording_start_local"] is None  # fixture has no creation_time
    assert client.get("/api/assets/ast_nope/metadata").status_code == 404


def test_save_creates_new_version(client, populated):
    plan = client.get(f"/api/plans/{populated['plan_id']}").json()
    clips = [c["clip"] for c in plan["clips"]]
    clips.reverse()
    clips[0]["source_out"] = 9.0  # shorten
    res = client.post(
        f"/api/plans/{populated['plan_id']}/save",
        json={"clips": clips, "feedback": [
            {"action": "reorder", "decision_idx": 0},
            {"action": "shorten", "decision_idx": 0, "reason": "tighter"},
        ]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["version"] == 2
    assert body["plan_id"] != populated["plan_id"]

    saved = client.get(f"/api/plans/{body['plan_id']}").json()
    assert saved["clips"][0]["clip"]["segment_id"] == "seg_b"
    assert saved["version"] == 2


def test_save_rejects_invalid_plan(client, populated):
    res = client.post(
        f"/api/plans/{populated['plan_id']}/save",
        json={"clips": [
            {"segment_id": "seg_nope", "source_in": 0.0, "source_out": 2.0,
             "story_beat": "x", "reason": ""},
        ]},
    )
    assert res.status_code == 422
    assert "does not exist" in res.json()["detail"]


def test_render_job_flow(client, populated, monkeypatch):
    # Render fast: patch the renderer to write a stub file.
    from aidirector.web.api import routes

    def fake_render(timeline, config, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"\x00" * 32)
        return output

    monkeypatch.setattr(routes, "render_preview", fake_render)
    started = client.post(f"/api/plans/{populated['plan_id']}/render")
    assert started.status_code == 200

    import time

    for _ in range(50):
        status = client.get(f"/api/plans/{populated['plan_id']}/render/status").json()
        if status["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert status["status"] == "done", status

    video = client.get(f"/api/plans/{populated['plan_id']}/preview.mp4")
    assert video.status_code == 200


def test_scrub_frame_endpoint(client, populated, memory, tmp_path):
    from aidirector.process import tool_available

    if not tool_available("ffmpeg"):
        pytest.skip("ffmpeg required")
    import subprocess

    proxy = tmp_path / "proxy.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=duration=13:size=320x180:rate=10",
         "-pix_fmt", "yuv420p", str(proxy)],
        check=True, timeout=60,
    )
    seg = memory.get_segment("seg_a")
    memory.save_color_transform(seg.asset_id, "analysis", None, None, False, str(proxy))

    res = client.get("/api/segments/seg_a/frame.jpg?t=1.5")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "image/jpeg"
    assert len(res.content) > 500

    # cached second hit + out-of-range time clamped, unknown segment 404
    assert client.get("/api/segments/seg_a/frame.jpg?t=1.5").status_code == 200
    assert client.get("/api/segments/seg_a/frame.jpg?t=99").status_code == 200
    assert client.get("/api/segments/seg_zz/frame.jpg?t=1").status_code == 404


def test_scrub_frame_missing_media(client, populated):
    # populated asset points at a nonexistent path and has no proxy
    res = client.get("/api/segments/seg_b/frame.jpg?t=7.0")
    assert res.status_code == 404


def test_export_endpoints(client, populated):
    plan_id = populated["plan_id"]

    fcpxml = client.get(f"/api/plans/{plan_id}/export/fcpxml")
    assert fcpxml.status_code == 200
    assert "attachment" in fcpxml.headers["content-disposition"]
    assert f"aidirector_{plan_id}.fcpxml" in fcpxml.headers["content-disposition"]
    assert "clip.mp4" in fcpxml.text          # original media reference
    assert "駅" in fcpxml.text                 # caption title carried over

    srt = client.get(f"/api/plans/{plan_id}/export/srt")
    assert srt.status_code == 200
    # subtitle at source 7.0 in a clip starting source 6.0 / timeline 4.0 -> 5.0
    assert "00:00:05,000 --> 00:00:07,000" in srt.text
    assert "こんにちは" in srt.text

    assert client.get(f"/api/plans/{plan_id}/export/otio").status_code == 200
    edl = client.get(f"/api/plans/{plan_id}/export/edl")
    assert edl.status_code == 200 and "FROM CLIP NAME" in edl.text

    assert client.get(f"/api/plans/{plan_id}/export/mov").status_code == 422
    assert client.get("/api/plans/plan_nope/export/fcpxml").status_code == 404


def test_projects_gallery_and_thumb(client, populated):
    res = client.get("/api/projects").json()
    project = res["projects"][0]
    assert project["id"] == populated["project_id"]
    assert project["video_count"] == 1
    assert project["total_duration"] == 12.5
    assert project["plan_count"] == 1
    assert project["thumb"].endswith("/thumb.jpg")

    thumb = client.get(project["thumb"])
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"

    assert client.get("/api/projects/prj_nope/thumb.jpg").status_code == 404


def test_browse(client, tmp_path):
    (tmp_path / "trip" / "day1").mkdir(parents=True)
    (tmp_path / "trip" / "day2").mkdir()
    (tmp_path / "trip" / ".hidden").mkdir()
    (tmp_path / "trip" / "clip.MP4").write_bytes(b"")

    (tmp_path / "trip" / "theme.mp3").write_bytes(b"")

    res = client.get(f"/api/browse?path={tmp_path}/trip").json()
    assert res["path"].endswith("/trip")
    assert [d["name"] for d in res["dirs"]] == ["day1", "day2"]
    assert res["video_count"] == 1
    assert res["audio_count"] == 1
    assert res["parent"] == str(tmp_path)

    assert client.get(f"/api/browse?path={tmp_path}/nope").status_code == 404
    # empty path falls back to home
    assert client.get("/api/browse").status_code == 200


def test_rename_project_and_plan(client, populated):
    res = client.patch(
        f"/api/projects/{populated['project_id']}", json={"name": "佐原の旅"}
    )
    assert res.status_code == 200
    state = client.get("/api/state").json()
    assert state["projects"][0]["name"] == "佐原の旅"

    res = client.patch(
        f"/api/plans/{populated['plan_id']}", json={"name": "雨の町 最終案"}
    )
    assert res.status_code == 200
    plans = client.get(f"/api/projects/{populated['project_id']}/plans").json()
    assert plans["plans"][0]["name"] == "雨の町 最終案"
    plan = client.get(f"/api/plans/{populated['plan_id']}").json()
    assert plan["name"] == "雨の町 最終案"

    # renaming survives save-as-new-version
    clips = [c["clip"] for c in plan["clips"]]
    saved = client.post(
        f"/api/plans/{populated['plan_id']}/save", json={"clips": clips}
    ).json()
    plans = client.get(f"/api/projects/{populated['project_id']}/plans").json()
    v2 = next(p for p in plans["plans"] if p["id"] == saved["plan_id"])
    assert v2["name"] == "雨の町 最終案"

    assert client.patch("/api/projects/prj_nope", json={"name": "x"}).status_code == 404
    assert client.patch("/api/plans/plan_nope", json={"name": "x"}).status_code == 404


def test_music_roundtrip_via_save(client, populated, tmp_path):
    track = tmp_path / "calm.wav"
    track.write_bytes(b"RIFF")

    plan = client.get(f"/api/plans/{populated['plan_id']}").json()
    assert plan["music"] is None
    clips = [c["clip"] for c in plan["clips"]]

    # attach music
    music = {"path": str(track), "file_name": "calm.wav",
             "gain_db": -20.0, "reason": "test pick"}
    res = client.post(
        f"/api/plans/{populated['plan_id']}/save",
        json={"clips": clips, "music": music},
    )
    assert res.status_code == 200, res.text
    v2_id = res.json()["plan_id"]
    v2 = client.get(f"/api/plans/{v2_id}").json()
    assert v2["music"]["file_name"] == "calm.wav"
    assert v2["music"]["gain_db"] == -20.0
    assert v2["music"]["enabled"] is True

    # omitting music preserves it (old clients must not drop the track)
    res = client.post(f"/api/plans/{v2_id}/save", json={"clips": clips})
    assert res.status_code == 200, res.text
    v3 = client.get(f"/api/plans/{res.json()['plan_id']}").json()
    assert v3["music"]["file_name"] == "calm.wav"

    # explicit null removes it
    res = client.post(
        f"/api/plans/{v2_id}/save", json={"clips": clips, "music": None}
    )
    assert res.status_code == 200, res.text
    v4 = client.get(f"/api/plans/{res.json()['plan_id']}").json()
    assert v4["music"] is None


def test_music_library_listing(client, memory, music_dir, tmp_path):
    from aidirector.memory.models import MusicTrackRecord
    from aidirector.perception.music import music_track_id

    # One analyzed row for calm_theme.wav; upbeat stays unanalyzed.
    calm = music_dir / "calm_theme.wav"
    memory.save_music_track(MusicTrackRecord(
        id=music_track_id(calm), path=str(calm), file_name=calm.name,
        duration=10.0,
        features={"bpm": 90.0, "key": "A", "scale": "minor", "energy": "low"},
        tags=[{"tag": "ambient", "category": "genre", "score": 0.4}],
        lyrics={"language": "en", "is_vocal": False},
        description="soft pads", analyzed_at="2026-08-15T00:00:00",
    ))

    res = client.get(f"/api/music/tracks?path={music_dir}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["analyzed_count"] == 1
    by_name = {t["file_name"]: t for t in body["tracks"]}
    assert set(by_name) == {"calm_theme.wav", "upbeat_energy.wav"}
    analyzed = by_name["calm_theme.wav"]
    assert analyzed["analyzed"] and analyzed["bpm"] == 90.0
    assert analyzed["key"] == "A minor" and analyzed["tags"] == ["ambient"]
    assert analyzed["is_vocal"] is False and analyzed["description"] == "soft pads"
    assert by_name["upbeat_energy.wav"]["analyzed"] is False

    assert client.get(f"/api/music/tracks?path={tmp_path}/nope").status_code == 422


def test_music_analyze_job_flow(client, music_dir, monkeypatch):
    import time

    import aidirector.perception.music as music_mod

    async def fake_analyze(music_dir_arg, config, memory, ai, progress=None):
        return 2

    monkeypatch.setattr(music_mod, "analyze_music_library", fake_analyze)
    res = client.post("/api/music/analyze", json={"path": str(music_dir)})
    assert res.status_code == 200, res.text
    for _ in range(50):
        st = client.get("/api/music/analyze/status").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st["status"] == "done", st

    assert client.post(
        "/api/music/analyze", json={"path": "/no/such/dir"}
    ).status_code == 422


def test_reanalyze_job_flow(client, memory, tmp_path, monkeypatch):
    import time

    import aidirector.pipeline as pipeline_mod

    footage = tmp_path / "clips"
    footage.mkdir()
    project = memory.get_or_create_project("re", footage)

    called = {}

    async def fake_analyze(footage_arg, config, mem, ai, root, **kwargs):
        called["reanalyze"] = kwargs.get("reanalyze")
        return project.id

    monkeypatch.setattr(pipeline_mod, "run_analyze", fake_analyze)

    res = client.post(f"/api/projects/{project.id}/reanalyze")
    assert res.status_code == 200, res.text
    for _ in range(100):
        st = client.get("/api/create/status").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st["status"] == "done", st
    assert st["plan_id"] == ""  # analysis only — no plan produced
    assert called["reanalyze"] is True

    assert client.post("/api/projects/prj_nope/reanalyze").status_code == 404


def test_create_rejects_missing_music_dir(client, tmp_path):
    footage = tmp_path / "f"
    footage.mkdir()
    res = client.post("/api/create", json={
        "footage_path": str(footage), "prompt": "x",
        "music_path": str(tmp_path / "no_such_music"),
    })
    assert res.status_code == 422


def test_create_accepts_project_name(client, tmp_path, monkeypatch):
    import time

    import aidirector.pipeline as pipeline_mod

    footage = tmp_path / "f"
    footage.mkdir()
    seen = {}

    async def fake_full_edit(*args, **kwargs):
        seen["project_name"] = kwargs.get("project_name")
        return "plan_named", None

    monkeypatch.setattr(pipeline_mod, "run_full_edit", fake_full_edit)
    res = client.post("/api/create", json={
        "footage_path": str(footage), "prompt": "x", "project_name": "  夏の旅  ",
    })
    assert res.status_code == 200
    for _ in range(50):
        if client.get("/api/create/status").json()["status"] == "done":
            break
        time.sleep(0.05)
    assert seen["project_name"] == "夏の旅"


def test_footage_validate(client, tmp_path):
    footage = tmp_path / "clips"
    footage.mkdir()
    (footage / "A.MP4").write_bytes(b"")
    (footage / "B.mov").write_bytes(b"")
    (footage / "B.LRF").write_bytes(b"")
    (footage / "notes.txt").write_bytes(b"")

    res = client.get(f"/api/footage/validate?path={footage}").json()
    assert res["exists"] is True
    assert res["video_count"] == 2
    assert "A.MP4" in res["files"]
    assert res["known_project"] is None

    res = client.get(f"/api/footage/validate?path={tmp_path}/nope").json()
    assert res["exists"] is False

    res = client.get(f"/api/footage/validate?path={footage}/A.MP4").json()
    assert res["exists"] is False


def test_profiles_listing(client):
    res = client.get("/api/profiles").json()
    names = {p["name"] for p in res["profiles"]}
    assert {"travel_vlog", "cinematic_travel", "talk"} <= names
    assert res["default"] == "travel_vlog"


def test_create_job_flow(client, tmp_path, monkeypatch):
    import time

    import aidirector.pipeline as pipeline_mod

    footage = tmp_path / "footage"
    footage.mkdir()
    (footage / "X.MP4").write_bytes(b"")

    async def fake_full_edit(*args, **kwargs):
        assert kwargs["prompt"] == "テスト"
        assert kwargs["duration"] == 30.0
        kwargs["progress"]("director")
        time.sleep(0.3)
        return "plan_created_x", None

    monkeypatch.setattr(pipeline_mod, "run_full_edit", fake_full_edit)

    missing = client.post("/api/create", json={"footage_path": str(tmp_path / "zz")})
    assert missing.status_code == 422

    started = client.post("/api/create", json={
        "footage_path": str(footage), "prompt": "テスト", "duration": 30,
    })
    assert started.status_code == 200
    assert started.json()["status"] == "running"

    # busy: a second create is rejected while the first runs
    busy = client.post("/api/create", json={
        "footage_path": str(footage), "prompt": "x", "duration": 10,
    })
    assert busy.status_code == 409

    for _ in range(60):
        st = client.get("/api/create/status").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st["status"] == "done", st
    assert st["plan_id"] == "plan_created_x"
    assert st["phase"] == "done"


def test_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "AI" in res.text and "タイムライン" in res.text


def test_favicon_served(client):
    res = client.get("/favicon.png")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content.startswith(b"\x89PNG")
