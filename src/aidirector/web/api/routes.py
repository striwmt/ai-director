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
from ...director.schemas import EditClip, EditPlan, PlanMusic
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
music_job = PipelineJob()  # music-library analysis (GPU: one at a time)


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


@router.get("/projects")
def list_projects(memory: MediaMemory = Depends(get_memory)) -> dict:
    """Project gallery data for the home screen."""
    rows = memory.conn.execute(
        """
        SELECT p.id, p.name, p.root_dir, p.created_at,
            (SELECT COUNT(*) FROM assets a
             WHERE a.project_id = p.id AND a.kind = 'video') AS video_count,
            (SELECT COALESCE(SUM(a.duration), 0) FROM assets a
             WHERE a.project_id = p.id AND a.kind = 'video') AS total_duration,
            (SELECT COUNT(*) FROM edit_plans ep
             JOIN director_runs r ON r.id = ep.run_id
             WHERE r.project_id = p.id) AS plan_count
        FROM projects p
        ORDER BY p.created_at DESC
        """
    ).fetchall()
    return {
        "projects": [
            {**dict(r), "thumb": f"/api/projects/{r['id']}/thumb.jpg"}
            for r in rows
        ]
    }


@router.get("/projects/{project_id}/thumb.jpg")
def project_thumb(project_id: str, memory: MediaMemory = Depends(get_memory)):
    rows = memory.conn.execute(
        """
        SELECT f.path FROM frames f
        JOIN segments s ON s.id = f.segment_id
        JOIN assets a ON a.id = s.asset_id
        WHERE a.project_id = ?
        ORDER BY a.file_name, s.idx, f.timestamp
        LIMIT 20
        """,
        (project_id,),
    ).fetchall()
    for row in rows:
        path = Path(row["path"])
        if path.is_file():
            return FileResponse(path, media_type="image/jpeg")
    raise HTTPException(404, "no frame available")


