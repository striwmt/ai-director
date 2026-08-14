"""aidirector command line interface (AGENT.md §64: CLI first)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .ai.runtime import ModelRuntimeManager
from .ai.services import AIServices
from .color.profile import ColorProfile, parse_color_profile
from .config import AppConfig, ensure_dirs, load_config
from .errors import AIDirectorError
from .logging import get_logger, setup_logging
from .memory.database import connect
from .memory.repository import MediaMemory

app = typer.Typer(
    name="aidirector",
    help="Local AI Director — understands footage and drafts an edit plan.",
    no_args_is_help=True,
)
log = get_logger("cli")


def _setup(config_file: Optional[Path], log_level: Optional[str]) -> AppConfig:
    config = load_config(config_file)
    setup_logging(log_level or config.log_level)
    ensure_dirs(config)
    return config


def _open_memory(config: AppConfig) -> MediaMemory:
    return MediaMemory(connect(config.paths.db_path))


def _make_ai(config: AppConfig) -> AIServices:
    return AIServices(ModelRuntimeManager(config.models))


def _parse_override(color_profile: str) -> ColorProfile | None:
    try:
        profile = parse_color_profile(color_profile)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return None if profile == ColorProfile.UNKNOWN else profile


@app.command()
def version() -> None:
    """Show version."""
    typer.echo(f"aidirector {__version__}")


@app.command()
def ingest(
    footage: Path = typer.Argument(..., help="Footage directory"),
    color_profile: str = typer.Option("auto", help="auto or explicit profile (e.g. dji-dlog2)"),
    config_file: Optional[Path] = typer.Option(None, "--config"),
    log_level: Optional[str] = typer.Option(None, "--log-level"),
) -> None:
    """Scan footage, probe metadata, detect color profiles, store assets."""
    config = _setup(config_file, log_level)
    memory = _open_memory(config)
    from .pipeline import run_ingest

    try:
        report = run_ingest(
            footage, config, memory, Path.cwd(),
            color_override=_parse_override(color_profile),
        )
    except AIDirectorError as exc:
        raise typer.Exit(code=_fail(str(exc)))
    typer.echo(
        f"ingested {len(report.ingested)} file(s), "
        f"{len(report.skipped_unchanged)} unchanged, "
        f"{len(report.failed)} failed, "
        f"{len(report.sidecars)} sidecar(s) linked"
    )


@app.command()
def analyze(
    footage: Path = typer.Argument(..., help="Footage directory"),
    color_profile: str = typer.Option("auto", help="auto or explicit profile"),
    config_file: Optional[Path] = typer.Option(None, "--config"),
    log_level: Optional[str] = typer.Option(None, "--log-level"),
) -> None:
    """Full analysis: ingest + color + segments + technical + ASR + VLM + embeddings."""
    config = _setup(config_file, log_level)
    memory = _open_memory(config)
    ai = _make_ai(config)
    from .pipeline import run_analyze

    try:
        project_id = asyncio.run(
            run_analyze(
                footage, config, memory, ai, Path.cwd(),
                color_override=_parse_override(color_profile),
            )
        )
    except AIDirectorError as exc:
        raise typer.Exit(code=_fail(str(exc)))
    typer.echo(f"analysis complete: project {project_id}")


@app.command()
def edit(
    footage: Path = typer.Argument(..., help="Footage directory"),
    duration: float = typer.Option(60.0, "--duration", help="Target duration (seconds)"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Director profile name"),
    prompt: str = typer.Option("", "--prompt", help="What the video should be"),
    color_profile: str = typer.Option("auto"),
    canvas: Optional[str] = typer.Option(
        None, "--canvas", help="auto | landscape | portrait | WxH (e.g. 1920x1080)"
    ),
    captions: Optional[str] = typer.Option(
        None, "--captions",
        help="Scene captions (time/place, centered): none | beats | clips",
    ),
    caption_format: Optional[str] = typer.Option(
        None, "--caption-format",
        help=(
            'Caption layout, e.g. "{HH}:{MM} {PLACE}". Tokens: {PLACE} {DATE} '
            "{TIME} {YYYY} {MO} {DD} {HH} {MM}; \\n starts the second line"
        ),
    ),
    no_preview: bool = typer.Option(False, "--no-preview", help="Skip preview rendering"),
    config_file: Optional[Path] = typer.Option(None, "--config"),
    log_level: Optional[str] = typer.Option(None, "--log-level"),
) -> None:
    """Analyze footage (incrementally) and produce edit-plan.json + preview.mp4."""
    config = _setup(config_file, log_level)
    memory = _open_memory(config)
    ai = _make_ai(config)

    async def _run() -> tuple[str, Path, Path | None]:
        from .director.orchestrator import run_director
        from .pipeline import run_analyze
        from .timeline.compiler import compile_timeline
        from .timeline.preview import render_preview
        from .timeline.validate import validate_edit_plan

        project_id = await run_analyze(
            footage, config, memory, ai, Path.cwd(),
            color_override=_parse_override(color_profile),
        )
        plan_id, plan = await run_director(
            project_id, config, memory, ai,
            user_prompt=prompt, target_duration=duration, profile_name=profile,
            captions=captions, caption_format=caption_format,
        )
        await ai.runtime.release_all()

        validate_edit_plan(plan, memory)

        plan_path = config.paths.plans_dir / f"{plan_id}.json"
        plan_path.write_text(
            json.dumps(plan.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        preview_path: Path | None = None
        if not no_preview:
            timeline = compile_timeline(
                plan, memory, canvas=canvas or config.output.canvas
            )
            preview_path = render_preview(
                timeline, config, config.paths.renders_dir / f"{plan_id}.mp4"
            )
        return plan_id, plan_path, preview_path

    try:
        plan_id, plan_path, preview_path = asyncio.run(_run())
    except AIDirectorError as exc:
        raise typer.Exit(code=_fail(str(exc)))
    typer.echo(f"edit plan: {plan_id}")
    typer.echo(f"  plan json: {plan_path}")
    if preview_path:
        typer.echo(f"  preview:   {preview_path}")


@app.command()
def preview(
    plan_id: str = typer.Argument(..., help="Edit plan id (or 'latest')"),
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
    canvas: Optional[str] = typer.Option(
        None, "--canvas", help="auto | landscape | portrait | WxH"
    ),
    config_file: Optional[Path] = typer.Option(None, "--config"),
    log_level: Optional[str] = typer.Option(None, "--log-level"),
) -> None:
    """Render an FFmpeg preview MP4 for a stored edit plan."""
    config = _setup(config_file, log_level)
    memory = _open_memory(config)
    from .timeline.compiler import compile_timeline
    from .timeline.preview import render_preview

    plan_id, plan = _load_plan(memory, plan_id)
    try:
        timeline = compile_timeline(plan, memory, canvas=canvas or config.output.canvas)
        path = render_preview(
            timeline, config, output or config.paths.renders_dir / f"{plan_id}.mp4"
        )
    except AIDirectorError as exc:
        raise typer.Exit(code=_fail(str(exc)))
    typer.echo(str(path))


@app.command()
def export(
    plan_id: str = typer.Argument(..., help="Edit plan id (or 'latest')"),
    format: str = typer.Option("fcpxml", "--format", help="fcpxml | otio | edl"),
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
    config_file: Optional[Path] = typer.Option(None, "--config"),
    log_level: Optional[str] = typer.Option(None, "--log-level"),
) -> None:
    """Export a stored edit plan for an NLE. References ORIGINAL camera media."""
    config = _setup(config_file, log_level)
    memory = _open_memory(config)
    from .timeline.compiler import compile_timeline

    plan_id, plan = _load_plan(memory, plan_id)
    try:
        timeline = compile_timeline(plan, memory, canvas=config.output.canvas)
        if format == "fcpxml":
            from .timeline.fcpxml import export_fcpxml as exporter
            suffix = ".fcpxml"
        elif format == "otio":
            from .timeline.otio import export_otio as exporter
            suffix = ".otio"
        elif format == "edl":
            from .timeline.edl import export_edl as exporter
            suffix = ".edl"
        else:
            raise typer.BadParameter("format must be fcpxml, otio or edl")
        path = exporter(
            timeline, output or config.paths.renders_dir / f"{plan_id}{suffix}"
        )
    except AIDirectorError as exc:
        raise typer.Exit(code=_fail(str(exc)))
    typer.echo(str(path))


@app.command()
def search(
    footage: Path = typer.Argument(..., help="Footage directory (project root)"),
    query: str = typer.Argument(..., help="Natural language query"),
    limit: int = typer.Option(10, "--limit"),
    config_file: Optional[Path] = typer.Option(None, "--config"),
    log_level: Optional[str] = typer.Option(None, "--log-level"),
) -> None:
    """Search Media Memory (embeddings + transcript + descriptions)."""
    config = _setup(config_file, log_level)
    memory = _open_memory(config)
    ai = _make_ai(config)
    from .memory.search import MediaSearch
    from .perception.interpretation import build_understanding

    project = memory.get_or_create_project(footage.resolve().name, footage.resolve())

    async def _run():
        hits = await MediaSearch(memory, ai).search(project.id, query, limit=limit)
        await ai.runtime.release_all()
        return hits

    hits = asyncio.run(_run())
    if not hits:
        typer.echo("no results")
        return
    for hit in hits:
        segment = memory.get_segment(hit.segment_id)
        if segment is None:
            continue
        understanding = build_understanding(segment, memory)
        typer.echo(f"{hit.score:.3f}  {understanding.to_summary_line()}")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8484, "--port"),
    config_file: Optional[Path] = typer.Option(None, "--config"),
    log_level: Optional[str] = typer.Option(None, "--log-level"),
) -> None:
    """Start the review/edit web UI (reorder, trim, captions, re-render)."""
    config = _setup(config_file, log_level)
    try:
        import uvicorn
    except ImportError as exc:
        raise typer.Exit(
            code=_fail("web extra not installed. Run: uv sync --extra web")
        ) from exc
    from .web.app import create_app

    typer.echo(f"AI Director UI: http://{host}:{port}/")
    uvicorn.run(create_app(config), host=host, port=port, log_level="warning")


def _load_plan(memory: MediaMemory, plan_id: str):
    from .director.schemas import EditPlan

    if plan_id == "latest":
        row = memory.conn.execute(
            "SELECT id, plan_json FROM edit_plans ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if not row:
            raise typer.Exit(code=_fail("no edit plans stored yet"))
        return row["id"], EditPlan.model_validate_json(row["plan_json"])
    plan_json = memory.get_edit_plan(plan_id)
    if plan_json is None:
        raise typer.Exit(code=_fail(f"edit plan not found: {plan_id}"))
    return plan_id, EditPlan.model_validate_json(plan_json)


def _fail(message: str) -> int:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    return 1


if __name__ == "__main__":
    app()
