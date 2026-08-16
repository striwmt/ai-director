"""AI Director orchestrator (AGENT.md §47).

    Intent -> Story -> Beats -> Retrieval -> Selection -> Sequence
           -> Critic -> Revision -> Edit Plan

Never a single giant prompt; each stage is structured and validated.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from ..ai.services import AIServices
from ..config import AppConfig
from ..logging import get_logger
from ..memory.repository import MediaMemory
from ..memory.search import MediaSearch
from ..perception.interpretation import SegmentUnderstanding, build_understanding
from .beat_planner import plan_beats
from .critic import critique_edit
from .editor import (
    dedupe_assets,
    describe_selection,
    edit_sequence,
    enforce_target_duration,
    sort_chronologically,
)
from .music import (
    annotate_tracks,
    list_music_tracks,
    rank_tracks_for_prompt,
    resolve_choice,
    select_music,
)
from .profile import DirectorProfile, load_director_profile
from .prompts import PROMPT_VERSION
from .schemas import (
    BeatSelection,
    ClipAudio,
    ClipCaption,
    ClipTransition,
    EditClip,
    EditPlan,
    EditPlanIntent,
    EditPlanStory,
    SequencePlan,
    StoryPlan,
    SubtitleLine,
)
from .selector import retrieve_candidates, select_for_beat
from .story_planner import plan_story

log = get_logger("director")

_AUDIO_INTENT_TO_MODE = {
    "preserve_ambient": "original",
    "preserve_speech": "original",
    "mute": "muted",
    "duck": "ducked",
}


def build_project_summary(memory: MediaMemory, project_id: str, limit: int = 40) -> str:
    """Compact project overview for the story planner — a summary, never the
    whole Media Memory (AGENT.md §61)."""
    assets = memory.list_assets(project_id, kind="video")
    segments = memory.list_project_segments(project_id)
    total_duration = sum(a.duration or 0.0 for a in assets)

    lines = [
        f"{len(assets)} video files, {len(segments)} segments, "
        f"{total_duration / 60:.1f} minutes of footage total.",
    ]
    times = sorted(
        a.metadata.creation_time for a in assets if a.metadata.creation_time
    )
    if times:
        lines.append(f"Recorded between {times[0]} and {times[-1]}.")

    lines.append("\nSample of what the footage contains:")
    step = max(1, len(segments) // limit)
    for segment in segments[::step][:limit]:
        understanding = build_understanding(segment, memory)
        if understanding.description or understanding.transcript:
            lines.append("- " + understanding.to_summary_line())
    return "\n".join(lines)


DEFAULT_CAPTION_FORMAT = "{PLACE}\n{DATE} {TIME}"

_TOKEN_RE = re.compile(r"\{(PLACE|DATE|TIME|YYYY|MO|DD|HH|MM)\}")
_PUNCT_ONLY_RE = re.compile(r"^[\s:\-–—・/|,.]+$")


def _caption_token_values(place: str, recorded_at: str | None) -> dict[str, str]:
    from ..perception.interpretation import parse_creation_time

    values = {"PLACE": place, "DATE": "", "TIME": "",
              "YYYY": "", "MO": "", "DD": "", "HH": "", "MM": ""}
    moment = parse_creation_time(recorded_at)
    if moment is not None:
        values.update(
            DATE=moment.strftime("%Y-%m-%d"),
            TIME=moment.strftime("%H:%M"),
            YYYY=moment.strftime("%Y"),
            MO=moment.strftime("%m"),
            DD=moment.strftime("%d"),
            HH=moment.strftime("%H"),
            MM=moment.strftime("%M"),
        )
    return values


def _render_caption_line(template: str, values: dict[str, str]) -> str:
    line = _TOKEN_RE.sub(lambda m: values[m.group(1)], template)
    # Drop words that collapsed to bare punctuation (e.g. "{HH}:{MM}" with
    # no recording time leaves ":"), then tidy whitespace.
    words = [w for w in line.split() if not _PUNCT_ONLY_RE.match(w)]
    return " ".join(words).strip(" :・-–—/|,")


def build_caption(
    clip,
    understanding: SegmentUnderstanding | None,
    caption_format: str = DEFAULT_CAPTION_FORMAT,
) -> ClipCaption | None:
    """Deterministic caption from plan facts: place from the sequence
    editor's location call, time from recorded metadata, layout from the
    user's format template. No facts, no caption — nothing is invented.

    Format tokens: {PLACE} {DATE} {TIME} {YYYY} {MO} {DD} {HH} {MM};
    '\\n' starts the smaller second line.
    """
    place = (clip.location or "").strip()
    recorded_at = understanding.recorded_at if understanding else None
    values = _caption_token_values(place, recorded_at)

    template = caption_format.replace("\\n", "\n")
    line_templates = template.split("\n", 1)
    lines = [_render_caption_line(t, values) for t in line_templates]
    lines = [line for line in lines if line]
    if not lines:
        return None
    return ClipCaption(text=lines[0], secondary=lines[1] if len(lines) > 1 else "")


def sequence_to_edit_plan(
    sequence: SequencePlan,
    story: StoryPlan,
    intent: EditPlanIntent,
    *,
    captions: str = "none",
    caption_format: str = DEFAULT_CAPTION_FORMAT,
    segments_by_id: dict[str, SegmentUnderstanding] | None = None,
) -> EditPlan:
    segments_by_id = segments_by_id or {}
    clips: list[EditClip] = []
    previous_beat: str | None = None
    for c in sequence.clips:
        caption: ClipCaption | None = None
        if captions == "clips" or (captions == "beats" and c.story_beat != previous_beat):
            caption = build_caption(
                c, segments_by_id.get(c.segment_id), caption_format
            )
        previous_beat = c.story_beat
        clips.append(
            EditClip(
                segment_id=c.segment_id,
                source_in=c.source_in,
                source_out=c.source_out,
                story_beat=c.story_beat,
                audio=ClipAudio(mode=_AUDIO_INTENT_TO_MODE.get(c.audio_intent, "original")),
                transition=ClipTransition(
                    type=c.transition,
                    duration=0.5 if c.transition == "crossfade" else 0.0,
                ),
                caption=caption,
                reason=c.reason,
            )
        )
    return EditPlan(
        version=1,
        intent=intent,
        story=EditPlanStory(concept=story.concept, tone=story.tone),
        clips=clips,
    )


def fill_subtitles(plan: EditPlan, memory: MediaMemory) -> EditPlan:
    """Attach transcript lines overlapping each clip as subtitles.

    Pure fact extraction (transcript + time math) — no AI judgement.
    """
    from ..perception.speech import subtitle_lines_for_span

    transcripts: dict[str, object] = {}
    for clip in plan.clips:
        segment = memory.get_segment(clip.segment_id)
        if segment is None:
            continue
        if segment.asset_id not in transcripts:
            transcripts[segment.asset_id] = memory.get_transcript(segment.asset_id)
        lines = subtitle_lines_for_span(
            transcripts[segment.asset_id], clip.source_in, clip.source_out
        )
        clip.subtitles = [
            SubtitleLine(start=s, end=e, text=t) for s, e, t in lines
        ]
    return plan


async def run_director(
    project_id: str,
    config: AppConfig,
    memory: MediaMemory,
    ai: AIServices,
    *,
    user_prompt: str,
    target_duration: float,
    profile_name: str | None = None,
    captions: str | None = None,
    caption_format: str | None = None,
    subtitles: bool | None = None,
    music_dir: Path | None = None,
    outline: list[str] | None = None,
    canvas: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, EditPlan]:
    """Run the full director pipeline. Returns (plan_id, edit_plan)."""

    def tick(**details) -> None:
        # Within-phase progress for the UI; phase-only callbacks are fine.
        if progress is None:
            return
        try:
            progress("director", **details)
        except TypeError:
            pass
    profile_name = profile_name or config.director.default_profile
    profile: DirectorProfile = load_director_profile(
        config.director.profiles_dir, profile_name
    )
    outline = outline or []
    intent = EditPlanIntent(
        target_duration=target_duration, profile=profile_name,
        user_prompt=user_prompt, outline=outline,
        captions=captions, caption_format=caption_format, subtitles=subtitles,
        music_dir=str(music_dir) if music_dir else None, canvas=canvas,
    )
    run_id = memory.create_director_run(project_id, intent.model_dump())
    search = MediaSearch(memory, ai)

    try:
        # 1. Story
        tick(item="ストーリー構成")
        summary = build_project_summary(memory, project_id)
        story_prompt = user_prompt
        if outline:
            story_prompt = (
                f"{user_prompt}\n\nRequired story flow, in this exact order: "
                + " → ".join(outline)
            ).strip()
        story = await plan_story(
            ai,
            user_prompt=story_prompt,
            target_duration=target_duration,
            profile=profile,
            project_summary=summary,
        )
        log.info("story: %s (tone=%s, arc=%s)", story.concept, story.tone, story.story_arc)

        # 2. Beats — a user outline becomes the beat structure verbatim
        # (enforce_outline guarantees names and order in code).
        tick(item="ビート設計")
        beats = await plan_beats(
            ai, story=story, target_duration=target_duration, outline=outline or None,
        )
        log.info("beats: %s", [(b.name, b.duration) for b in beats.beats])

        # Profile preferences with a deterministic guarantee (AGENT.md §2):
        # the prompts advise the LLM, these flags make code enforce it.
        # chronology: strict/preferred -> sort by recording time; flexible ->
        # the AI's order stands. duplicate_shots: allow -> a source video may
        # be cut into several clips (talk profile); anything else -> one
        # clip per source video.
        enforce_chronology = (
            profile.preferences.get("chronology", "preferred") != "flexible"
        )
        reuse_allowed = profile.preferences.get("duplicate_shots", "avoid") == "allow"
        segments_of_asset: dict[str, list[str]] = {}
        if not reuse_allowed:
            for segment in memory.list_project_segments(project_id):
                segments_of_asset.setdefault(segment.asset_id, []).append(segment.id)

        # 3+4. Per-beat retrieval and selection. usage_counts steers half of
        # each beat's candidate slots toward footage past plans never used,
        # so re-creating explores the whole library over time.
        usage_counts = memory.asset_usage_counts(project_id)
        used: list[SegmentUnderstanding] = []
        used_ids: set[str] = set()
        selections: list[tuple[BeatSelection, list[SegmentUnderstanding]]] = []
        segments_by_id: dict[str, SegmentUnderstanding] = {}
        for beat_idx, beat in enumerate(beats.beats):
            tick(done=beat_idx, total=len(beats.beats),
                 item=f"素材選択: {beat.name}")
            candidates = await retrieve_candidates(
                search, memory, project_id, beat, story,
                limit=config.director.candidates_per_beat, exclude=used_ids,
                usage_counts=usage_counts,
            )
            for c in candidates:
                segments_by_id[c.segment_id] = c
            selection = await select_for_beat(
                ai, story=story, beat=beat, candidates=candidates, used=used,
            )
            chosen = [
                segments_by_id[c.segment_id]
                for c in selection.choices
                if c.segment_id in segments_by_id
            ]
            used.extend(chosen)
            used_ids.update(c.segment_id for c in chosen)
            if not reuse_allowed:
                # Exclude every other segment of the chosen source videos
                # from later retrieval, so no video appears twice.
                for c in chosen:
                    used_ids.update(segments_of_asset.get(c.asset_id, []))
            selections.append((selection, chosen))
            log.info(
                "beat '%s': %d/%d candidates chosen",
                beat.name, len(chosen), len(candidates),
            )

        # 5. Sequence (+ 6. Critic, 7. Revision loop)
        selections_text_parts: list[str] = []
        for selection, chosen in selections:
            selections_text_parts.append(f"### beat: {selection.beat_name}")
            if not chosen:
                selections_text_parts.append("(no segments selected)")
            for u in chosen:
                selections_text_parts.append(describe_selection(u))
        selections_text = "\n".join(selections_text_parts)

        constraint_lines: list[str] = []
        if not reuse_allowed:
            constraint_lines.append(
                "Use at most ONE clip from each source file — never reuse a "
                "source video, even for a different moment."
            )
        if enforce_chronology:
            constraint_lines.append(
                "Order ALL clips strictly by recording time, oldest first."
            )
        constraints = "\n".join(constraint_lines)

        revision_notes = ""
        sequence: SequencePlan | None = None
        best: tuple[float, SequencePlan] | None = None  # (fitness, plan)
        critique = None
        for round_no in range(config.director.max_revision_loops + 1):
            tick(done=round_no, total=config.director.max_revision_loops + 1,
                 item=f"シーケンス生成と批評 (ラウンド{round_no + 1})")
            sequence = await edit_sequence(
                ai,
                story=story,
                beats=beats,
                selections_text=selections_text,
                segments_by_id=segments_by_id,
                revision_notes=revision_notes,
                constraints=constraints,
            )
            # Deterministic guarantees before the critic sees the draft, so
            # revision rounds compensate for any dropped/moved clips.
            if not reuse_allowed:
                sequence = dedupe_assets(sequence, segments_by_id)
            if enforce_chronology:
                # A user outline is authoritative for the overall order;
                # chronology then only applies within each flow section.
                sequence = sort_chronologically(
                    sequence, segments_by_id,
                    group_order=[b.name for b in beats.beats] if outline else None,
                )
            critique = await critique_edit(
                ai,
                story=story,
                plan=sequence,
                target_duration=target_duration,
                segments_by_id=segments_by_id,
            )
            log.info(
                "critic round %d: score=%d, revision_required=%s, issues=%d",
                round_no, critique.score, critique.revision_required, len(critique.issues),
            )

            # Deterministic duration guard: the critic judges story quality,
            # code guarantees the duration constraint (AGENT.md §2).
            actual = sum(c.source_out - c.source_in for c in sequence.clips)
            deviation = abs(actual - target_duration) / target_duration

            # Revision loops may get worse — keep the best draft, judged by
            # critic score minus a duration-deviation penalty.
            fitness = critique.score - 50.0 * deviation
            if best is None or fitness > best[0]:
                best = (fitness, sequence)

            duration_note = ""
            if deviation > 0.2:
                duration_note = (
                    f"The total duration is {actual:.0f}s but the target is "
                    f"{target_duration:.0f}s. "
                    + ("Trim clips or drop the weakest ones to reach the target."
                       if actual > target_duration
                       else "Extend the strongest clips to reach the target.")
                )
                critique.revision_required = True

            if not critique.revision_required:
                break
            revision_notes = "\n".join(
                note for note in (
                    critique.revision_notes,
                    "\n".join(
                        f"- ({i.severity}) {i.type}: {i.description}"
                        for i in critique.issues
                    ),
                    duration_note,
                ) if note
            )

        assert sequence is not None
        if best is not None:
            sequence = best[1]
        sequence = enforce_target_duration(sequence, target_duration)
        plan = sequence_to_edit_plan(
            sequence, story, intent,
            captions=captions or config.output.captions,
            caption_format=caption_format or config.output.caption_format,
            segments_by_id=segments_by_id,
        )
        want_subtitles = subtitles if subtitles is not None else config.output.subtitles
        if want_subtitles:
            plan = fill_subtitles(plan, memory)

        # Music selection (optional; the plan is complete without it)
        effective_music_dir = (
            music_dir if music_dir is not None else config.output.music_dir
        )
        if effective_music_dir is not None:
            tick(item="BGM選曲")
            tracks = list_music_tracks(Path(effective_music_dir))
            if tracks:
                try:
                    # Cached content analysis (BPM/tags/lyrics/description);
                    # tracks without a cache row stay filename-only.
                    annotate_tracks(tracks, memory)
                    tracks = rank_tracks_for_prompt(
                        tracks, memory, story=story, user_prompt=user_prompt,
                        clap_model=config.models.music_embedding.model,
                    )
                    choice = await select_music(
                        ai, story=story, user_prompt=user_prompt,
                        target_duration=target_duration, tracks=tracks,
                    )
                    plan.music = resolve_choice(
                        choice, tracks,
                        default_gain_db=config.output.music_gain_db,
                    )
                    if plan.music is not None:
                        log.info(
                            "music: %s (%s)", plan.music.file_name, plan.music.reason
                        )
                except Exception as exc:
                    log.warning("music selection skipped: %s", exc)
            else:
                log.info("music: no candidate files in %s", effective_music_dir)

        plan_id = memory.save_edit_plan(
            run_id, plan.model_dump_json(), version=1,
            name=(story.concept or user_prompt or "").strip()[:60] or None,
        )
        memory.finish_director_run(run_id, "done")
        log.info(
            "edit plan %s: %d clips, %.1fs total (prompt version %s)",
            plan_id, len(plan.clips), plan.total_duration, PROMPT_VERSION,
        )
        return plan_id, plan

    except Exception as exc:
        memory.finish_director_run(run_id, "failed", str(exc))
        raise
