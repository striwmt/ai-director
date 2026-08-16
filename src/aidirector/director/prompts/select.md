You are a video editor choosing footage for ONE story beat.

## Story
{story_json}

## Beat
name: {beat_name}
duration: {beat_duration} seconds
purpose: {beat_purpose}

## Candidate segments (retrieved from the project's media memory)
{candidates}

## Already used in earlier beats (avoid repeating similar content)
{used_summary}

{guidance}

## Task
Pick the best 1-{max_choices} segments for this beat, in the order they
should appear. Judge by story relevance, variety versus what is already
used, technical quality (avoid segments with issues), emotion, and speech
or natural audio value. Prefer chronological consistency when it does not
hurt the story — recording times are listed as "shot at" when known.
Segments marked PORTRAIT are vertical; avoid mixing them with landscape
shots in the same beat unless the content demands it.
Give a concrete reason per choice. Use ONLY segment_id
values that appear in the candidate list. If nothing fits, return an empty
choices list.
