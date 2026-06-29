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


def write_ai_prompt(output: Path, context_path: Path, summary_path: Path) -> Path:
    prompt_path = output / "ai_html_prompt.md"
    prompt_path.write_text(
        f"""Create or refine the final WeChat/public-account-ready HTML article page.

Inputs:
- Context JSON: {context_path.name}
- Current HTML scaffold: {summary_path.name}
- Storyboard sheets and selected source screenshots are referenced in the context JSON.

Requirements:
1. Write a polished standalone HTML page at `{summary_path.name}` that can be uploaded directly as a WeChat official account article.
2. Make the page read like a finished public article for readers: catchy shareable headline, strong subtitle/deck, hooky lead, narrative sections, image evidence, captions, pull quotes/key sentences, and a satisfying ending.
3. Include original video screenshots from the `selected_screenshots` list; keep image paths relative.
   If `focused_moment_frames` exists, inspect its region storyboard sheets first, then choose visually useful frames or a short visual progression from that focused set.
4. Explain what happens in the video and why the selected moments matter. Turn title, description, comments, and transcript into engaging natural prose, not bullet-point analysis.
5. Do not expose developer-only or backstage artifacts such as raw JSON links, manifest paths, frame counts, exhaustive transcript files, implementation notes, clip/editing suggestions, or generated file paths unless the user explicitly asks.
6. Distinguish visual evidence from transcript/comment context.
7. Do not invent details that are not supported by the screenshots, metadata, comments, or transcript.
8. Keep the page local-file friendly; do not require a server or external assets.
9. If the user wants a clean final deliverable after the HTML is polished, run `scripts/package_summary.py <output-dir>` to review the cleanup plan, then run it again with `--apply`. The final directory should contain only `summary.html`, `assets/`, and optional `summary-long.png`.
10. Avoid final-page headings like "video understanding report", "summary", "analysis", "artifacts", or "clip suggestions"; use reader-facing magazine/public-account-style headings.
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

Read the title, description, comments, and local Whisper transcript data referenced in the context. Prefer `transcript_segments_path` because it contains timestamped Whisper segments. Pick only regions where the text suggests there may be useful visual evidence in the video: a reaction, action, object, UI state, visual joke, scene change, demonstration, or emotionally important moment.

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
    metadata = context.get("metadata") or {}
    bili = context.get("bilibili") or {}
    ytdlp = context.get("ytdlp") or {}
    title = bili.get("title") or ytdlp.get("title") or Path(context["video_path"]).name
    description = bili.get("description") or ytdlp.get("description") or ""
    cover = rel(bili.get("cover_path"), output)
    screenshots = context.get("selected_screenshots") or []
    focused = ((context.get("focused_moment_frames") or {}).get("captures") or [])[:12]
    sheets = (context.get("storyboard") or {}).get("sheets") or []
    comments = bili.get("comments") or []
    transcript_excerpt = context.get("transcript_excerpt") or ""

    screenshot_html = "\n".join(
        f'<figure><img src="{html.escape(item["relative_frame_path"])}" alt="Video screenshot at {html.escape(item.get("timestamp", ""))}"><figcaption>{html.escape(item.get("timestamp", ""))}</figcaption></figure>'
        for item in screenshots
    )
    focused_html = "\n".join(
        f'<figure><img src="{html.escape(item["relative_frame_path"])}" alt="Focused video frame at {html.escape(item.get("timestamp", ""))}"><figcaption>{html.escape(item.get("timestamp", ""))} - {html.escape(str(item.get("text") or item.get("reason") or ""))[:120]}</figcaption></figure>'
        for item in focused
    )
    sheet_html = "\n".join(
        f'<li><a href="{html.escape(item["relative_path"])}">{html.escape(item.get("start", ""))} - {html.escape(item.get("end", ""))}</a></li>'
        for item in sheets
    )
    comment_html = "\n".join(
        f'<li><strong>{html.escape(str(comment.get("user") or "unknown"))}</strong>: {html.escape(str(comment.get("message") or ""))}</li>'
        for comment in comments
    )
    cover_html = f'<img class="cover" src="{html.escape(cover)}" alt="Video cover">' if cover else ""
    no_comments_html = "<li>No comments captured.</li>"
    transcript_html = html.escape(transcript_excerpt) if transcript_excerpt else "No transcript captured yet."

    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f7f7f4; color: #1f2933; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 28px; }}
    header {{ display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(260px, .6fr); gap: 24px; align-items: start; }}
    h1 {{ font-size: 32px; line-height: 1.15; margin: 0 0 12px; }}
    h2 {{ font-size: 18px; margin: 32px 0 12px; }}
    p {{ line-height: 1.65; }}
    .cover {{ width: 100%; border-radius: 8px; background: #ddd; }}
    .meta {{ color: #52606d; font-size: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    figure {{ margin: 0; background: #fff; border: 1px solid #e1e5e8; border-radius: 8px; overflow: hidden; }}
    figure img {{ width: 100%; display: block; }}
    figcaption {{ font-size: 13px; padding: 8px 10px; color: #52606d; }}
    pre {{ white-space: pre-wrap; background: #fff; border: 1px solid #e1e5e8; padding: 14px; border-radius: 8px; max-height: 360px; overflow: auto; }}
    li {{ margin: 8px 0; }}
    @media (max-width: 780px) {{ header {{ grid-template-columns: 1fr; }} main {{ padding: 18px; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <section>
      <h1>{html.escape(title)}</h1>
      <p class="meta">Generated {html.escape(metadata.get("generated_at", ""))}</p>
      <p>{html.escape(description[:1200])}</p>
    </section>
    {cover_html}
  </header>

  <section>
    <h2>Source Screenshots</h2>
    <div class="grid">
      {screenshot_html}
    </div>
  </section>

  <section>
    <h2>Focused Text-Moment Frames</h2>
    <div class="grid">
      {focused_html or "<p>No focused moment frames extracted yet.</p>"}
    </div>
  </section>

  <section>
    <h2>Storyboard Sheets</h2>
    <ul>{sheet_html}</ul>
  </section>

  <section>
    <h2>Comments</h2>
    <ul>{comment_html or no_comments_html}</ul>
  </section>

  <section>
    <h2>Transcript Excerpt</h2>
    <pre>{transcript_html}</pre>
  </section>
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
    print(f"AI HTML prompt: {prompt_path}")
    print(f"Moment selection prompt: {moment_prompt_path}")
    if summary_path:
        print(f"HTML scaffold: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