@router.get("/projects/{project_id}/plans")
def list_plans(project_id: str, memory: MediaMemory = Depends(get_memory)) -> dict:
    rows = memory.conn.execute(
        """
        SELECT p.id, p.version, p.name, p.created_at, r.intent_json
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
                "name": r["name"],
                "created_at": r["created_at"],
                "intent": json.loads(r["intent_json"]),
            }
            for r in rows
        ]
    }


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.patch("/projects/{project_id}")
def rename_project(
    project_id: str,
    body: RenameRequest,
    memory: MediaMemory = Depends(get_memory),
) -> dict:
    if not memory.rename_project(project_id, body.name.strip()):
        raise HTTPException(404, f"project not found: {project_id}")
    return {"id": project_id, "name": body.name.strip()}


@router.patch("/plans/{plan_id}")
def rename_plan(
    plan_id: str,
    body: RenameRequest,
    memory: MediaMemory = Depends(get_memory),
) -> dict:
    if not memory.rename_plan(plan_id, body.name.strip()):
        raise HTTPException(404, f"plan not found: {plan_id}")
    return {"id": plan_id, "name": body.name.strip()}


def _segment_info(memory: MediaMemory, segment_id: str) -> dict | None:
    segment = memory.get_segment(segment_id)
    if segment is None:
        return None
    u = build_understanding(segment, memory)
    return {
        "segment_id": segment.id,
        "asset_id": segment.asset_id,
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
        "video": f"/api/segments/{segment.id}/video.mp4",
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
        "name": memory.get_plan_name(plan_id),
        "intent": plan.intent.model_dump(),
        "story": plan.story.model_dump(),
        "version": plan.version,
        "clips": clips,
        "music": plan.music.model_dump() if plan.music else None,
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


@router.get("/assets/{asset_id}/metadata")
def asset_metadata(
    asset_id: str,
    memory: MediaMemory = Depends(get_memory),
) -> dict:
    """Full media metadata for one source file: normalized fields plus
    every raw ffprobe tag verbatim, and the derived recording start
    (timecode-refined and local-time when trustworthy)."""
    asset = memory.get_asset(asset_id)
    if asset is None:
        raise HTTPException(404, f"asset not found: {asset_id}")
    from ...media.metadata import refined_creation_time
    from ...perception.interpretation import parse_creation_time, to_local_time

    refined = refined_creation_time(asset.metadata)
    start = refined or asset.metadata.creation_time
    local = to_local_time(parse_creation_time(start))
    return {
        "asset_id": asset.id,
        "file_name": asset.file_name,
        "path": asset.path,
        "size": asset.size,
        "duration": asset.duration,
        "status": asset.status,
        "metadata": asset.metadata.model_dump(),
        "recording_start_local": local.isoformat() if local else None,
        "timecode_trusted": refined is not None,
    }


@router.get("/segments/{segment_id}/understanding")
def segment_understanding(
    segment_id: str,
    memory: MediaMemory = Depends(get_memory),
) -> dict:
    """Everything the AI detected about one segment: the VLM's analysis
    (with the model that produced it), the transcript span, and the
    deterministic technical features."""
    segment = memory.get_segment(segment_id)
    if segment is None:
        raise HTTPException(404, f"segment not found: {segment_id}")
    u = build_understanding(segment, memory)
    row = memory.conn.execute(
        "SELECT provenance_json FROM semantic_annotations WHERE segment_id = ?",
        (segment_id,),
    ).fetchone()
    provenance = json.loads(row["provenance_json"]) if row else None
    embeddings = memory.conn.execute(
        "SELECT model FROM embeddings WHERE owner_type = 'segment' AND owner_id = ?",
        (segment_id,),
    ).fetchall()
    return {
        "understanding": u.model_dump(),
        "vision_provenance": provenance,
        "embedding_models": [r["model"] for r in embeddings],
    }


@router.api_route("/segments/{segment_id}/video.mp4", methods=["GET", "HEAD"])
def segment_video(
    segment_id: str,
    memory: MediaMemory = Depends(get_memory),
):
    """Browser-playable source video for a segment's asset.

    Serves the 540p analysis proxy when present (original codecs like
    H.265 often don't play in browsers); falls back to the original file.
    Range requests are handled by FileResponse, so seeking works.
    """
    segment = memory.get_segment(segment_id)
    if segment is None:
        raise HTTPException(404, f"segment not found: {segment_id}")
    proxy = memory.get_analysis_proxy(segment.asset_id)
    path = Path(proxy) if proxy else None
    if path is None or not path.is_file():
        asset = memory.get_asset(segment.asset_id)
        path = Path(asset.path) if asset else None
    if path is None or not path.is_file():
        raise HTTPException(404, "no playable file for this segment")
    return FileResponse(path, media_type="video/mp4")


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
    # Omitted -> keep the plan's current music; explicit null -> remove it.
    music: PlanMusic | None = None
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

    music = body.music if "music" in body.model_fields_set else source.music
    new_plan = EditPlan(
        version=source.version + 1,
        intent=source.intent,
        story=source.story,
        clips=body.clips,
        music=music,
    )
    try:
        validate_edit_plan(new_plan, memory)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc

    row = memory.conn.execute(
        "SELECT run_id, name FROM edit_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    new_id = memory.save_edit_plan(
        row["run_id"], new_plan.model_dump_json(),
        version=new_plan.version, name=row["name"],
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
# NLE export

_EXPORT_FORMATS = {
    "fcpxml": (".fcpxml", "application/xml"),
    "otio": (".otio", "application/json"),
    "edl": (".edl", "text/plain; charset=utf-8"),
    "srt": (".srt", "application/x-subrip"),
}


@router.api_route("/plans/{plan_id}/export/{format}", methods=["GET", "HEAD"])
def export_plan(
    plan_id: str,
    format: str,
    config: AppConfig = Depends(get_config),
    memory: MediaMemory = Depends(get_memory),
):
    """Download the plan for an NLE. References original camera media."""
    if format not in _EXPORT_FORMATS:
        raise HTTPException(
            422, f"format must be one of: {', '.join(_EXPORT_FORMATS)}"
        )
    plan_json = memory.get_edit_plan(plan_id)
    if plan_json is None:
        raise HTTPException(404, f"plan not found: {plan_id}")
    plan = EditPlan.model_validate_json(plan_json)
    timeline = compile_timeline(plan, memory, canvas=config.output.canvas)

    if format == "fcpxml":
        from ...timeline.fcpxml import export_fcpxml as exporter
    elif format == "otio":
        from ...timeline.otio import export_otio as exporter
    elif format == "edl":
        from ...timeline.edl import export_edl as exporter
    else:
        from ...timeline.srt import export_srt as exporter

    suffix, media_type = _EXPORT_FORMATS[format]
    path = exporter(timeline, config.paths.renders_dir / f"{plan_id}{suffix}")
    log.info("exported %s as %s", plan_id, format)
    return FileResponse(
        path, media_type=media_type, filename=f"aidirector_{plan_id}{suffix}"
    )


# ---------------------------------------------------------------------------
# Creation (footage -> analyze -> director -> preview)


@router.get("/browse")
def browse_directories(
    path: str = "",
    config: AppConfig = Depends(get_config),
) -> dict:
    """List subdirectories for the footage picker (local tool, read-only)."""
    directory = Path(path).expanduser() if path else Path.home()
    try:
        directory = directory.resolve()
    except OSError:
        raise HTTPException(422, f"invalid path: {path}")
    if not directory.is_dir():
        raise HTTPException(404, f"not a directory: {directory}")

    # Non-recursive on purpose: browsing must stay instant even in huge
    # trees (recursive counting is validate_footage's job on the final pick).
    video_extensions = set(config.ingest.video_extensions)
    audio_extensions = set(config.ingest.audio_extensions)
    subdirs = []
    video_count = 0
    audio_count = 0
    try:
        for entry in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                subdirs.append({"name": entry.name, "path": str(entry)})
            elif entry.suffix.lower() in video_extensions:
                video_count += 1
            elif entry.suffix.lower() in audio_extensions:
                audio_count += 1
    except PermissionError:
        raise HTTPException(403, f"permission denied: {directory}")
    parent = str(directory.parent) if directory.parent != directory else None
    return {
        "path": str(directory),
        "parent": parent,
        "dirs": subdirs[:500],
        "video_count": video_count,
        "audio_count": audio_count,
    }


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
    project_name: str | None = Field(default=None, max_length=120)
    prompt: str = ""
    duration: float = Field(default=60.0, gt=0, le=3600)
    profile: str | None = None
    captions: str = "none"
    caption_format: str | None = None
    subtitles: bool = False
    music_path: str | None = None
    canvas: str | None = None
    # Free text; parsed with parse_outline (one per line or , 、 → separators)
    flow: str | None = None


@router.post("/create")
def start_create(
    body: CreateRequest,
    config: AppConfig = Depends(get_config),
) -> dict:
    footage = Path(body.footage_path).expanduser()
    if not footage.is_dir():
        raise HTTPException(422, f"素材ディレクトリが見つかりません: {footage}")
    if music_job.status()["status"] == "running":
        raise HTTPException(409, "BGM解析の実行中は作成を開始できません")
    music_dir: Path | None = None
    if body.music_path and body.music_path.strip():
        music_dir = Path(body.music_path.strip()).expanduser()
        if not music_dir.is_dir():
            raise HTTPException(422, f"BGMディレクトリが見つかりません: {music_dir}")
    from ...director.beat_planner import parse_outline

    outline = parse_outline(body.flow) or None

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
                    subtitles=body.subtitles or None, music_dir=music_dir,
                    outline=outline, canvas=body.canvas,
                    project_name=(body.project_name or "").strip() or None,
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
# Music library (BGM candidates + analysis)


@router.get("/music/tracks")
def list_music_library(
    path: str,
    config: AppConfig = Depends(get_config),
    memory: MediaMemory = Depends(get_memory),
) -> dict:
    """List BGM candidates in a folder with cached analysis facts.

    Fast by design: file scan + content hash + DB lookup only — no ffprobe
    on unanalyzed files, no model calls.
    """
    from ...director.music import MUSIC_EXTENSIONS
    from ...perception.music import music_track_id

    directory = Path(path).expanduser()
    if not directory.is_dir():
        raise HTTPException(422, f"BGMディレクトリが見つかりません: {directory}")
    tracks = []
    analyzed_count = 0
    for entry in sorted(directory.rglob("*")):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.suffix.lower() not in MUSIC_EXTENSIONS:
            continue
        info: dict = {
            "file_name": entry.name,
            "path": str(entry.resolve()),
            "analyzed": False,
            "duration": None,
        }
        try:
            record = memory.get_music_track(music_track_id(entry))
        except OSError:
            record = None
        if record is not None:
            features = record.features or {}
            lyrics = record.lyrics or {}
            info.update({
                "analyzed": bool(record.analyzed_at),
                "duration": record.duration,
                "bpm": features.get("bpm"),
                "key": (f"{features.get('key', '')} "
                        f"{features.get('scale', '')}").strip() or None,
                "energy": features.get("energy"),
                "tags": [t["tag"] for t in (record.tags or [])][:6],
                "is_vocal": lyrics.get("is_vocal"),
                "lyrics_language": lyrics.get("language"),
                "description": record.description or "",
            })
            if record.analyzed_at:
                analyzed_count += 1
        tracks.append(info)
        if len(tracks) >= 500:
            break
    return {
        "path": str(directory),
        "tracks": tracks,
        "analyzed_count": analyzed_count,
    }


class MusicAnalyzeRequest(BaseModel):
    path: str


@router.post("/music/analyze")
def start_music_analyze(
    body: MusicAnalyzeRequest,
    config: AppConfig = Depends(get_config),
) -> dict:
    music_dir = Path(body.path).expanduser()
    if not music_dir.is_dir():
        raise HTTPException(422, f"BGMディレクトリが見つかりません: {music_dir}")
    if create_job.status()["status"] == "running":
        raise HTTPException(409, "作成ジョブの実行中はBGM解析を開始できません")

    def work(progress) -> str:
        import asyncio

        from ...ai.runtime import ModelRuntimeManager
        from ...ai.services import AIServices
        from ...perception.music import analyze_music_library

        conn = connect(config.paths.db_path)
        try:
            job_memory = MediaMemory(conn)
            ai = AIServices(ModelRuntimeManager(config.models))

            async def _run() -> int:
                try:
                    return await analyze_music_library(
                        music_dir, config, job_memory, ai, progress
                    )
                finally:
                    await ai.runtime.release_all()

            return str(asyncio.run(_run()))
        finally:
            conn.close()

    if not music_job.start(body.model_dump(), work):
        raise HTTPException(409, "別のBGM解析が実行中です")
    return music_job.status()


@router.get("/music/analyze/status")
def music_analyze_status() -> dict:
    return music_job.status()


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
