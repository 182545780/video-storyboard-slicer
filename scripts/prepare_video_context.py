#!/usr/bin/env python3
"""Prepare downloaded video context, storyboard sheets, metadata, transcript, and HTML report inputs."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


BVID_RE = re.compile(r"\bBV[0-9A-Za-z]{10}\b")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".flv"}
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "defaults.json"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
BILIBILI_ORIGIN = "https://www.bilibili.com"
BILIBILI_DOMAINS = ("bilibili.com", "b23.tv")


def is_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"}


def is_bilibili_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    host = parsed.netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in BILIBILI_DOMAINS)


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise SystemExit(f"Command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(path: Path | None) -> dict[str, Any]:
    config_path = path or Path(os.environ.get("VIDEO_STORYBOARD_CONFIG", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        return {}
    try:
        data = load_json(config_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read config file {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Config file must contain a JSON object: {config_path}")
    return data


def nested_get(data: dict[str, Any], keys: tuple[str, ...], fallback: Any) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return fallback
        current = current[key]
    return current


def configured_default(config: dict[str, Any], keys: tuple[str, ...], env_name: str, fallback: Any) -> Any:
    if env_name in os.environ:
        return os.environ[env_name]
    return nested_get(config, keys, fallback)


def configured_optional_int(config: dict[str, Any], keys: tuple[str, ...], env_name: str) -> int | None:
    value = configured_default(config, keys, env_name, None)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{env_name} / config value must be an integer: {value!r}") from exc


def rel(path: str | Path | None, base: Path) -> str | None:
    if path is None:
        return None
    return os.path.relpath(Path(path), base).replace(os.sep, "/")


def read_cookie_header(args: argparse.Namespace) -> str | None:
    if args.bili_cookie_file:
        return Path(args.bili_cookie_file).read_text(encoding="utf-8").strip()
    return os.environ.get("BILI_COOKIE")


def http_json(url: str, params: dict[str, Any], referer: str | None, cookie: str | None) -> dict[str, Any]:
    full_url = url + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"User-Agent": BROWSER_USER_AGENT}
    if referer:
        headers["Referer"] = referer
        if "bilibili.com" in referer:
            headers["Origin"] = BILIBILI_ORIGIN
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(full_url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def download_binary(url: str, out_path: Path, referer: str | None, cookie: str | None) -> Path | None:
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    headers = {"User-Agent": BROWSER_USER_AGENT}
    if referer:
        headers["Referer"] = referer
        if "bilibili.com" in referer:
            headers["Origin"] = BILIBILI_ORIGIN
    if cookie:
        headers["Cookie"] = cookie
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as response:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(response.read())
        return out_path
    except Exception:
        return None


def find_bvid(source_text: str, info: dict[str, Any] | None, explicit_bvid: str | None) -> str | None:
    if explicit_bvid:
        return explicit_bvid
    candidates = [source_text]
    if info:
        for key in ("id", "display_id", "webpage_url", "original_url"):
            value = info.get(key)
            if isinstance(value, str):
                candidates.append(value)
    for candidate in candidates:
        match = BVID_RE.search(candidate)
        if match:
            return match.group(0)
    return None


def first_int(*values: Any) -> int | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def compact_ytdlp_info(info: dict[str, Any] | None) -> dict[str, Any]:
    if not info:
        return {}
    keys = [
        "id",
        "display_id",
        "title",
        "description",
        "duration",
        "webpage_url",
        "original_url",
        "thumbnail",
        "uploader",
        "uploader_id",
        "channel",
        "channel_id",
        "upload_date",
        "timestamp",
        "view_count",
        "like_count",
        "comment_count",
    ]
    return {key: info.get(key) for key in keys if info.get(key) is not None}


def choose_downloaded_video(download_dir: Path, info: dict[str, Any], prepared_filename: Path | None) -> Path:
    for item in info.get("requested_downloads") or []:
        filepath = item.get("filepath")
        if filepath and Path(filepath).exists():
            return Path(filepath)
    if prepared_filename and prepared_filename.exists():
        return prepared_filename
    candidates = [path for path in download_dir.iterdir() if path.suffix.lower() in VIDEO_EXTENSIONS]
    if not candidates:
        raise SystemExit("yt-dlp completed but no downloaded video file was found.")
    return max(candidates, key=lambda path: path.stat().st_size)


def download_with_ytdlp(source: str, output: Path, args: argparse.Namespace) -> tuple[Path, dict[str, Any], Path]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise SystemExit("Missing Python package: yt-dlp. Install it with `python3 -m pip install yt-dlp`.") from exc

    download_dir = output / "source"
    metadata_dir = output / "metadata"
    download_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts: dict[str, Any] = {
        "outtmpl": {"default": str(download_dir / "%(id)s.%(ext)s")},
        "noplaylist": True,
        "writeinfojson": True,
        "writethumbnail": True,
        "merge_output_format": "mp4",
        "quiet": False,
        "retries": 3,
    }
    if is_bilibili_url(source):
        ydl_opts["http_headers"] = {
            "User-Agent": BROWSER_USER_AGENT,
            "Referer": BILIBILI_ORIGIN + "/",
            "Origin": BILIBILI_ORIGIN,
        }
    if args.ytdlp_comments:
        ydl_opts["getcomments"] = True
    if args.cookies:
        ydl_opts["cookiefile"] = str(args.cookies)
    if args.cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = tuple(part for part in args.cookies_from_browser.split(":") if part)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(source, download=True)
        sanitized = ydl.sanitize_info(info)
        prepared_filename = Path(ydl.prepare_filename(info))

    info_path = metadata_dir / "ytdlp_info.json"
    write_json(info_path, sanitized)
    video_path = choose_downloaded_video(download_dir, sanitized, prepared_filename)
    return video_path, sanitized, info_path


def normalize_comment(reply: dict[str, Any]) -> dict[str, Any]:
    member = reply.get("member") or {}
    content = reply.get("content") or {}
    return {
        "rpid": reply.get("rpid"),
        "user": member.get("uname"),
        "message": content.get("message"),
        "like_count": reply.get("like"),
        "reply_count": reply.get("rcount"),
        "ctime": reply.get("ctime"),
    }


def fetch_bilibili_comments(aid: int, max_comments: int, referer: str, cookie: str | None) -> dict[str, Any]:
    endpoints = [
        (
            "https://api.bilibili.com/x/v2/reply/main",
            {"type": 1, "oid": aid, "mode": 3, "next": 0, "ps": max(1, min(max_comments, 20)), "plat": 1},
        ),
        (
            "https://api.bilibili.com/x/v2/reply",
            {"type": 1, "oid": aid, "sort": 2, "pn": 1, "ps": max(1, min(max_comments, 20))},
        ),
    ]
    errors = []
    for url, params in endpoints:
        try:
            payload = http_json(url, params, referer=referer, cookie=cookie)
            if payload.get("code") != 0:
                errors.append({"endpoint": url, "code": payload.get("code"), "message": payload.get("message")})
                continue
            data = payload.get("data") or {}
            raw_replies = (data.get("top_replies") or []) + (data.get("replies") or [])
            comments = []
            seen = set()
            for reply in raw_replies:
                rpid = reply.get("rpid")
                if rpid in seen:
                    continue
                seen.add(rpid)
                comments.append(normalize_comment(reply))
                if len(comments) >= max_comments:
                    break
            return {"comments": comments, "endpoint": url, "errors": errors}
        except Exception as exc:
            errors.append({"endpoint": url, "error": f"{type(exc).__name__}: {exc}"})
    return {"comments": [], "endpoint": None, "errors": errors}


def fetch_bilibili_context(
    source_text: str,
    info: dict[str, Any] | None,
    output: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    cookie = read_cookie_header(args)
    bvid = find_bvid(source_text, info, args.bvid)
    aid = first_int(args.aid, info.get("aid") if info else None)
    referer = args.source_url or (f"https://www.bilibili.com/video/{bvid}" if bvid else None)
    context: dict[str, Any] = {"bvid": bvid, "aid": aid, "enabled": not args.no_bilibili_api, "errors": []}
    if args.no_bilibili_api:
        return context
    if not bvid and not aid:
        context["errors"].append("No BV id or aid found for Bilibili API lookup.")
        return context

    try:
        params = {"bvid": bvid} if bvid else {"aid": aid}
        payload = http_json("https://api.bilibili.com/x/web-interface/view", params, referer=referer, cookie=cookie)
        if payload.get("code") != 0:
            context["errors"].append({"view_code": payload.get("code"), "message": payload.get("message")})
        else:
            view = payload.get("data") or {}
            context.update(
                {
                    "bvid": view.get("bvid") or bvid,
                    "aid": view.get("aid") or aid,
                    "cid": view.get("cid"),
                    "title": view.get("title"),
                    "description": view.get("desc"),
                    "cover_url": view.get("pic"),
                    "duration_seconds": view.get("duration"),
                    "owner": view.get("owner"),
                    "stats": view.get("stat"),
                    "pages": view.get("pages"),
                    "pubdate": view.get("pubdate"),
                }
            )
    except Exception as exc:
        context["errors"].append({"view_error": f"{type(exc).__name__}: {exc}"})

    cover_url = context.get("cover_url") or (info or {}).get("thumbnail")
    if cover_url:
        cover_path = download_binary(str(cover_url), output / "metadata" / "cover.jpg", referer=referer, cookie=cookie)
        if cover_path:
            context["cover_path"] = str(cover_path)

    if context.get("aid") and args.comments > 0:
        comment_result = fetch_bilibili_comments(int(context["aid"]), args.comments, referer=referer or "", cookie=cookie)
        context["comments"] = comment_result.get("comments", [])
        context["comments_endpoint"] = comment_result.get("endpoint")
        if comment_result.get("errors"):
            context["comment_errors"] = comment_result["errors"]

    write_json(output / "metadata" / "bilibili.json", context)
    return context


def run_storyboard(video_path: Path, output: Path, args: argparse.Namespace, need_audio: bool) -> dict[str, Any]:
    script_path = Path(__file__).with_name("make_storyboard.py")
    storyboard_dir = output / "storyboard"
    cmd = [
        sys.executable,
        str(script_path),
        str(video_path),
        "--output",
        str(storyboard_dir),
        "--density",
        args.density,
    ]
    optional_args = [
        ("--max-total-frames", args.max_total_frames),
        ("--interval", args.interval),
        ("--cols", args.cols),
        ("--max-frames-per-sheet", args.max_frames_per_sheet),
        ("--segment-seconds", args.segment_seconds),
        ("--thumb-width", args.thumb_width),
    ]
    for flag, value in optional_args:
        if value is not None:
            cmd.extend([flag, str(value)])
    if need_audio:
        cmd.append("--extract-audio")
    run(cmd)
    return load_json(storyboard_dir / "manifest.json")


def run_whisper(audio_path: Path | None, output: Path, args: argparse.Namespace) -> dict[str, Any]:
    transcript_dir = output / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    if not args.transcribe:
        return {"enabled": False, "source": "local-whisper"}
    if not audio_path or not audio_path.exists():
        return {"enabled": True, "source": "local-whisper", "status": "missing-audio"}
    whisper_cmd = shutil.which(args.whisper_command)
    if not whisper_cmd:
        note = transcript_dir / "transcription_needed.txt"
        note.write_text("Local Whisper CLI was not found. Install whisper or rerun with --whisper-command.\n", encoding="utf-8")
        return {"enabled": True, "source": "local-whisper", "status": "missing-whisper", "note_path": str(note)}

    whisper_device = resolve_whisper_device(args.whisper_device)
    whisper_fp16 = resolve_whisper_fp16(args.whisper_fp16, whisper_device)
    cmd = build_whisper_command(whisper_cmd, audio_path, transcript_dir, args, whisper_device, whisper_fp16)
    fallback_used = False
    files: list[str] = []
    try:
        run(cmd)
        files = sorted(str(path) for path in transcript_dir.iterdir() if path.is_file())
        if not files and args.whisper_device == "auto" and whisper_device != "cpu":
            whisper_device = "cpu"
            whisper_fp16 = "False"
            fallback_used = True
            run(build_whisper_command(whisper_cmd, audio_path, transcript_dir, args, whisper_device, whisper_fp16))
            files = sorted(str(path) for path in transcript_dir.iterdir() if path.is_file())
    except SystemExit:
        if args.whisper_device == "auto" and whisper_device != "cpu":
            whisper_device = "cpu"
            whisper_fp16 = "False"
            fallback_used = True
            run(build_whisper_command(whisper_cmd, audio_path, transcript_dir, args, whisper_device, whisper_fp16))
            files = sorted(str(path) for path in transcript_dir.iterdir() if path.is_file())
        else:
            raise
    return {
        "enabled": True,
        "source": "local-whisper",
        "status": "ok" if files else "empty-output",
        "command": whisper_cmd,
        "model": args.whisper_model,
        "language": args.whisper_language,
        "device": whisper_device,
        "fp16": whisper_fp16,
        "fallback_used": fallback_used,
        "initial_prompt": args.whisper_initial_prompt,
        "threads": args.whisper_threads,
        "files": files,
    }


def build_whisper_command(
    whisper_cmd: str,
    audio_path: Path,
    transcript_dir: Path,
    args: argparse.Namespace,
    whisper_device: str,
    whisper_fp16: str,
) -> list[str]:
    cmd = [
        whisper_cmd,
        str(audio_path),
        "--model",
        args.whisper_model,
        "--device",
        whisper_device,
        "--output_dir",
        str(transcript_dir),
        "--output_format",
        "all",
        "--fp16",
        whisper_fp16,
    ]
    if args.whisper_language and args.whisper_language != "auto":
        cmd.extend(["--language", args.whisper_language])
    if args.whisper_initial_prompt:
        cmd.extend(["--initial_prompt", args.whisper_initial_prompt])
    if args.whisper_threads:
        cmd.extend(["--threads", str(args.whisper_threads)])
    return cmd


def resolve_whisper_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def resolve_whisper_fp16(requested: str, device: str) -> str:
    if requested != "auto":
        return requested
    return "False" if device == "cpu" else "True"


def select_screenshots(storyboard_manifest: dict[str, Any], count: int) -> list[dict[str, Any]]:
    frames = storyboard_manifest.get("frames") or []
    if not frames:
        return []
    if len(frames) <= count:
        return frames
    positions = []
    for index in range(count):
        pos = round(index * (len(frames) - 1) / max(count - 1, 1))
        positions.append(pos)
    selected = []
    seen = set()
    for pos in positions:
        if pos in seen:
            continue
        seen.add(pos)
        selected.append(frames[pos])
    return selected


def load_whisper_segments(transcription: dict[str, Any]) -> list[dict[str, Any]]:
    for file_path in transcription.get("files") or []:
        path = Path(file_path)
        if path.suffix.lower() != ".json" or not path.exists():
            continue
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        segments = []
        for index, segment in enumerate(data.get("segments") or [], start=1):
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            start = segment.get("start")
            end = segment.get("end")
            try:
                start_seconds = float(start)
                end_seconds = float(end)
            except (TypeError, ValueError):
                continue
            segments.append(
                {
                    "index": index,
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                    "start": format_seconds(start_seconds),
                    "end": format_seconds(end_seconds),
                    "text": text,
                }
            )
        if segments:
            return segments
    return []


def format_seconds(seconds: float) -> str:
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    fraction = seconds - total
    suffix = f"{fraction:.2f}"[1:].rstrip("0") if fraction > 0.001 else ""
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}{suffix}"
    return f"{minutes:02d}:{secs:02d}{suffix}"


def transcript_text_from_segments(segments: list[dict[str, Any]], limit: int = 2500) -> str:
    parts = [f"[{item['start']} - {item['end']}] {item['text']}" for item in segments]
    return "\n".join(parts)[:limit]


def first_transcript_text(transcription: dict[str, Any], segments: list[dict[str, Any]], limit: int = 2500) -> str:
    if segments:
        return transcript_text_from_segments(segments, limit=limit)
    for file_path in transcription.get("files") or []:
        path = Path(file_path)
        if path.suffix.lower() == ".txt" and path.exists():
            return path.read_text(encoding="utf-8", errors="replace")[:limit]
    return ""


def write_transcript_segments(output: Path, segments: list[dict[str, Any]]) -> Path | None:
    if not segments:
        return None
    segments_path = output / "transcript" / "transcript_segments.json"
    write_json(segments_path, {"segments": segments})
    return segments_path


def write_visual_digest_prompt(output: Path, context_path: Path) -> Path:
    prompt_path = output / "visual_digest_prompt.md"
    prompt_path.write_text(
        f"""Create the first-pass visual digest before selecting text moments or writing the final article.

