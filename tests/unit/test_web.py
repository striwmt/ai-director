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
             "caption": {"text": "駅", "secondary": "09:12", "duration": 3.0}},
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
