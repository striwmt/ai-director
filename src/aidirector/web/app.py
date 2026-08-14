"""FastAPI application factory for the review/edit UI."""

from __future__ import annotations

from pathlib import Path

from ..config import AppConfig, ensure_dirs
from ..logging import get_logger

log = get_logger("web")

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(config: AppConfig):
    try:
        from fastapi import FastAPI
        from fastapi.responses import FileResponse
    except ImportError as exc:
        raise RuntimeError(
            "web extra not installed. Install with: uv sync --extra web"
        ) from exc

    from .api.routes import router

    ensure_dirs(config)
    app = FastAPI(title="AI Director", docs_url="/api/docs")
    app.state.config = config
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")

    return app
