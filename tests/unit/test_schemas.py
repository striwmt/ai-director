import json

import pytest
from pydantic import ValidationError

from aidirector.ai.schemas import Transcript, TranscriptSegment, TranscriptWord
from aidirector.director.schemas import (
    Beat,
    BeatPlan,
    ClipAudio,
    EditClip,
    EditPlan,
    EditPlanIntent,
)
from aidirector.director.beat_planner import normalize_beats


def test_transcript_schema():
    transcript = Transcript(
        language="ja",
        duration=10.0,
        segments=[
            TranscriptSegment(
                start=0.0, end=2.5, text="駅に着きました",
                words=[TranscriptWord(start=0.0, end=0.5, text="駅", probability=0.98)],
            )
        ],
    )
    assert transcript.text == "駅に着きました"
    reloaded = Transcript.model_validate_json(transcript.model_dump_json())
    assert reloaded.segments[0].words[0].probability == 0.98


def test_edit_plan_roundtrip():
    plan = EditPlan(
        intent=EditPlanIntent(target_duration=90, profile="travel_vlog", user_prompt="雨"),
        clips=[
            EditClip(segment_id="seg_1", source_in=12.4, source_out=17.1,
                     story_beat="opening", reason="rain mood"),
            EditClip(segment_id="seg_2", source_in=0.0, source_out=5.0,
                     audio=ClipAudio(mode="muted")),
        ],
    )
    assert abs(plan.total_duration - 9.7) < 1e-6
    data = json.loads(plan.model_dump_json())
    reloaded = EditPlan.model_validate(data)
    assert reloaded.clips[1].audio.mode == "muted"


def test_edit_plan_rejects_bad_values():
    with pytest.raises(ValidationError):
        EditClip(segment_id="s", source_in=-1.0, source_out=2.0)
    with pytest.raises(ValidationError):
        EditPlanIntent(target_duration=0)


def test_normalize_beats_hits_target():
    plan = BeatPlan(
        target_duration=100,
        beats=[Beat(name="a", duration=10), Beat(name="b", duration=30)],
    )
    normalized = normalize_beats(plan, 60.0)
    assert abs(sum(b.duration for b in normalized.beats) - 60.0) < 0.1
    assert normalized.beats[0].duration == 15.0
