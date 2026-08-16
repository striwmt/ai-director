"""Background jobs for the web UI.

RenderJobs: one preview render per plan at a time.
PipelineJob: single-slot create job (analyze -> director -> preview) with
log capture — only one may run because the pipeline owns the GPU.
"""

from __future__ import annotations

import collections
import logging
import threading
from typing import Callable

from ..logging import get_logger

log = get_logger("web.jobs")


class _RingBufferHandler(logging.Handler):
    """Capture aidirector log lines for the UI's progress view."""

    def __init__(self, maxlen: int = 200) -> None:
        super().__init__(level=logging.INFO)
        self.lines: collections.deque[str] = collections.deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            name = record.name.removeprefix("aidirector.")
            self.lines.append(f"{name}: {record.getMessage()}")
        except Exception:
            pass


class PipelineJob:
    """One create job at a time; status/log polled by the frontend."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict = {"status": "idle", "phase": None, "progress": None,
                             "log": [], "plan_id": None, "error": None,
                             "params": None}
        self._handler: _RingBufferHandler | None = None

    def start(self, params: dict, work: Callable[[Callable[[str], None]], str]) -> bool:
        """Start ``work(progress)`` in a thread; returns False if busy.

        ``work`` receives a progress callback and returns the plan id. The
        callback takes the phase name, plus optional done/total counters and
        an item label for within-phase progress: progress("vision", done=3,
        total=81, item="CLIP_0042.MP4"). Phase-only calls reset the detail.
        """
        with self._lock:
            if self._state["status"] == "running":
                return False
            handler = _RingBufferHandler()
            self._handler = handler
            self._state = {"status": "running", "phase": "starting",
                           "progress": None, "log": [],
                           "plan_id": None, "error": None, "params": params}

        def progress(
            phase: str,
            done: int | None = None,
            total: int | None = None,
            item: str | None = None,
        ) -> None:
            detail = (
                {"done": done, "total": total, "item": item}
                if (done is not None or total is not None or item is not None)
                else None
            )
            with self._lock:
                self._state["phase"] = phase
                self._state["progress"] = detail

        def _run() -> None:
            root = logging.getLogger("aidirector")
            root.addHandler(handler)
            try:
                plan_id = work(progress)
                with self._lock:
                    self._state["status"] = "done"
                    self._state["plan_id"] = plan_id
                    self._state["phase"] = "done"
            except Exception as exc:
                log.error("create job failed: %s", exc)
                with self._lock:
                    self._state["status"] = "failed"
                    self._state["error"] = str(exc)
            finally:
                root.removeHandler(handler)

        threading.Thread(target=_run, name="aidirector-create", daemon=True).start()
        return True

    def status(self) -> dict:
        with self._lock:
            state = dict(self._state)
        if self._handler is not None:
            state["log"] = list(self._handler.lines)
        return state


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
