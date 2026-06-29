# Video Storyboard Analysis Notes

Load this reference only when the task needs stronger interpretation guidance after generating storyboard sheets.

## Use The Sheets For

- Visual summarization: identify scene changes, actions, UI states, slides, gestures, and readable on-screen text.
- Editing prep: find hooks, chapter boundaries, thumbnail frames, reaction shots, dead air, and fast visual changes.
- QA/review: scan for missing sections, layout problems, visual glitches, or unclear frames.
- Multimodal handoff: pair sheet timestamps with local Whisper transcript segments so a model can reason about both image and speech.

## Readability Heuristics

- If faces, captions, dashboards, or code are unreadable, rerun with larger thumbnails or fewer columns.
- If movement matters, rerun with denser sampling or manually crop the time range before generating sheets.
- If speech timing matters, do not rely on sheets alone; use local Whisper and read `transcript/transcript_segments.json`.
- For long videos, use the adaptive default first for coarse review, then rerun a candidate range with `--density dense`.

## Text-Driven Focused Frames

Use the focused-frame loop when transcript/copy/comment context suggests a moment could be visually valuable but the coarse storyboard is too sparse to verify it.

Recommended loop:

1. Read `summary_context.json` and local Whisper `transcript_segments_path`.
2. Produce `candidate_moments.json` with 5-15 timestamped moments.
3. Run `scripts/extract_moment_frames.py summary_context.json candidate_moments.json`.
4. Review `focused_frames/sheets/focused_frames_###.jpg`.
5. Keep only frames that visibly support the summary, edit, or thumbnail decision.

Candidate moment JSON:

```json
{
  "moments": [
    {
      "start": "00:01:23.40",
      "end": "00:01:28.00",
      "text": "short transcript quote",
      "reason": "why this may be visually useful",
      "priority": 1
    }
  ]
}
```

Use comments and descriptions to propose candidates, but use local Whisper segments for timestamped text and require video frames before making visual claims.

## Output Shape

For generic video analysis, return:

```text
Summary:
Key timestamps:
Notable visual changes:
Candidate thumbnails/reference frames:
Focused frames to keep:
Unclear sections:
Recommended rerun settings:
```

For highlight editing, return:

```text
Title/angle:
Duration target:
Opening hook:
Keep:
Cut:
Cover frame:
Text/effects:
Audio/transcript needed:
```

## Source Context

When `prepare_video_context.py` is used, treat `summary_context.json` as the source of truth for:

- downloaded/local video path
- yt-dlp metadata
- Bilibili title, description, cover, stats, pages, and comments when available
- storyboard sheets and selected original-video screenshots
- local Whisper transcript files, timestamped segments, and excerpt

Use comments and descriptions as context, not as verified facts about what appears in the video. Use screenshots/storyboards as visual evidence.
