#!/usr/bin/env python3
"""Extract focused frames around AI-selected transcript or copy moments."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TIME_RE = re.compile(r"^\s*(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)\s*$")
NICE_INTERVALS = [
    0.25,
    0.5,
    0.75,
    1,
    1.25,
    1.5,
    2,
    2.5,
    3,
    3.5,
    4,
    4.5,
    5,
    5.5,
    6,
    7,
    8,
    9,
    10,
    12,
    15,
    20,
    30,
    45,
    60,
    90,
    120,
]


@dataclass
class Capture:
    moment_index: int
    frame_index: int
    timestamp: float
    path: Path
    moment: dict[str, Any]
    sheet_path: Path | None = None


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise SystemExit(f"Command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Missing required tool: {name}. Install ffmpeg so {name} is available on PATH.")


def rel(path: Path | str | None, base: Path) -> str | None:
    if path is None:
        return None
    return os.path.relpath(Path(path), base).replace(os.sep, "/")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_time(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    match = TIME_RE.match(text)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def format_time(seconds: float) -> str:
    total = int(math.floor(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    fraction = seconds - math.floor(seconds)
    suffix = f"{fraction:.2f}"[1:].rstrip("0") if fraction > 0.001 else ""
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}{suffix}"
    return f"{minutes:02d}:{secs:02d}{suffix}"


def probe_duration(video: Path) -> float:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ]
    )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise SystemExit(f"Could not parse video duration: {result.stdout!r}") from exc


def choose_nice_interval(raw_interval: float) -> float:
    for interval in NICE_INTERVALS:
        if interval >= raw_interval:
            return interval
    return NICE_INTERVALS[-1]


def evenly_select(values: list[float], target_count: int) -> list[float]:
    if len(values) <= target_count:
        return values
    selected = []
    seen = set()
    for index in range(target_count):
        pos = round(index * (len(values) - 1) / max(target_count - 1, 1))
        value = values[pos]
        if value in seen:
            continue
        seen.add(value)
        selected.append(value)
    return selected


def build_region_timestamps(start: float, end: float, interval: float) -> list[float]:
    if end < start:
        start, end = end, start
    if end - start < 0.001:
        return [round(start, 3)]
    count = int(math.floor((end - start) / interval)) + 1
    values = [round(start + index * interval, 3) for index in range(count)]
    rounded_end = round(end, 3)
    if values[-1] < rounded_end and rounded_end - values[-1] >= min(interval * 0.33, 1.0):
        values.append(rounded_end)
    unique = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def extract_frame(video: Path, timestamp: float, out_path: Path, thumb_width: int) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"scale={thumb_width}:-1",
            "-q:v",
            "2",
            "-y",
            str(out_path),
        ]
    )
    return out_path.exists() and out_path.stat().st_size > 0


def load_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise SystemExit("Missing Python package: Pillow. Install it with `python3 -m pip install Pillow`.") from exc
    return Image, ImageDraw, ImageFont


def draw_label(draw, xy: tuple[int, int], text: str, font) -> None:
    x, y = xy
    try:
        bbox = draw.textbbox((x, y), text, font=font)
    except AttributeError:
        w, h = draw.textsize(text, font=font)
        bbox = (x, y, x + w, y + h)
    pad = 5
    draw.rectangle((bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad), fill=(0, 0, 0))
    draw.text((x, y), text, fill=(255, 255, 255), font=font)


def create_sheets(captures: list[Capture], output: Path, cols: int, max_frames_per_sheet: int, label_height: int) -> list[dict[str, Any]]:
    Image, ImageDraw, ImageFont = load_pillow()
    sheets_dir = output / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    for sheet_index, start in enumerate(range(0, len(captures), max_frames_per_sheet), start=1):
        group = captures[start : start + max_frames_per_sheet]
        images = [Image.open(item.path).convert("RGB") for item in group]
        if not images:
            continue
        thumb_w, thumb_h = images[0].size
        rows = math.ceil(len(images) / cols)
        sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_height)), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        try:
            font = ImageFont.truetype("Arial.ttf", 18)
        except OSError:
            font = ImageFont.load_default()

        for offset, (capture, image) in enumerate(zip(group, images)):
            row = offset // cols
            col = offset % cols
            x = col * thumb_w
            y = row * (thumb_h + label_height)
            sheet.paste(image, (x, y + label_height))
            draw_label(
                draw,
                (x + 8, y + 8),
                f"M{capture.moment_index:02d}-{capture.frame_index}  {format_time(capture.timestamp)}",
                font,
            )

        sheet_path = sheets_dir / f"focused_frames_{sheet_index:03d}.jpg"
        sheet.save(sheet_path, quality=92)
        for image in images:
            image.close()
        for item in group:
            item.sheet_path = sheet_path
        sheets.append(
            {
                "path": str(sheet_path),
                "relative_path": None,
                "frame_count": len(group),
                "start_seconds": group[0].timestamp,
                "end_seconds": group[-1].timestamp,
                "start": format_time(group[0].timestamp),
                "end": format_time(group[-1].timestamp),
            }
        )
    return sheets


def normalize_moments(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw_moments = raw.get("moments") or raw.get("selected_moments") or raw.get("items") or []
    elif isinstance(raw, list):
        raw_moments = raw
    else:
        raise SystemExit("Moments JSON must be a list or an object with a `moments` list.")

    moments = []
    for index, item in enumerate(raw_moments, start=1):
        if not isinstance(item, dict):
            continue
        start = parse_time(
            item.get("start_seconds")
            or item.get("start")
            or item.get("timestamp_seconds")
            or item.get("timestamp")
            or item.get("time")
        )
        end = parse_time(item.get("end_seconds") or item.get("end"))
        if start is None and end is None:
            continue
        if start is None:
            start = end
        if end is None:
            end = start
        if end < start:
            start, end = end, start
        normalized = dict(item)
        normalized["moment_index"] = index
        normalized["start_seconds"] = start
        normalized["end_seconds"] = end
        normalized["start"] = format_time(start)
        normalized["end"] = format_time(end)
        moments.append(normalized)
    return moments


def timestamps_for_moment(
    moment: dict[str, Any],
    duration: float,
    frames_per_moment: int,
    context_seconds: float,
    interval_override: float | None,
) -> list[float]:
    start = max(0.0, float(moment["start_seconds"]))
    end = min(duration, float(moment["end_seconds"]))
    if end < start:
        end = start

    span_start = max(0.0, start - context_seconds)
    span_end = min(duration, end + context_seconds)
    span_duration = max(0.0, span_end - span_start)

    if frames_per_moment <= 1:
        values = [(span_start + span_end) / 2]
    else:
        interval = interval_override
        if interval is None:
            interval = 1.0
        values = evenly_select(build_region_timestamps(span_start, span_end, interval), frames_per_moment)

    clipped = []
    seen = set()
    for value in values:
        timestamp = round(min(max(value, 0.0), max(duration - 0.05, 0.0)), 3)
        if timestamp in seen:
            continue
        seen.add(timestamp)
        clipped.append(timestamp)
    return clipped


def load_context_or_video(source: Path) -> tuple[Path, dict[str, Any] | None, Path]:
    if source.suffix.lower() == ".json":
        context = load_json(source)
        video_path = Path(context.get("video_path") or "")
        if not video_path.exists():
            raise SystemExit(f"Context JSON does not point to an existing video: {video_path}")
        return video_path, context, source
    if not source.exists():
        raise SystemExit(f"Video not found: {source}")
    return source, None, source


def write_selection_prompt(output: Path, manifest_path: Path) -> Path:
    prompt_path = output / "final_frame_selection_prompt.md"
    prompt_path.write_text(
        f"""Review the focused region storyboard sheets and pick the best evidence frames for the final video summary.