Input:
- Context JSON: {context_path.name}
- Storyboard sheets: `storyboard/sheets/storyboard_###.jpg`
- Storyboard manifest: `storyboard/manifest.json`

Task:
Inspect every storyboard sheet in order. Treat this as the first route of video understanding: it should answer "what can be seen across the whole video?" before transcript-driven reasoning narrows the focus.

Write `visual_digest.md` in the output directory with these sections:

1. Overall visual type
   - Identify whether this is interview, gameplay, tutorial, lecture, livestream, reaction, vlog, product demo, stage/performance, screen recording, or mixed.
2. Timeline visual map
   - For each storyboard sheet, summarize the visible scene, scene changes, onscreen text/UI, people/characters, objects, and notable visual rhythm.
3. Visually promising timestamps
   - List timestamps or short ranges that look useful for final screenshots, thumbnails, transitions, or article evidence. Explain why each is visually useful.
4. Weak or repetitive spans
   - Identify stretches where the picture is repetitive and transcript should carry more weight.
5. Questions for text/audio
   - Note any visual moments that need Whisper text or comments to understand.
6. Recommended focused reruns
   - Suggest time ranges that deserve `extract_moment_frames.py` second-pass region storyboards.

Also write `visual_digest.json` with this shape:

```json
{{
  "overall_visual_type": "gameplay/tutorial/interview/etc",
  "sheet_notes": [
    {{
      "sheet": "storyboard/sheets/storyboard_001.jpg",
      "start": "00:00",
      "end": "03:20",
      "visible_summary": "what is visible",
      "scene_changes": ["major visual changes"],
      "promising_timestamps": [
        {{"timestamp": "00:42", "reason": "why this frame/range matters visually"}}
      ],
      "needs_text_context": false
    }}
  ],
  "recommended_focused_ranges": [
    {{"start": "00:42", "end": "01:05", "reason": "why to rerun as a focused storyboard"}}
  ],
  "avoid_or_low_value_ranges": [
    {{"start": "02:10", "end": "03:00", "reason": "repetitive/static"}}
  ]
}}
```

