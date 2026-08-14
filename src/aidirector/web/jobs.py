"""Background render jobs for the web UI.

One render per plan at a time; status is polled by the frontend.
"""

from __future__ import annotations

import threading
from typing import Callable

from ..logging import get_logger

log = get_logger("web.jobs")


class RenderJobs:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, plan_id: str, work: Callable[[], str]) -> dict:
        """Start (or return the running) render job for a plan.

        ``work`` runs in a thread and returns the output path.
        """
        with self._lock:
            job = self._jobs.get(plan_id)
            if job and job["status"] == "running":
                return job
            job = {"status": "running", "path": None, "error": None}
            self._jobs[plan_id] = job

        def _run() -> None:
            try:
                path = work()
                with self._lock:
                    job["status"] = "done"
                    job["path"] = path
            except Exception as exc:
                log.error("render job failed for %s: %s", plan_id, exc)
                with self._lock:
                    job["status"] = "failed"
                    job["error"] = str(exc)

        threading.Thread(target=_run, name=f"render-{plan_id}", daemon=True).start()
        return job

    def status(self, plan_id: str) -> dict:
        with self._lock:
            return dict(self._jobs.get(plan_id, {"status": "idle"}))