Inputs:
- Focused frame manifest: {manifest_path.name}
- Focused region sheets: `sheets/focused_frames_###.jpg`
- Individual frames: `frames/`

Return:
1. which transcript-selected regions are visually confirmed by the focused sheets
2. which moments looked promising in text but are not visually useful
3. best frame or short visual progression to include in the final HTML summary
4. captions for each selected frame
5. any moments that need denser reruns or transcript clarification

Use the timestamps and moment text/reasons from the manifest. Treat each selected text moment as a small storyboard region, not as a single frame. Do not infer visual details that are not visible in the frames.
""",
        encoding="utf-8",
    )
    return prompt_path


def update_context(context_path: Path, context: dict[str, Any], manifest: dict[str, Any], output: Path) -> None:
    context["focused_moment_frames"] = {
        "manifest_path": rel(output / "focused_frames_manifest.json", context_path.parent),
        "prompt_path": rel(output / "final_frame_selection_prompt.md", context_path.parent),
        "sheets": manifest["sheets"],
        "captures": manifest["captures"],
    }
    write_json(context_path, context)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract focused frames around AI-selected transcript moments.")
    parser.add_argument("source", type=Path, help="summary_context.json or a local video path.")
    parser.add_argument("moments", type=Path, help="JSON file containing AI-selected moments.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--frames-per-moment", type=int, default=240, help="Cap frame count for each selected transcript region. Default region sampling is 1 FPS.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between frames inside each selected transcript region.")
    parser.add_argument("--context-seconds", type=float, default=5.0)
    parser.add_argument("--thumb-width", type=int, default=300)
    parser.add_argument("--cols", type=int, default=10)
    parser.add_argument("--max-frames-per-sheet", type=int, default=80)
    parser.add_argument("--label-height", type=int, default=34)
    parser.add_argument("--no-update-context", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.frames_per_moment <= 0:
        raise SystemExit("--frames-per-moment must be greater than 0.")
    if args.context_seconds < 0:
        raise SystemExit("--context-seconds cannot be negative.")
    if args.interval is not None and args.interval <= 0:
        raise SystemExit("--interval must be greater than 0.")
    if args.cols <= 0 or args.max_frames_per_sheet <= 0:
        raise SystemExit("--cols and --max-frames-per-sheet must be greater than 0.")
    if not args.moments.exists():
        raise SystemExit(f"Moments file not found: {args.moments}")

    require_tool("ffmpeg")
    require_tool("ffprobe")

    video_path, context, context_path = load_context_or_video(args.source)
    output = args.output or ((args.source.parent / "focused_frames") if context else video_path.with_suffix("").parent / f"{video_path.stem}-focused-frames")
    output.mkdir(parents=True, exist_ok=True)
    frames_dir = output / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    duration = probe_duration(video_path)
    moments = normalize_moments(load_json(args.moments))
    captures: list[Capture] = []
    for moment in moments:
        timestamps = timestamps_for_moment(moment, duration, args.frames_per_moment, args.context_seconds, args.interval)
        for frame_index, timestamp in enumerate(timestamps, start=1):
            moment_index = int(moment["moment_index"])
            frame_name = f"moment_{moment_index:03d}_frame_{frame_index:02d}_{format_time(timestamp).replace(':', '-')}.jpg"
            frame_path = frames_dir / frame_name
            if extract_frame(video_path, timestamp, frame_path, args.thumb_width):
                captures.append(
                    Capture(
                        moment_index=moment_index,
                        frame_index=frame_index,
                        timestamp=timestamp,
                        path=frame_path,
                        moment=moment,
                    )
                )

    sheets = create_sheets(captures, output, args.cols, args.max_frames_per_sheet, args.label_height)
    for sheet in sheets:
        sheet["relative_path"] = rel(sheet["path"], output)

    manifest = {
        "video_path": str(video_path),
        "source_context_path": str(context_path) if context else None,
        "moments_path": str(args.moments),
        "duration_seconds": duration,
        "settings": {
            "sampling_strategy": "region_storyboard",
            "frames_per_moment": args.frames_per_moment,
            "interval": args.interval,
            "context_seconds": args.context_seconds,
            "thumb_width": args.thumb_width,
            "cols": args.cols,
            "max_frames_per_sheet": args.max_frames_per_sheet,
        },
        "moments": moments,
        "captures": [
            {
                "moment_index": capture.moment_index,
                "frame_index": capture.frame_index,
                "timestamp_seconds": capture.timestamp,
                "timestamp": format_time(capture.timestamp),
                "moment_start": capture.moment.get("start"),
                "moment_end": capture.moment.get("end"),
                "frame_path": str(capture.path),
                "relative_frame_path": rel(capture.path, output),
                "sheet_path": str(capture.sheet_path) if capture.sheet_path else None,
                "relative_sheet_path": rel(capture.sheet_path, output) if capture.sheet_path else None,
                "text": capture.moment.get("text") or capture.moment.get("quote") or capture.moment.get("caption"),
                "reason": capture.moment.get("reason"),
            }
            for capture in captures
        ],
        "sheets": sheets,
    }
    manifest_path = output / "focused_frames_manifest.json"
    write_json(manifest_path, manifest)
    prompt_path = write_selection_prompt(output, manifest_path)

    if context and not args.no_update_context:
        # Rewrite paths relative to the context bundle so the final HTML prompt can use them directly.
        context_manifest = dict(manifest)
        context_manifest["sheets"] = [{**sheet, "relative_path": rel(sheet["path"], context_path.parent)} for sheet in sheets]
        context_manifest["captures"] = [
            {**capture, "relative_frame_path": rel(capture["frame_path"], context_path.parent), "relative_sheet_path": rel(capture["sheet_path"], context_path.parent) if capture.get("sheet_path") else None}
            for capture in manifest["captures"]
        ]
        update_context(context_path, context, context_manifest, output)

    print(f"Output: {output}")
    print(f"Moments: {len(moments)}")
    print(f"Focused frames: {len(captures)}")
    print(f"Sheets: {len(sheets)}")
    print(f"Manifest: {manifest_path}")
    print(f"Selection prompt: {prompt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