Rules:
- Do not use transcript or comments as the main evidence for this pass; this pass is for visual understanding.
- Use transcript only to mark "needs text context" when the picture alone is ambiguous.
- Do not skip sheets. If a sheet is repetitive, say so explicitly.
- Later steps must read `visual_digest.md` before selecting candidate moments or writing `summary.html`.
""",
        encoding="utf-8",
    )
    return prompt_path


def write_ai_prompt(output: Path, context_path: Path, summary_path: Path) -> Path:
    prompt_path = output / "ai_html_prompt.md"
    prompt_path.write_text(
        f"""Create or refine the final WeChat/public-account-ready HTML article page.

Inputs:
- Context JSON: {context_path.name}
- Current HTML scaffold: {summary_path.name}
- Storyboard sheets and selected source screenshots are referenced in the context JSON.
- Required first-pass visual digest: `visual_digest.md` or `visual_digest.json`.

Requirements:
1. Before writing or refining the page, read `visual_digest.md` or `visual_digest.json`. If neither exists, create it from `visual_digest_prompt.md` by inspecting all storyboard sheets first.
2. Write a polished standalone HTML page at `{summary_path.name}` that can be uploaded directly as a WeChat official account article.
3. Make the page read like a finished public article for readers: catchy shareable headline, strong subtitle/deck, hooky lead, narrative sections, image evidence, captions, pull quotes/key sentences, and a satisfying ending. It must not read like a data report, workflow report, or engineering summary.
4. Include original video screenshots from the `selected_screenshots` list; keep image paths relative.
   If `focused_moment_frames` exists, inspect its region storyboard sheets first, then choose visually useful frames or a short visual progression from that focused set.
