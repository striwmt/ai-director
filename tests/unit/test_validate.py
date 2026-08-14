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
