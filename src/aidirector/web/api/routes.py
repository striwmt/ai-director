"""HTTP API for the review/edit UI (AGENT.md §65).

The UI edits Edit Plans — never raw media. Every save creates a new,
validated plan version; user actions are recorded as feedback for future
director context (§63, Phase 8).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ...config import AppConfig
from ...director.schemas import EditClip, EditPlan
from ...errors import ValidationError
from ...logging import get_logger
from ...memory.database import connect
from ...memory.repository import MediaMemory
from ...perception.interpretation import build_understanding
from ...timeline.compiler import compile_timeline
from ...timeline.preview import render_preview
from ...timeline.validate import validate_edit_plan
from ..jobs import PipelineJob, RenderJobs

log = get_logger("web.api")

router = APIRouter(prefix="/api")
jobs = RenderJobs()
create_job = PipelineJob()


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_memory(request: Request) -> MediaMemory:
    # One connection per request; SQLite migrations are idempotent.
    conn = connect(request.app.state.config.paths.db_path)
    try:
        yield MediaMemory(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Read


@router.get("/state")
def state(memory: MediaMemory = Depends(get_memory)) -> dict:
    rows = memory.conn.execute(
        "SELECT id, name, root_dir FROM projects ORDER BY created_at DESC"
    ).fetchall()
    return {"projects": [dict(r) for r in rows]}


@router.get("/projects/{project_id}/plans")
def list_plans(project_id: str, memory: MediaMemory = Depends(get_memory)) -> dict:
    rows = memory.conn.execute(
        """
        SELECT p.id, p.version, p.created_at, r.intent_json
        FROM edit_plans p JOIN director_runs r ON r.id = p.run_id
        WHERE r.project_id = ?
        ORDER BY p.created_at DESC, p.rowid DESC
        """,
        (project_id,),
    ).fetchall()
    return {
        "plans": [
            {
                "id": r["id"],
                "version": r["version"],
                "created_at": r["created_at"],
                "intent": json.loads(r["intent_json"]),
            }
            for r in rows
        ]
    }


def _segment_info(memory: MediaMemory, segment_id: str) -> dict | None:
    segment = memory.get_segment(segment_id)
    if segment is None:
        return None
    u = build_understanding(segment, memory)
    return {
        "segment_id": segment.id,
        "asset_name": u.asset_name,
        "seg_start": segment.start,
        "seg_end": segment.end,
        "description": u.description,
        "transcript": u.transcript,
        "mood": u.mood,
        "quality_flags": u.quality_flags,
        "recorded_at": u.recorded_at,
        "orientation": u.orientation,
        "thumb": f"/api/segments/{segment.id}/thumb.jpg",
    }


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, memory: MediaMemory = Depends(get_memory)) -> dict:
    plan_json = memory.get_edit_plan(plan_id)
    if plan_json is None:
        raise HTTPException(404, f"plan not found: {plan_id}")
    plan = EditPlan.model_validate_json(plan_json)
    clips = []
    for clip in plan.clips:
        info = _segment_info(memory, clip.segment_id)
        clips.append({"clip": clip.model_dump(), "segment": info})
    return {
        "id": plan_id,
        "intent": plan.intent.model_dump(),
        "story": plan.story.model_dump(),
        "version": plan.version,
        "clips": clips,
        "render": jobs.status(plan_id),
    }


@router.get("/projects/{project_id}/segments")
def list_segments(project_id: str, memory: MediaMemory = Depends(get_memory)) -> dict:
    result = []
    for segment in memory.list_project_segments(project_id):
        info = _segment_info(memory, segment.id)
        if info:
            result.append(info)
    return {"segments": result}


@router.get("/segments/{segment_id}/thumb.jpg")
def segment_thumb(segment_id: str, memory: MediaMemory = Depends(get_memory)):
    frames = memory.list_frames(segment_id)
    for frame in frames:
        path = Path(frame.path)
        if path.is_file():
            return FileResponse(path, media_type="image/jpeg")
    raise HTTPException(404, "no frame available")


@router.get("/segments/{segment_id}/frame.jpg")
def segment_frame(
    segment_id: str,
    t: float,
    config: AppConfig = Depends(get_config),
    memory: MediaMemory = Depends(get_memory),
):
    """Frame at an arbitrary source time — filmstrip tiles and scrub preview.

    Extracted from the analysis proxy on demand and cached on disk
    (quantized to 0.2s so a drag doesn't spawn hundreds of extractions).
    """
    from ...process import run_command

    segment = memory.get_segment(segment_id)
    if segment is None:
        raise HTTPException(404, f"segment not found: {segment_id}")
    asset = memory.get_asset(segment.asset_id)
    if asset is None:
        raise HTTPException(404, "asset not found")

    limit = asset.duration if asset.duration else segment.end
    t = min(max(t, 0.0), max(0.0, limit - 0.05))
    quantized = round(t * 5) / 5  # 0.2s buckets

    cache_dir = config.paths.cache_dir / "scrub" / asset.id
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{quantized:.1f}.jpg"
    if not out.is_file():
        source = memory.get_analysis_proxy(asset.id) or asset.path
        if not Path(source).is_file():
            raise HTTPException(404, "media not available")
        try:
            run_command(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{quantized:.3f}", "-i", str(source),
                    "-frames:v", "1", "-vf", "scale=-2:180", "-q:v", "5",
                    str(out),
                ],
                timeout=30.0,
            )
        except Exception as exc:
            raise HTTPException(500, f"frame extraction failed: {exc}") from exc
    if not out.is_file():
        raise HTTPException(404, "no frame at requested time")
    return FileResponse(out, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Write


class FeedbackItem(BaseModel):
    action: str  # accept | reject | trim | extend | shorten | reorder | replace
    decision_idx: int | None = None
    reason: str | None = None


class SavePlanRequest(BaseModel):
    clips: list[EditClip] = Field(min_length=1)
    feedback: list[FeedbackItem] = Field(default_factory=list)


@router.post("/plans/{plan_id}/save")
def save_plan(
    plan_id: str,
    body: SavePlanRequest,
    memory: MediaMemory = Depends(get_memory),
) -> dict:
    source_json = memory.get_edit_plan(plan_id)
    if source_json is None:
        raise HTTPException(404, f"plan not found: {plan_id}")
    source = EditPlan.model_validate_json(source_json)

    new_plan = EditPlan(
        version=source.version + 1,
        intent=source.intent,
        story=source.story,
        clips=body.clips,
    )
    try:
        validate_edit_plan(new_plan, memory)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc

    row = memory.conn.execute(
        "SELECT run_id FROM edit_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    new_id = memory.save_edit_plan(
        row["run_id"], new_plan.model_dump_json(), version=new_plan.version
    )
    for item in body.feedback:
        memory.add_user_feedback(
            plan_id, item.action, decision_idx=item.decision_idx, reason=item.reason
        )
    log.info(
        "plan %s saved as %s (v%d, %d clips, %d feedback items)",
        plan_id, new_id, new_plan.version, len(new_plan.clips), len(body.feedback),
    )
    return {"plan_id": new_id, "version": new_plan.version}


# ---------------------------------------------------------------------------
# Creation (footage -> analyze -> director -> preview)


@router.get("/footage/validate")
def validate_footage(
    path: str,
    config: AppConfig = Depends(get_config),
    memory: MediaMemory = Depends(get_memory),
) -> dict:
    from ...media.ingest import scan_directory

    directory = Path(path).expanduser()
    if not directory.is_dir():
        return {"exists": False, "video_count": 0, "files": [], "known_project": None}
    found = scan_directory(directory, config)
    row = memory.conn.execute(
        "SELECT id, name FROM projects WHERE root_dir = ?",
        (str(directory.resolve()),),
    ).fetchone()
    return {
        "exists": True,
        "video_count": len(found["video"]),
        "files": [p.name for p in found["video"][:20]],
        "known_project": dict(row) if row else None,
    }


@router.get("/profiles")
def list_profiles(config: AppConfig = Depends(get_config)) -> dict:
    from ...director.profile import load_director_profile

    profiles = []
    profiles_dir = config.director.profiles_dir
    if profiles_dir.is_dir():
        for path in sorted(profiles_dir.glob("*.yaml")):
            try:
                profile = load_director_profile(profiles_dir, path.stem)
                profiles.append({"name": profile.name, "description": profile.description})
            except Exception as exc:
                log.warning("skipping profile %s: %s", path.name, exc)
    return {"profiles": profiles, "default": config.director.default_profile}


class CreateRequest(BaseModel):
    footage_path: str
    prompt: str = ""
    duration: float = Field(default=60.0, gt=0, le=3600)
    profile: str | None = None
    captions: str = "none"
    caption_format: str | None = None
    subtitles: bool = False
    canvas: str | None = None


@router.post("/create")
def start_create(
    body: CreateRequest,
    config: AppConfig = Depends(get_config),
) -> dict:
    footage = Path(body.footage_path).expanduser()
    if not footage.is_dir():
        raise HTTPException(422, f"素材ディレクトリが見つかりません: {footage}")

    def work(progress) -> str:
        import asyncio

        from ...ai.runtime import ModelRuntimeManager
        from ...ai.services import AIServices
        from ...pipeline import run_full_edit

        conn = connect(config.paths.db_path)
        try:
            job_memory = MediaMemory(conn)
            ai = AIServices(ModelRuntimeManager(config.models))
            plan_id, _preview = asyncio.run(
                run_full_edit(
                    footage, config, job_memory, ai, Path.cwd(),
                    prompt=body.prompt, duration=body.duration,
                    profile=body.profile, captions=body.captions,
                    caption_format=body.caption_format,
                    subtitles=body.subtitles or None, canvas=body.canvas,
                    progress=progress,
                )
            )
            return plan_id
        finally:
            conn.close()

    if not create_job.start(body.model_dump(), work):
        raise HTTPException(409, "別の作成ジョブが実行中です")
    return create_job.status()


@router.get("/create/status")
def create_status() -> dict:
    return create_job.status()


# ---------------------------------------------------------------------------
# Render


@router.post("/plans/{plan_id}/render")
def start_render(
    plan_id: str,
    config: AppConfig = Depends(get_config),
    memory: MediaMemory = Depends(get_memory),
) -> dict:
    if memory.get_edit_plan(plan_id) is None:
        raise HTTPException(404, f"plan not found: {plan_id}")

    def work() -> str:
        conn = connect(config.paths.db_path)
        try:
            job_memory = MediaMemory(conn)
            plan = EditPlan.model_validate_json(job_memory.get_edit_plan(plan_id))
            timeline = compile_timeline(plan, job_memory, canvas=config.output.canvas)
            output = render_preview(
                timeline, config, config.paths.renders_dir / f"{plan_id}.mp4"
            )
            return str(output)
        finally:
            conn.close()

    return jobs.start(plan_id, work)


@router.get("/plans/{plan_id}/render/status")
def render_status(plan_id: str) -> dict:
    return jobs.status(plan_id)


@router.api_route("/plans/{plan_id}/preview.mp4", methods=["GET", "HEAD"])
def preview_file(plan_id: str, config: AppConfig = Depends(get_config)):
    # plan_id comes from the DB namespace; still keep the path strictly inside
    # the renders dir.
    safe = "".join(ch for ch in plan_id if ch.isalnum() or ch == "_")
    path = config.paths.renders_dir / f"{safe}.mp4"
    if not path.is_file():
        raise HTTPException(404, "no render yet")
    return FileResponse(path, media_type="video/mp4")