5. Use the visual digest as the source of truth for visible scene flow, useful screenshots, repetitive sections, and places where visuals need text context.
6. Explain what happens in the video and why the selected moments matter. Turn title, description, comments, visual digest, and transcript into engaging natural prose, not bullet-point analysis.
7. Do not expose developer-only or backstage artifacts such as raw JSON links, manifest paths, frame counts, exhaustive transcript files, implementation notes, clip/editing suggestions, or generated file paths unless the user explicitly asks.
8. Distinguish visual evidence from transcript/comment context.
9. Do not invent details that are not supported by the screenshots, visual digest, metadata, comments, or transcript.
10. Keep the page local-file friendly; do not require a server or external assets.
11. If the user wants a clean final deliverable after the HTML is polished, run `scripts/package_summary.py <output-dir>` to review the cleanup plan, then run it again with `--apply`. The final directory should contain only `summary.html`, `assets/`, and optional `summary-long.png`.
12. Avoid final-page headings like "video understanding report", "summary", "analysis", "artifacts", or "clip suggestions"; use reader-facing magazine/public-account-style headings.
13. Before final packaging, run `scripts/check_article_html.py {summary_path.name}`. If it reports dry engineering language, rewrite the page and run the check again.

Hard fail patterns for the final page:
- Headings such as "Source Screenshots", "Focused Text-Moment Frames", "Storyboard Sheets", "Comments", "Transcript Excerpt", "Metadata", "Artifacts", "Manifest", or "Analysis Report".
- Visible raw paths, JSON filenames, frame counts, tool commands, or workflow/debug notes.
- A page that mainly lists facts or timestamps without a narrative point of view.
""",
        encoding="utf-8",
    )
    return prompt_path


def write_moment_selection_prompt(output: Path, context_path: Path) -> Path:
    prompt_path = output / "moment_selection_prompt.md"
    prompt_path.write_text(
        f"""Select text-driven regions that deserve focused video storyboard extraction.

