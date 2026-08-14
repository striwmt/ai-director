"""OpenTimelineIO export (minimal native .otio JSON, no library dependency)."""

from __future__ import annotations

import json
from pathlib import Path

from .model import Timeline


def _rational_time(value: float, rate: float) -> dict:
    return {
        "OTIO_SCHEMA": "RationalTime.1",
        "rate": rate,
        "value": round(value * rate),
    }


def timeline_to_otio(timeline: Timeline) -> dict:
    rate = timeline.fps
    clips = []
    for clip in timeline.clips:
        metadata = {
            "segment_id": clip.segment_id,
            "story_beat": clip.story_beat,
            "reason": clip.reason,
        }
        markers = []
        if clip.caption is not None:
            caption_text = " / ".join(
                line for line in (clip.caption.text, clip.caption.secondary) if line
            )
            metadata["caption"] = clip.caption.model_dump()
            # A marker at the cut makes the caption visible in NLEs that
            # import OTIO markers (e.g. Resolve) even without a title clip.
            markers.append(
                {
                    "OTIO_SCHEMA": "Marker.2",
                    "name": f"CAPTION: {caption_text}",
                    "color": "GREEN",
                    "marked_range": {
                        "OTIO_SCHEMA": "TimeRange.1",
                        "start_time": _rational_time(clip.source_in, rate),
                        "duration": _rational_time(
                            min(clip.caption.duration, clip.duration), rate
                        ),
                    },
                    "metadata": {},
                }
            )
        clips.append(
            {
                "OTIO_SCHEMA": "Clip.2",
                "name": Path(clip.original_path).stem,
                "source_range": {
                    "OTIO_SCHEMA": "TimeRange.1",
                    "start_time": _rational_time(clip.source_in, rate),
                    "duration": _rational_time(clip.duration, rate),
                },
                "media_reference": {
                    "OTIO_SCHEMA": "ExternalReference.1",
                    "target_url": Path(clip.original_path).resolve().as_uri(),
                },
                "markers": markers,
                "metadata": {"aidirector": metadata},
            }
        )
    children = [
        {
            "OTIO_SCHEMA": "Track.1",
            "name": "V1",
            "kind": "Video",
            "children": clips,
        }
    ]

    # BGM: an audio track referencing the ORIGINAL music file. OTIO has no
    # standard volume effect, so the mix parameters travel as metadata.
    music = timeline.music
    if music is not None and music.enabled:
        music_len = timeline.duration
        if music.duration:
            music_len = min(music.duration, timeline.duration)
        children.append(
            {
                "OTIO_SCHEMA": "Track.1",
                "name": "A1 Music",
                "kind": "Audio",
                "children": [
                    {
                        "OTIO_SCHEMA": "Clip.2",
                        "name": music.file_name or Path(music.path).stem,
                        "source_range": {
                            "OTIO_SCHEMA": "TimeRange.1",
                            "start_time": _rational_time(0.0, rate),
                            "duration": _rational_time(music_len, rate),
                        },
                        "media_reference": {
                            "OTIO_SCHEMA": "ExternalReference.1",
                            "target_url": Path(music.path).resolve().as_uri(),
                        },
                        "markers": [],
                        "metadata": {
                            "aidirector": {
                                "role": "music",
                                "gain_db": music.gain_db,
                                "fade_in": music.fade_in,
                                "fade_out": music.fade_out,
                                "ducking": music.ducking,
                                "reason": music.reason,
                            }
                        },
                    }
                ],
            }
        )

    return {
        "OTIO_SCHEMA": "Timeline.1",
        "name": timeline.name,
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "children": children,
        },
    }


def export_otio(timeline: Timeline, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(timeline_to_otio(timeline), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output
