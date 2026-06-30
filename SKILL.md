---
name: video-storyboard-slicer
description: Prepare video understanding context for GPT workflows from local videos or downloadable URLs. Use when Codex needs yt-dlp video download, adaptive timestamped storyboard sheets, ordered screenshots, manifests, Bilibili title/description/cover/comment metadata, local Whisper transcription with timestamped segments, text-driven focused region storyboard extraction around promising transcript/copy ranges, video summaries, highlight selection, thumbnail frame choice, UI recording review, a polished WeChat/public-account-ready HTML article page containing screenshots from the original video, or clean final packaging that removes engineering artifacts. Defaults adapt frame sampling and sheet layout to video duration.
---

# Video Storyboard Slicer

Use this skill to turn a local video or downloadable video URL into ordered screenshots, metadata, transcript inputs, and a local HTML summary artifact so a model can reason about more video time without directly ingesting the full video.

## Workflow

1. Confirm the source is a local video file or a URL the user is allowed to download/analyze.
2. For URLs or full context bundles, run `scripts/prepare_video_context.py`. It downloads with yt-dlp, fetches Bilibili metadata when applicable, runs storyboard extraction, runs local Whisper by default, and writes HTML-summary inputs.
3. For local frame-only work, run `scripts/make_storyboard.py` directly.
4. Treat the first storyboard route as mandatory visual understanding, not as a passive screenshot pool. Inspect `storyboard/sheets/storyboard_###.jpg`, `storyboard/manifest.json`, and `summary_context.json`, then use `visual_digest_prompt.md` to write `visual_digest.md` or `visual_digest.json`. This digest must summarize visible scene flow, visually promising timestamps, repetitive spans, and ranges that need transcript context.
5. If transcript/copy/comments suggest valuable regions that are not visually proven by the coarse storyboard, use `moment_selection_prompt.md` plus `visual_digest.md/json` to make `candidate_moments.json` with start/end ranges, then run `scripts/extract_moment_frames.py summary_context.json candidate_moments.json`. This second pass samples each chosen region into focused storyboard sheets instead of betting on one frame.
6. If the user requested a final summary page, use the generated `ai_html_prompt.md`, `summary_context.json`, `visual_digest.md/json`, and any `focused_moment_frames` to refine or replace `summary.html` as a WeChat/public-account-ready article, not a developer report. Include original-video screenshots from `selected_screenshots` and focused region frames that are visually useful. The final page must pass `scripts/check_article_html.py summary.html`.
7. If the user asks for a clean final deliverable, finish `summary.html`, run `scripts/check_article_html.py <output-dir>/summary.html`, run `scripts/package_summary.py <output-dir>` for a dry-run cleanup plan, then run `scripts/package_summary.py <output-dir> --apply`. The package step runs the article check and first-pass visual digest check again, copies only images referenced by `summary.html` into `assets/`, rewrites image paths, verifies references, and removes source downloads, manifests, transcripts, storyboard folders, focused-frame folders, prompts, and other engineering artifacts. Leave only `summary.html`, `assets/`, and `summary-long.png` when that long screenshot exists.

Generated outputs:

- `source/`: yt-dlp downloaded video and thumbnail/info files for URL inputs
- `metadata/ytdlp_info.json`: yt-dlp metadata when URL input is used
- `metadata/bilibili.json`: Bilibili API metadata and first comments when available
- `metadata/cover.jpg`: downloaded Bilibili/yt-dlp cover when available
- `storyboard/sheets/storyboard_###.jpg`: ordered frame grids with visible timestamps
- `storyboard/frames/frame_*.jpg`: extracted original-video screenshots
- `storyboard/manifest.json`: source metadata, adaptive config, timestamps, frame paths, and sheet coverage
- `transcript/`: local Whisper outputs, including `transcript_segments.json` when timestamped segments are available
- `summary_context.json`: compact context for AI analysis and HTML generation
- `visual_digest_prompt.md`: prompt for converting the first storyboard route into a visual understanding digest
- `visual_digest.md` / `visual_digest.json`: mandatory first-pass visual digest written after inspecting all storyboard sheets
- `ai_html_prompt.md`: prompt for making the final summary page
- `moment_selection_prompt.md`: prompt for choosing transcript/copy ranges that deserve focused region storyboard extraction
- `focused_frames/`: optional second-pass region frames, focused storyboard sheets, manifest, and frame-selection prompt
- `summary.html`: initial local HTML article scaffold containing source screenshots; refine it into a polished public article for final delivery
- `assets/`: optional final-only image folder when packaging a clean HTML deliverable
- `summary-long.png`: optional long screenshot of the final article for sharing/preview

## Quick Start

```bash
python3 /path/to/video-storyboard-slicer/scripts/prepare_video_context.py "https://www.bilibili.com/video/BV..." --output ./video-context
```

For a local file:

```bash
python3 /path/to/video-storyboard-slicer/scripts/prepare_video_context.py input.mp4 --output ./video-context
```