Input:
- Context JSON: {context_path.name}
- Required first-pass visual digest: `visual_digest.md` or `visual_digest.json`

Before selecting moments, read `visual_digest.md` or `visual_digest.json`. If neither exists, create it from `visual_digest_prompt.md` by inspecting every storyboard sheet first. The first-pass visual digest tells you which ranges are visually promising, repetitive, ambiguous, or in need of text context.

Then read the title, description, comments, and local Whisper transcript data referenced in the context. Prefer `transcript_segments_path` because it contains timestamped Whisper segments. Pick only regions where text importance and visual potential overlap: a reaction, action, object, UI state, visual joke, scene change, demonstration, or emotionally important moment that the first-pass visual map suggests may be useful.

Write a JSON file named `candidate_moments.json` with this shape:

```json
{{
  "moments": [
    {{
      "start": "00:01:20.00",
      "end": "00:01:35.00",
      "text": "short transcript quote or copy clue",
      "reason": "why this time range may be visually useful",
      "priority": 1
    }}
  ]
}}
```

Rules:
- Do not choose moments from transcript alone. A candidate should either appear in `visual_digest` as visually promising/ambiguous, or explain why text reveals a useful range that the coarse storyboard may have missed.
- Prefer timestamped transcript segments when available.
- Treat local Whisper text as an ASR draft; preserve timestamps but allow minor wording uncertainty.
- Prefer start/end ranges over single timestamps. Expand the range enough to cover the surrounding visual action, but avoid multi-minute spans unless the scene itself stays relevant.
- Use `timestamp` instead of `start`/`end` only when the source truly points to a single instant; the extraction script will still add context and turn it into a small storyboard region.
- Keep the list small: 5-15 strong candidates for normal videos.
- Do not include moments with no time reference.
- After writing `candidate_moments.json`, run `scripts/extract_moment_frames.py summary_context.json candidate_moments.json` to capture focused region storyboard sheets.
""",
        encoding="utf-8",
    )
    return prompt_path


def write_html_scaffold(output: Path, context: dict[str, Any]) -> Path:
    summary_path = output / "summary.html"
    bili = context.get("bilibili") or {}
    ytdlp = context.get("ytdlp") or {}
    title = bili.get("title") or ytdlp.get("title") or Path(context["video_path"]).name
    description = bili.get("description") or ytdlp.get("description") or ""
    cover = rel(bili.get("cover_path"), output)
    screenshots = context.get("selected_screenshots") or []
    focused = ((context.get("focused_moment_frames") or {}).get("captures") or [])[:12]
    comments = bili.get("comments") or []

    deck = description.strip().replace("\n", " ")
    if len(deck) > 220:
        deck = deck[:220].rstrip() + "..."
    if not deck:
        deck = "这是一篇基于原视频画面、标题、评论与转录线索写成的读者向文章草稿。"

    screenshot_html = "\n".join(
        f"""<figure class="shot-card">
          <img src="{html.escape(item["relative_frame_path"])}" alt="原视频画面 {html.escape(item.get("timestamp", ""))}">
          <figcaption>画面停在 {html.escape(item.get("timestamp", ""))}：这一帧把视频里的关键信息变成了可以被读者直观看见的证据。</figcaption>
        </figure>"""
        for item in screenshots[:8]
    )
    focused_html = "\n".join(
        f"""<figure class="shot-card">
          <img src="{html.escape(item["relative_frame_path"])}" alt="值得重看的原视频瞬间 {html.escape(item.get("timestamp", ""))}">
          <figcaption>{html.escape(item.get("timestamp", ""))}：{html.escape(str(item.get("text") or item.get("reason") or "这个瞬间值得被放进文章，因为它让抽象观点落到了具体画面上。"))[:140]}</figcaption>
        </figure>"""
        for item in focused[:6]
    )
    comment_html = "\n".join(
        f"""<blockquote>
          <p>{html.escape(str(comment.get("message") or ""))}</p>
          <cite>{html.escape(str(comment.get("user") or "观众"))}</cite>
        </blockquote>"""
        for comment in comments[:3]
    )
    cover_html = (
        f"""<figure class="cover-figure">
          <img class="cover" src="{html.escape(cover)}" alt="视频封面">
          <figcaption>封面先抛出问题，正文要回答它为什么值得被看见。</figcaption>
        </figure>"""
        if cover
        else ""
    )

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f3f0e8; color: #202124; }}
    main {{ max-width: 760px; margin: 0 auto; background: #fffdf8; min-height: 100vh; }}
    article {{ padding: 34px 22px 48px; }}
    header {{ padding: 10px 0 24px; border-bottom: 1px solid #e8dfcf; }}
    .eyebrow {{ margin: 0 0 12px; color: #9a4c2e; font-size: 13px; font-weight: 700; letter-spacing: 0; }}
    h1 {{ font-size: 34px; line-height: 1.18; margin: 0 0 16px; color: #171717; }}
    .deck {{ margin: 0; color: #5f5a52; font-size: 17px; line-height: 1.75; }}
    h2 {{ font-size: 23px; line-height: 1.35; margin: 38px 0 14px; color: #171717; }}
    p {{ font-size: 16px; line-height: 1.9; margin: 14px 0; }}
    .lead {{ font-size: 18px; color: #32302c; }}
    .pull {{ margin: 28px 0; padding: 18px 20px; border-left: 4px solid #b85c38; background: #fbf3e7; font-size: 20px; line-height: 1.7; font-weight: 700; }}
    .cover-figure, .shot-card {{ margin: 24px 0; }}
    img {{ width: 100%; display: block; border-radius: 6px; background: #e6e0d6; }}
    figcaption {{ color: #716a61; font-size: 13px; line-height: 1.65; padding: 9px 2px 0; }}
    .shot-grid {{ display: grid; grid-template-columns: 1fr; gap: 18px; margin-top: 18px; }}
    blockquote {{ margin: 16px 0; padding: 16px 18px; background: #f6f7f8; border-left: 3px solid #6d7f8f; }}
    blockquote p {{ margin: 0; color: #2c3136; }}
    cite {{ display: block; margin-top: 8px; color: #7a7f85; font-size: 13px; font-style: normal; }}
    .ending {{ margin-top: 34px; padding-top: 22px; border-top: 1px solid #e8dfcf; color: #3a3630; }}
    @media (min-width: 720px) {{ article {{ padding: 48px 54px 64px; }} h1 {{ font-size: 42px; }} }}
  </style>
</head>
<body>
<main>
  <article>
    <header>
      <p class="eyebrow">视频里的关键一幕</p>
      <h1>{html.escape(title)}</h1>
      <p class="deck">{html.escape(deck)}</p>
    </header>

    {cover_html}

    <p class="lead">先把最重要的感受放在前面：这支视频真正值得被记住的，不只是它说了什么，而是它怎样把一个抽象话题一步步推到眼前。</p>
    <p>标题给了入口，画面给了证据，评论则说明观众为什么会停下来。好的视频总结不该只复述信息，它应该替读者抓住那条暗线：从问题出现，到情绪被点燃，再到答案变得没那么轻松。</p>

    <div class="pull">真正吸引人的地方，往往不是结论本身，而是结论出现之前，画面里那些让人意识到“事情不简单”的瞬间。</div>

    <section>
      <h2>它先把问题摆到台前</h2>
      <p>开头的任务不是交代背景，而是让读者迅速明白：这不是一段可以随手划走的视频。它把人物、观点或事件放在一个足够紧的场景里，让后面的每一次转折都有了重量。</p>
      <div class="shot-grid">
        {screenshot_html}
      </div>
    </section>

    <section>
      <h2>几个值得反复看的瞬间</h2>
      <p>如果只看文字，很多信息会显得平铺直叙；但一旦回到画面，语气、停顿、表情、镜头选择都会变成理解内容的线索。下面这些定格适合继续扩写成文章里的关键证据。</p>
      <div class="shot-grid">
        {focused_html or screenshot_html}
      </div>
    </section>

    <section>
      <h2>观众真正接住了什么</h2>
      <p>评论区的价值不在于替视频下结论，而在于暴露观众最敏感的部分：他们在哪一句话前停住，在哪个问题上产生共鸣，又在哪里感到不安。</p>
      {comment_html or "<p>当评论素材不足时，正文应更多依靠视频画面和转录内容建立判断，而不是硬凑互动感。</p>"}
    </section>

    <section>
      <h2>为什么它值得被写成一篇文章</h2>
      <p>把这支视频写成公众号文章时，重点不是把所有信息压缩成清单，而是把读者带回观看现场：先看见问题，再理解冲突，最后留下一个可以继续思考的余味。</p>
      <p class="ending">一个好的结尾，应该让读者觉得自己不只是“看完了一个视频”，而是重新理解了视频里那个问题为什么会重要。</p>
    </section>
  </article>
</main>
</body>
</html>
"""
    summary_path.write_text(html_text, encoding="utf-8")
    return summary_path


