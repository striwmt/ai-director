"""Within-phase progress reporting (done/total/item) for the create job."""

from __future__ import annotations

import threading
import time

from aidirector.pipeline import _notify
from aidirector.web.jobs import PipelineJob


def test_pipeline_job_reports_progress():
    job = PipelineJob()
    reached = threading.Event()
    release = threading.Event()

    def work(progress):
        progress("vision", done=3, total=81, item="CLIP_0042.MP4")
        reached.set()
        release.wait(5)
        progress("director")  # phase-only call resets the detail
        return "plan_x"

    assert job.start({}, work)
    assert reached.wait(5)
    state = job.status()
    assert state["phase"] == "vision"
    assert state["progress"] == {"done": 3, "total": 81, "item": "CLIP_0042.MP4"}

    release.set()
    for _ in range(50):
        if job.status()["status"] == "done":
            break
        time.sleep(0.1)
    state = job.status()
    assert state["status"] == "done"
    assert state["progress"] is None, "phase-only call cleared the detail"


def test_notify_falls_back_for_phase_only_callbacks():
    phases: list[str] = []

    def old_style(phase):  # no keyword support
        phases.append(phase)

    _notify(old_style, "vision", done=1, total=2, item="x")
    assert phases == ["vision"]
    _notify(None, "vision", done=1, total=2)  # no callback: no error


def test_notify_passes_details_through():
    calls: list[tuple] = []

    def new_style(phase, done=None, total=None, item=None):
        calls.append((phase, done, total, item))

    _notify(new_style, "segments", done=4, total=10, item="a.mp4")
    assert calls == [("segments", 4, 10, "a.mp4")]
