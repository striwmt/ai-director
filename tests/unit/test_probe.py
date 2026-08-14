from aidirector.media.metadata import extract_metadata
from aidirector.media.probe import parse_ffprobe_json

DJI_LIKE_JSON = {
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "hevc",
            "profile": "Main 10",
            "width": 3840,
            "height": 2160,
            "pix_fmt": "yuv420p10le",
            "avg_frame_rate": "30000/1001",
            "r_frame_rate": "30000/1001",
            "time_base": "1/30000",
            "duration": "12.512500",
            "color_primaries": "bt2020",
            "color_transfer": "arib-std-b67",
            "color_space": "bt2020nc",
            "tags": {"creation_time": "2026-08-01T09:30:00.000000Z"},
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "channels": 2,
            "sample_rate": "48000",
            "duration": "12.5",
        },
    ],
    "format": {
        "filename": "DJI_0042.MP4",
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "12.513",
        "size": "104857600",
        "bit_rate": "67000000",
        "tags": {
            "make": "DJI",
            "model": "Osmo Pocket 3",
            "creation_time": "2026-08-01T09:30:00.000000Z",
            "location": "+35.6895+139.6917/",
        },
    },
}


def test_parse_video_stream():
    probe = parse_ffprobe_json(DJI_LIKE_JSON)
    video = probe.video_stream
    assert video is not None
    assert video.codec_name == "hevc"
    assert video.bit_depth == 10
    assert abs(video.fps - 29.97) < 0.01
    assert probe.duration == 12.513


def test_parse_tags_case_insensitive():
    probe = parse_ffprobe_json(DJI_LIKE_JSON)
    assert probe.camera_make == "DJI"
    assert probe.camera_model == "Osmo Pocket 3"
    assert probe.gps == "+35.6895+139.6917/"
    assert probe.creation_time == "2026-08-01T09:30:00.000000Z"


def test_metadata_extraction():
    probe = parse_ffprobe_json(DJI_LIKE_JSON)
    meta = extract_metadata(probe)
    assert meta.width == 3840
    assert meta.bit_depth == 10
    assert meta.color_transfer == "arib-std-b67"
    assert meta.audio_stream_count == 1
    assert meta.has_audio
    assert meta.camera_make == "DJI"


def test_missing_metadata_is_normal():
    probe = parse_ffprobe_json({"streams": [], "format": {}})
    meta = extract_metadata(probe)
    assert meta.duration is None
    assert not meta.has_video
    assert not meta.has_audio
    assert meta.camera_make is None


def test_cover_art_not_primary_video():
    data = {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "mjpeg"},
            {"index": 1, "codec_type": "video", "codec_name": "h264", "width": 1920},
        ],
        "format": {},
    }
    probe = parse_ffprobe_json(data)
    assert probe.video_stream.codec_name == "h264"