def build_context(
    output: Path,
    source: str,
    video_path: Path,
    info: dict[str, Any] | None,
    info_path: Path | None,
    bili: dict[str, Any],
    storyboard_manifest: dict[str, Any],
    transcription: dict[str, Any],
) -> dict[str, Any]:
    transcript_segments = load_whisper_segments(transcription)
    transcript_segments_path = write_transcript_segments(output, transcript_segments)
    selected = []
    for frame in select_screenshots(storyboard_manifest, count=8):
        copied = dict(frame)
        copied["relative_frame_path"] = rel(frame.get("frame_path"), output)
        copied["relative_sheet_path"] = rel(frame.get("sheet_path"), output)
        selected.append(copied)

    storyboard = dict(storyboard_manifest)
    storyboard["sheets"] = [
        {**sheet, "relative_path": rel(sheet.get("path"), output)} for sheet in storyboard_manifest.get("sheets", [])
    ]

    context = {
        "metadata": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "source_is_url": is_url(source),
        },
        "video_path": str(video_path),
        "relative_video_path": rel(video_path, output),
        "ytdlp": compact_ytdlp_info(info),
        "ytdlp_info_path": rel(info_path, output),
        "bilibili": bili,
        "storyboard": storyboard,
        "visual_digest_path": "visual_digest.md",
        "visual_digest_json_path": "visual_digest.json",
        "visual_digest_prompt_path": "visual_digest_prompt.md",
        "selected_screenshots": selected,
        "transcription": transcription,
        "transcript_segments_path": rel(transcript_segments_path, output),
        "transcript_segments_count": len(transcript_segments),
        "transcript_segments_preview": transcript_segments[:40],
        "transcript_excerpt": first_transcript_text(transcription, transcript_segments),
    }
    return context


