from pathlib import Path

import pytest

from aidirector.director.schemas import EditClip, EditPlan, EditPlanIntent
from aidirector.errors import ValidationError
from aidirector.memory.models import SegmentRecord
from aidirector.timeline.validate import validate_edit_plan
from tests.unit.test_memory import make_asset


@pytest.fixture()
def populated(memory):
    project = memory.get_or_create_project("trip", Path("/footage"))
    asset = make_asset(project.id)
    memory.upsert_asset(asset)
    memory.replace_segments(
        asset.id,
        [SegmentRecord(id="seg_ok", asset_id=asset.id, idx=0, start=0.0, end=10.0)],
    )
    return memory


def plan_with(clips):
    return EditPlan(intent=EditPlanIntent(target_duration=30), clips=clips)


def test_valid_plan_passes(populated):
    plan = plan_with([EditClip(segment_id="seg_ok", source_in=1.0, source_out=5.0)])
    assert validate_edit_plan(plan, populated) == []


def test_unknown_segment_fails(populated):
    plan = plan_with([EditClip(segment_id="seg_nope", source_in=0.0, source_out=2.0)])
    with pytest.raises(ValidationError, match="does not exist"):
        validate_edit_plan(plan, populated)


def test_inverted_bounds_fail(populated):
    plan = plan_with([EditClip(segment_id="seg_ok", source_in=5.0, source_out=5.0)])
    with pytest.raises(ValidationError, match="source_in >= source_out"):
        validate_edit_plan(plan, populated)


def test_out_of_asset_bounds_fails(populated):
    # asset duration is 12.5
    plan = plan_with([EditClip(segment_id="seg_ok", source_in=1.0, source_out=20.0)])
    with pytest.raises(ValidationError, match="exceeds asset"):
        validate_edit_plan(plan, populated)


def test_empty_plan_fails(populated):
    with pytest.raises(ValidationError, match="no clips"):
        validate_edit_plan(plan_with([]), populated)


def _plan_with_music(music):
    plan = plan_with([EditClip(segment_id="seg_ok", source_in=1.0, source_out=5.0)])
    plan.music = music
    return plan


def test_music_missing_file_fails(populated):
    from aidirector.director.schemas import PlanMusic

    music = PlanMusic(path="/nope/gone.mp3", file_name="gone.mp3")
    with pytest.raises(ValidationError, match="file not found"):
        validate_edit_plan(_plan_with_music(music), populated)


def test_music_disabled_missing_file_passes(populated):
    from aidirector.director.schemas import PlanMusic

    music = PlanMusic(path="/nope/gone.mp3", file_name="gone.mp3", enabled=False)
    assert validate_edit_plan(_plan_with_music(music), populated) == []


def test_music_implausible_gain_fails(populated, tmp_path):
    from aidirector.director.schemas import PlanMusic

    track = tmp_path / "ok.wav"
    track.write_bytes(b"RIFF")
    music = PlanMusic(path=str(track), file_name="ok.wav", gain_db=40.0)
    with pytest.raises(ValidationError, match="implausible gain"):
        validate_edit_plan(_plan_with_music(music), populated)


def test_music_bad_extension_fails(populated, tmp_path):
    from aidirector.director.schemas import PlanMusic

    track = tmp_path / "movie.mp4"
    track.write_bytes(b"\x00")
    music = PlanMusic(path=str(track), file_name="movie.mp4")
    with pytest.raises(ValidationError, match="unsupported file type"):
        validate_edit_plan(_plan_with_music(music), populated)
