"""FastAPI application factory for the review/edit UI."""

from __future__ import annotations

from pathlib import Path

from ..config import AppConfig, ensure_dirs
from ..logging import get_logger

log = get_logger("web")

_STATIC_DIR = Path(__file__).parent / "static"


def _warm_ssl() -> None:
    """Create the first SSLContext on the main thread.

    Some OpenSSL 3.x builds fail with ``ssl.SSLError: unknown error`` when
    the very first context is created off the main thread — which is exactly
    where background create/render jobs build their httpx clients.
    """
    try:
        import ssl

        ssl.create_default_context()
    except Exception as exc:  # pragma: no cover - environment specific
        log.warning("ssl warmup failed: %s", exc)


def create_app(config: AppConfig):
    try:
        from fastapi import FastAPI
        from fastapi.responses import FileResponse
    except ModuleNotFoundError as exc:
        if exc.name in ("fastapi", "uvicorn"):
            raise RuntimeError(
                "web extra not installed. Install with: uv sync --extra web"
            ) from exc
        raise  # a dependency of fastapi is broken — show the real traceback

    _warm_ssl()

    from .api.routes import router

    ensure_dirs(config)
    app = FastAPI(title="AI Director", docs_url="/api/docs")
    app.state.config = config
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")

    return app