def parse_args() -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path)
    pre_args, _ = pre_parser.parse_known_args()
    config = load_config(pre_args.config)

    parser = argparse.ArgumentParser(description="Prepare video context from a URL or local file.", parents=[pre_parser])
    parser.add_argument("source", help="Video URL or local video path.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--source-url", help="Original URL to use for metadata when source is a local file.")
    parser.add_argument("--density", choices=["coarse", "balanced", "dense"], default="balanced")
    parser.add_argument("--max-total-frames", type=int)
    parser.add_argument("--interval", type=float)
    parser.add_argument("--cols", type=int)
    parser.add_argument("--max-frames-per-sheet", type=int)
    parser.add_argument("--segment-seconds", type=float)
    parser.add_argument("--thumb-width", type=int)
    parser.add_argument("--extract-audio", action="store_true", help="Extract audio for later ASR.")
    parser.add_argument(
        "--transcribe",
        dest="transcribe",
        action="store_true",
        default=True,
        help="Run local Whisper CLI on extracted audio. This is enabled by default.",
    )
    parser.add_argument("--no-transcribe", dest="transcribe", action="store_false", help="Skip local Whisper transcription.")
    parser.add_argument("--whisper-command", default=configured_default(config, ("whisper", "command"), "VIDEO_STORYBOARD_WHISPER_COMMAND", "whisper"))
    parser.add_argument("--whisper-model", default=configured_default(config, ("whisper", "model"), "VIDEO_STORYBOARD_WHISPER_MODEL", "base"))
    parser.add_argument(
        "--whisper-language",
        default=configured_default(config, ("whisper", "language"), "VIDEO_STORYBOARD_WHISPER_LANGUAGE", "auto"),
        help="Language code such as zh or en, or auto to let Whisper detect.",
    )
    parser.add_argument(
        "--whisper-device",
        choices=["auto", "cpu", "cuda", "mps"],
        default=configured_default(config, ("whisper", "device"), "VIDEO_STORYBOARD_WHISPER_DEVICE", "auto"),
    )
    parser.add_argument(
        "--whisper-fp16",
        choices=["auto", "True", "False"],
        default=configured_default(config, ("whisper", "fp16"), "VIDEO_STORYBOARD_WHISPER_FP16", "auto"),
    )
    parser.add_argument("--whisper-initial-prompt", default=configured_default(config, ("whisper", "initial_prompt"), "VIDEO_STORYBOARD_WHISPER_INITIAL_PROMPT", None))
    parser.add_argument("--whisper-threads", type=int, default=configured_optional_int(config, ("whisper", "threads"), "VIDEO_STORYBOARD_WHISPER_THREADS"))
    parser.add_argument("--comments", type=int, default=8, help="Number of Bilibili comments to fetch.")
    parser.add_argument("--no-bilibili-api", action="store_true")
    parser.add_argument("--bvid")
    parser.add_argument("--aid")
    parser.add_argument("--bili-cookie-file", type=Path, help="File containing a raw Cookie header for Bilibili API calls.")
    parser.add_argument("--cookies", type=Path, help="yt-dlp cookies file.")
    parser.add_argument("--cookies-from-browser", help="yt-dlp browser cookie source, e.g. chrome or chrome:Profile 1.")
    parser.add_argument("--ytdlp-comments", action="store_true", help="Ask yt-dlp to include comments in info.json when supported.")
    parser.add_argument("--no-html", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source_url or args.source
    default_name = "video-context"
    if not is_url(args.source):
        default_name = f"{Path(args.source).stem}-context"
    output = args.output or Path.cwd() / default_name
    output.mkdir(parents=True, exist_ok=True)

    info = None
    info_path = None
    if is_url(args.source):
        video_path, info, info_path = download_with_ytdlp(args.source, output, args)
    else:
        video_path = Path(args.source).expanduser().resolve()
        if not video_path.exists():
            raise SystemExit(f"Video not found: {video_path}")

    bili = fetch_bilibili_context(source, info, output, args)
    need_audio = args.extract_audio or args.transcribe
    storyboard_manifest = run_storyboard(video_path, output, args, need_audio=need_audio)

    audio_path = storyboard_manifest.get("audio_path")
    transcription = run_whisper(Path(audio_path) if audio_path else None, output, args)
    context = build_context(output, source, video_path, info, info_path, bili, storyboard_manifest, transcription)
    context_path = output / "summary_context.json"
    write_json(context_path, context)
    visual_digest_prompt_path = write_visual_digest_prompt(output, context_path)

    summary_path = None
    if not args.no_html:
        summary_path = write_html_scaffold(output, context)
        prompt_path = write_ai_prompt(output, context_path, summary_path)
    else:
        prompt_path = write_ai_prompt(output, context_path, output / "summary.html")
    moment_prompt_path = write_moment_selection_prompt(output, context_path)

    print(f"Output: {output}")
    print(f"Video: {video_path}")
    print(f"Context: {context_path}")
    print(f"Visual digest prompt: {visual_digest_prompt_path}")
    print(f"AI HTML prompt: {prompt_path}")
    print(f"Moment selection prompt: {moment_prompt_path}")
    if summary_path:
        print(f"HTML scaffold: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