For fast visual-only scanning without local Whisper:

```bash
python3 /path/to/video-storyboard-slicer/scripts/prepare_video_context.py input.mp4 --output ./video-context --no-transcribe
```

For storyboard sheets only:

```bash
python3 /path/to/video-storyboard-slicer/scripts/make_storyboard.py input.mp4 --output ./storyboard-out
```

For text-driven supplemental region extraction:

```bash
python3 /path/to/video-storyboard-slicer/scripts/extract_moment_frames.py ./video-context/summary_context.json ./video-context/candidate_moments.json
```

For final cleanup after `summary.html` is polished:

```bash
python3 /path/to/video-storyboard-slicer/scripts/check_article_html.py ./video-context/summary.html
python3 /path/to/video-storyboard-slicer/scripts/package_summary.py ./video-context
python3 /path/to/video-storyboard-slicer/scripts/package_summary.py ./video-context --apply
```

By default, the scripts probe duration and automatically choose compact overview settings:

- frame interval for reading the video
- thumbnail width
- sheet columns
- max frames per sheet
- sheet time span
- target total frames

The default overview favors seeing more of the video at lower visual precision: for a one-hour video, the balanced target is about 800 frames, usually around 10 long-video storyboard sheets at 80 frames per sheet. Use this to understand rough visual flow before asking for high-resolution evidence.

Use `--dry-run` to preview the adaptive configuration without writing frames:

```bash
python3 /path/to/video-storyboard-slicer/scripts/make_storyboard.py input.mp4 --dry-run
```

## URL And Bilibili Workflow

Use `prepare_video_context.py` for URL inputs. It uses yt-dlp for download and info extraction. For Bilibili videos, it best-effort calls Bilibili web APIs to fetch title, description, cover, owner, stats, pages, aid/cid, and the first comments.

Useful options:

- `--cookies path/to/cookies.txt` or `--cookies-from-browser chrome` for yt-dlp.
- `--comments 12` to change how many Bilibili comments to fetch.
- `--no-bilibili-api` when only yt-dlp metadata is desired.
- `--bvid BV...` or `--aid 123` when analyzing a local file and still fetching Bilibili metadata.
- `BILI_COOKIE` environment variable or `--bili-cookie-file` for Bilibili API calls that need logged-in cookies.

## Adaptive Controls

- Use `--density coarse` for long-video overviews or cheap first passes.
- Use `--density balanced` for the default readable overview.
- Use `--density dense` for reactions, UI details, dance/motion, or candidate highlight ranges.
- Use `--max-total-frames N` to cap adaptive frame count.
- Override any adaptive value with `--interval`, `--cols`, `--max-frames-per-sheet`, `--segment-seconds`, or `--thumb-width`.

Examples:

```bash
# Generic first pass
python3 scripts/make_storyboard.py lecture.mp4 --output ./lecture-board

# Long video coarse scan
python3 scripts/make_storyboard.py recording.mp4 --density coarse

# Candidate highlight or movement-heavy segment
python3 scripts/make_storyboard.py clip.mp4 --density dense --thumb-width 480
```

## Transcript Rule

Use local Whisper for video copy/transcript reading. `prepare_video_context.py` runs local Whisper by default and writes raw Whisper files plus structured timestamped segments when available.

Useful options:

- `--whisper-model tiny|base|small|medium|large`
- `--whisper-language auto|zh|en|ja|...`; `auto` omits `--language` and lets Whisper detect
- `--whisper-command /path/to/whisper` when the CLI is not on PATH
- `--whisper-device auto|mps|cuda|cpu`; `auto` prefers CUDA, then Apple MPS, then CPU
- `--whisper-fp16 auto|True|False`; `auto` uses `False` on CPU and `True` elsewhere
- `--whisper-initial-prompt "..."` for names, jargon, or domain terms
- `--whisper-threads N` for CPU tuning
- `--no-transcribe` for visual-only scans

Defaults can also be set with environment variables:

```bash
export VIDEO_STORYBOARD_WHISPER_MODEL=small
export VIDEO_STORYBOARD_WHISPER_LANGUAGE=auto
export VIDEO_STORYBOARD_WHISPER_DEVICE=auto
```

Persistent defaults live in `config/defaults.json`. Edit that file when the user's standing preference changes. Use one-off CLI flags when the language is only known for the current video.

If `auto` chooses MPS and Whisper fails, the script retries on CPU with `fp16 False`.

Do not treat `audio.wav` as model-readable context by itself. The useful model input is the local Whisper transcript, especially `transcript/transcript_segments.json` and `transcript_segments_preview` in `summary_context.json`.

## Analysis

For general video understanding, ask for summary, key timestamps, visual changes, unclear sections, and recommended rerun settings.

For editing/highlight tasks, ask for hook, keep/cut ranges, peak moment, thumbnail frame, text/effect notes, and transcript needs.

Three-route understanding model:

1. First visual route: storyboard sheets see the whole video. Write `visual_digest.md` or `visual_digest.json` before any final selection.
2. Transcript route: Whisper text and metadata explain what is said and why viewers care.
3. Focused visual route: transcript plus `visual_digest` selects ranges for `extract_moment_frames.py` so important moments are seen more clearly.

For first-pass visual digestion:

1. Read `visual_digest_prompt.md`, `summary_context.json`, and `storyboard/manifest.json`.
2. Inspect every `storyboard/sheets/storyboard_###.jpg` in order.
3. Write `visual_digest.md` and preferably `visual_digest.json` with overall visual type, per-sheet visible summaries, promising timestamps, repetitive spans, questions for text/audio, and recommended focused reruns.
4. Do not select transcript moments or write the final HTML until this digest exists.

For text-driven focused extraction:

1. Read `summary_context.json`, `visual_digest.md/json`, `transcript_segments_path`, comments, title, and description.
2. Use `moment_selection_prompt.md` to write `candidate_moments.json` with start/end ranges, not only single timestamps. Expand ranges enough to cover the likely visual action.
3. Run `extract_moment_frames.py` to sample each selected transcript/copy region into focused storyboard sheets. Defaults capture up to 36 compact frames per region with surrounding context; use `--interval`, `--frames-per-moment`, `--cols`, `--max-frames-per-sheet`, and `--thumb-width` to tune.
4. Inspect `focused_frames/sheets/focused_frames_###.jpg` as mini storyboards, then inspect individual frames only for the best visual evidence.
5. Use `focused_frames/final_frame_selection_prompt.md` to decide which regions and frames actually support the final summary.

For HTML summary pages, read `summary_context.json`, `visual_digest.md/json`, and `ai_html_prompt.md`, then create or refine `summary.html`. The final page must include original-video screenshots from `selected_screenshots`, not only the cover image. If `focused_moment_frames` exists, inspect its focused region sheets first and prefer visually confirmed frames or short visual progressions for important claims.

Write the final HTML as a WeChat/public-account-ready article:

- Treat `summary.html` as a reader-facing article from the first draft. Do not preserve scaffold sections, headings, lists, or wording that expose the workflow.
- Use a catchy shareable headline, strong subtitle/deck, hooky lead paragraph, crisp section headings, image blocks with captions, pull quotes/key sentences, and an emotionally satisfying ending.
- Make the page feel like a finished public article that can be uploaded directly to a WeChat official account. Do not make it feel like a workflow report, analysis memo, or internal deliverable.
- Explain the video's content and why the selected moments matter in natural prose; do not merely list metadata, transcripts, frame counts, JSON links, or artifact paths.
- Treat Bilibili title, description, comments, and local Whisper text as interpretation context, then turn them into an engaging narrative.
- Use screenshots as visual evidence inside the narrative. Captions should explain what the frame proves or why it is useful for editing.
- Do not include developer-facing, workflow-facing, or creator-backstage advice such as "how to edit", "clip suggestions", "artifact list", "manifest", "transcript files", raw JSON, generated paths, frame counts, or exhaustive file links unless the user explicitly asks for those.
- Avoid headings like "video understanding report", "summary", "analysis", or "artifacts" in the final article; prefer reader-facing headlines and magazine-style sections.
- Keep paths relative and local-file friendly; do not require a server or external assets.

Never ship these visible headings or phrases in the final HTML: `Source Screenshots`, `Focused Text-Moment Frames`, `Storyboard Sheets`, `Transcript Excerpt`, `Metadata`, `Artifacts`, `Manifest`, `summary_context.json`, `ai_html_prompt.md`, `selected_screenshots`, `frame_count`, `工程总结`, `工作流报告`, `元数据`, or `转录节选`.

When final packaging is requested, run `scripts/check_article_html.py <output-dir>/summary.html` first. Then run `scripts/package_summary.py <output-dir>` and inspect the dry-run plan. Then run `scripts/package_summary.py <output-dir> --apply`; it must leave only `summary.html`, `assets/`, and optional `summary-long.png`, with every local image reference in `summary.html` pointing into `assets/`. If `package_summary.py --apply` fails the article check or visual digest check, create the missing visual digest or rewrite the HTML into a public article and rerun it.

Read `references/video-analysis.md` when the task needs more interpretation guidance after generation.

## Quality Checks

- Open the first and last sheet; confirm timestamps are readable and frame order is correct.
- Confirm `visual_digest.md` or `visual_digest.json` exists before selecting candidate moments or packaging a final page.
- Check `manifest.json` for `adaptive_config` so the sampling choice is explicit.
- If details are unreadable, rerun with `--density dense`, larger `--thumb-width`, fewer columns, or a shorter source clip.
- If too many sheets are produced, rerun with `--density coarse` or `--max-total-frames`.
- If the moment depends on audio, add transcript/subtitles before final conclusions.
- Before packaging, run `scripts/check_article_html.py <output-dir>/summary.html`; if it fails, rewrite the page instead of delivering a dry workflow summary.
