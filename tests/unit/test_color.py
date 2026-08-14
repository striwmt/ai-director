from pathlib import Path

from aidirector.color.detect import ColorProfileDetector, DetectionRule
from aidirector.color.pipeline import build_color_filter
from aidirector.color.profile import ColorProfile, parse_color_profile
from aidirector.color.registry import ColorTransformRegistry
from aidirector.color.transforms import ColorTransform
from aidirector.media.metadata import MediaMetadata

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILES_YAML = PROJECT_ROOT / "config" / "color_profiles.yaml"


def make_detector() -> ColorProfileDetector:
    return ColorProfileDetector.from_yaml(PROFILES_YAML, min_confidence=0.5)


def test_hlg_detected_by_transfer():
    detector = make_detector()
    meta = MediaMetadata(color_transfer="arib-std-b67", camera_make="DJI")
    detection = detector.detect(meta, "DJI_0001.MP4")
    assert detection.profile == ColorProfile.HLG
    assert detection.confidence > 0.9


def test_rec709_detected_by_bt709():
    detector = make_detector()
    meta = MediaMetadata(color_transfer="bt709", color_primaries="bt709")
    detection = detector.detect(meta, "IMG_1234.MOV")
    assert detection.profile == ColorProfile.REC709


def test_dji_dlog2_by_filename():
    detector = make_detector()
    meta = MediaMetadata(camera_make="DJI")
    detection = detector.detect(meta, "DJI_0042_DLOG2.MP4")
    assert detection.profile == ColorProfile.DJI_DLOG2
    assert detection.confidence >= 0.9


def test_unknown_when_no_signal():
    detector = make_detector()
    meta = MediaMetadata()
    detection = detector.detect(meta, "clip.mp4")
    assert detection.profile == ColorProfile.UNKNOWN


def test_user_override_wins():
    detector = make_detector()
    meta = MediaMetadata(color_transfer="bt709")
    detection = detector.detect(meta, "x.mp4", override=ColorProfile.DJI_DLOG_M)
    assert detection.profile == ColorProfile.DJI_DLOG_M
    assert detection.confidence == 1.0
    assert detection.source == "user"


def test_parse_color_profile_dashes():
    assert parse_color_profile("dji-dlog2") == ColorProfile.DJI_DLOG2
    assert parse_color_profile("auto") == ColorProfile.UNKNOWN


def test_registry_skips_missing_lut(tmp_path):
    registry = ColorTransformRegistry.from_yaml(
        PROFILES_YAML, base_dir=PROJECT_ROOT
    )
    # No vendor LUT installed in the repo -> lut3d transform unusable.
    transform = registry.resolve(
        ColorProfile.DJI_DLOG2, ColorProfile.REC709, "analysis"
    )
    assert transform is None


def test_registry_uses_present_lut(tmp_path):
    lut = tmp_path / "test.cube"
    lut.write_text("LUT_3D_SIZE 2\n0 0 0\n1 1 1\n")
    registry = ColorTransformRegistry(
        [
            ColorTransform(
                id="test_lut",
                source_profile=ColorProfile.DJI_DLOG2,
                destination_profile=ColorProfile.REC709,
                type="lut3d",
                path=lut,
            )
        ]
    )
    transform = registry.resolve(ColorProfile.DJI_DLOG2, ColorProfile.REC709, "analysis")
    assert transform is not None and transform.id == "test_lut"

    result = build_color_filter(ColorProfile.DJI_DLOG2, registry, "analysis")
    assert "lut3d" in result.filter_expr
    assert result.lut_hash is not None
    assert not result.is_fallback


def test_pipeline_fallback_for_log_without_lut():
    registry = ColorTransformRegistry([])
    result = build_color_filter(ColorProfile.DJI_DLOG2, registry, "analysis")
    assert result.is_fallback
    assert result.filter_expr is not None


def test_pipeline_passthrough_for_rec709():
    registry = ColorTransformRegistry.from_yaml(PROFILES_YAML, base_dir=PROJECT_ROOT)
    result = build_color_filter(ColorProfile.REC709, registry, "analysis")
    assert result.filter_expr is None
    assert not result.is_fallback


def test_pipeline_hlg_tonemap_from_config():
    registry = ColorTransformRegistry.from_yaml(PROFILES_YAML, base_dir=PROJECT_ROOT)
    result = build_color_filter(ColorProfile.HLG, registry, "analysis")
    assert result.transform_id == "hlg_to_rec709_tonemap"
    assert "tonemap" in result.filter_expr
