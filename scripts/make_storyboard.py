#!/usr/bin/env python3
"""Create adaptive timestamped storyboard sheets from a video file."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


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
DENSITY_MULTIPLIERS = {
    "coarse": 0.7,
    "balanced": 1.0,
    "dense": 1.5,
}


@dataclass
class VideoMeta:
    duration_seconds: float
    width: int | None = None
    height: int | None = None


@dataclass
class StoryboardConfig:
    density: str
    interval: float
    cols: int
    max_frames_per_sheet: int
    segment_seconds: float
    thumb_width: int
    label_height: int
    target_total_frames: int
    expected_total_frames: int
    rationale: str


@dataclass
class FrameInfo:
    index: int
    timestamp: float
    frame_path: Path
    sheet_path: Path | None = None


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Missing required tool: {name}. Install ffmpeg so {name} is available on PATH.")


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise SystemExit(f"Command failed: {' '.join(cmd)}\n{result.stderr.strip()}")


def probe_video(video: Path) -> VideoMeta:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(video),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"Could not inspect video:\n{result.stderr.strip()}")

    try:
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not parse video metadata: {result.stdout!r}") from exc

    width = None
    height = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = stream.get("width")
            height = stream.get("height")
            break
    return VideoMeta(duration_seconds=duration, width=width, height=height)


def format_time(seconds: float) -> str:
    rounded = round(seconds)
    include_fraction = abs(seconds - rounded) > 0.001
    total = int(math.floor(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    suffix = ""
    if include_fraction:
        fraction = f"{seconds - math.floor(seconds):.2f}"[1:].rstrip("0")
        suffix = fraction
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}{suffix}"
    return f"{minutes:02d}:{secs:02d}{suffix}"


def choose_target_frames(duration: float, density: str, max_total_frames: int | None) -> int:
    if duration <= 60:
        base = 120
    elif duration <= 180:
        base = 180
    elif duration <= 600:
        base = 320
    elif duration <= 1800:
        base = 520
    elif duration <= 7200:
        base = 800
    else:
        base = 1000

    target = max(8, round(base * DENSITY_MULTIPLIERS[density]))
    if max_total_frames is not None:
        target = min(target, max_total_frames)
    return target


def choose_nice_interval(raw_interval: float) -> float:
    for interval in NICE_INTERVALS:
        if interval >= raw_interval:
            return interval
    return NICE_INTERVALS[-1]


def choose_auto_thumb_width(duration: float, source_width: int | None) -> int:
    if duration <= 60:
        preferred = 480
    elif duration <= 600:
        preferred = 360
    elif duration <= 7200:
        preferred = 300
    else:
        preferred = 280

    if source_width:
        return max(200, min(preferred, source_width))
    return preferred


def choose_sheet_shape(duration: float, density: str) -> tuple[int, int]:
    if duration <= 180 and density == "dense":
        return 5, 30
    if duration <= 180:
        return 6, 36
    if duration <= 600:
        return 8, 64
    if density == "dense":
        return 8, 64
    if density in {"balanced", "coarse"}:
        return 10, 80
    return 10, 80


def build_adaptive_config(
    meta: VideoMeta,
    density: str,
    max_total_frames: int | None,
    interval_override: float | None,
    cols_override: int | None,
    max_frames_per_sheet_override: int | None,
    segment_seconds_override: float | None,
    thumb_width_override: int | None,
    label_height: int,
) -> StoryboardConfig:
    duration = meta.duration_seconds
    target_total_frames = choose_target_frames(duration, density, max_total_frames)

    if interval_override is None:
        if target_total_frames >= 600:
            interval = max(0.25, duration / max(target_total_frames - 1, 1))
        else:
            raw_interval = duration / max(target_total_frames, 1)
            interval = choose_nice_interval(max(0.25, raw_interval))
    else:
        interval = interval_override

    auto_cols, auto_max_frames = choose_sheet_shape(duration, density)
    cols = cols_override or auto_cols
    max_frames_per_sheet = max_frames_per_sheet_override or auto_max_frames

    if segment_seconds_override is None:
        segment_seconds = interval * max_frames_per_sheet
    else:
        segment_seconds = segment_seconds_override

    thumb_width = thumb_width_override or choose_auto_thumb_width(duration, meta.width)
    expected_total_frames = len(build_timestamps(duration, interval))

    if duration <= 60:
        bucket = "short clip"
    elif duration <= 600:
        bucket = "short-to-medium video"
    elif duration <= 7200:
        bucket = "long video"
    else:
        bucket = "very long video"

    rationale = (
        f"Auto settings treat this as a {bucket}: target about {target_total_frames} frames, "
        f"sample every {interval:g}s, write {max_frames_per_sheet} compact overview frames per sheet."
    )
    return StoryboardConfig(
        density=density,
        interval=interval,
        cols=cols,
        max_frames_per_sheet=max_frames_per_sheet,
        segment_seconds=segment_seconds,
        thumb_width=thumb_width,
        label_height=label_height,
        target_total_frames=target_total_frames,
        expected_total_frames=expected_total_frames,
        rationale=rationale,
    )


def extract_frame(video: Path, timestamp: float, out_path: Path, thumb_width: int) -> None:
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


def extract_audio(video: Path, audio_path: Path) -> None:
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-y",
            str(audio_path),
        ]
    )


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


def create_sheet(frames: list[FrameInfo], sheet_path: Path, cols: int, label_height: int) -> None:
    Image, ImageDraw, ImageFont = load_pillow()
    images = [Image.open(frame.frame_path).convert("RGB") for frame in frames]
    if not images:
        return
    thumb_w, thumb_h = images[0].size
    rows = math.ceil(len(images) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_height)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("Arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()

    for offset, (frame, image) in enumerate(zip(frames, images)):
        row = offset // cols
        col = offset % cols
        x = col * thumb_w
        y = row * (thumb_h + label_height)
        sheet.paste(image, (x, y + label_height))
        draw_label(draw, (x + 8, y + 8), f"{frame.index:04d}  {format_time(frame.timestamp)}", font)

    sheet.save(sheet_path, quality=92)
    for image in images:
        image.close()


def build_timestamps(duration: float, interval: float) -> list[float]:
    count = int(math.floor((duration + 1e-6) / interval)) + 1
    timestamps = [min(i * interval, max(duration - 0.05, 0)) for i in range(count)]
    seen: set[float] = set()
    unique = []
    for ts in timestamps:
        rounded = round(ts, 3)
        if rounded not in seen:
            seen.add(rounded)
            unique.append(rounded)
    return unique


def write_prompt(path: Path, video: Path, config: StoryboardConfig) -> None:
    path.write_text(
        f"""Analyze these timestamped storyboard sheets for video understanding or editing.

Source video: {video.name}
Sampling: every {config.interval:g} seconds, about {config.expected_total_frames} frames total
Sheet layout: {config.cols} columns, up to {config.max_frames_per_sheet} frames per sheet

Use the sheet timestamps and transcript/subtitles if provided. Return:
1. concise summary of what happens
2. important timestamps and visual changes
3. candidate highlight, cut, chapter, or review points
4. best frame candidates for thumbnails or reference
5. places where audio/transcript context is needed
6. suggested editing notes if the task is a highlight cut

If the sheets alone are not enough, say what additional transcript, audio, or higher-resolution frames are needed.
""",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create adaptive timestamped storyboard sheets from a video.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--density",
        choices=sorted(DENSITY_MULTIPLIERS.keys()),
        default="balanced",
        help="Sampling density used by adaptive defaults.",
    )
    parser.add_argument("--max-total-frames", type=int, default=None, help="Cap the adaptive total frame target.")
    parser.add_argument("--interval", type=float, default=None, help="Override seconds between extracted frames.")
    parser.add_argument("--cols", type=int, default=None, help="Override sheet column count.")
    parser.add_argument("--max-frames-per-sheet", type=int, default=None, help="Override sheet frame count.")
    parser.add_argument("--segment-seconds", type=float, default=None, help="Override maximum time span per sheet.")
    parser.add_argument("--thumb-width", type=int, default=None, help="Override extracted frame width.")
    parser.add_argument("--label-height", type=int, default=34)
    parser.add_argument(
        "--extract-audio",
        action="store_true",
        help="Also write audio/audio.wav for later ASR transcription. GPT cannot use this file directly.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print adaptive settings without extracting frames.")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.interval is not None and args.interval <= 0:
        raise SystemExit("--interval must be greater than 0.")
    if args.cols is not None and args.cols <= 0:
        raise SystemExit("--cols must be greater than 0.")
    if args.max_frames_per_sheet is not None and args.max_frames_per_sheet <= 0:
        raise SystemExit("--max-frames-per-sheet must be greater than 0.")
    if args.segment_seconds is not None and args.segment_seconds <= 0:
        raise SystemExit("--segment-seconds must be greater than 0.")
    if args.thumb_width is not None and args.thumb_width <= 0:
        raise SystemExit("--thumb-width must be greater than 0.")
    if args.max_total_frames is not None and args.max_total_frames <= 0:
        raise SystemExit("--max-total-frames must be greater than 0.")
    if not args.video.exists():
        raise SystemExit(f"Video not found: {args.video}")


def write_manifest(
    output: Path,
    args: argparse.Namespace,
    meta: VideoMeta,
    config: StoryboardConfig,
    frames: list[FrameInfo],
    sheets: list[dict[str, object]],
    audio_path: Path | None,
) -> None:
    manifest = {
        "video": str(args.video),
        "source": asdict(meta),
        "adaptive_config": asdict(config),
        "overrides": {
            "interval": args.interval,
            "cols": args.cols,
            "max_frames_per_sheet": args.max_frames_per_sheet,
            "segment_seconds": args.segment_seconds,
            "thumb_width": args.thumb_width,
            "max_total_frames": args.max_total_frames,
        },
        "frames": [
            {
                "index": frame.index,
                "timestamp_seconds": frame.timestamp,
                "timestamp": format_time(frame.timestamp),
                "frame_path": str(frame.frame_path),
                "sheet_path": str(frame.sheet_path) if frame.sheet_path else None,
            }
            for frame in frames
        ],
        "sheets": sheets,
        "audio_path": str(audio_path) if audio_path else None,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    validate_args(args)
    require_tool("ffmpeg")
    require_tool("ffprobe")

    meta = probe_video(args.video)
    config = build_adaptive_config(
        meta=meta,
        density=args.density,
        max_total_frames=args.max_total_frames,
        interval_override=args.interval,
        cols_override=args.cols,
        max_frames_per_sheet_override=args.max_frames_per_sheet,
        segment_seconds_override=args.segment_seconds,
        thumb_width_override=args.thumb_width,
        label_height=args.label_height,
    )

    output = args.output or args.video.with_suffix("").parent / f"{args.video.stem}-storyboard"
    if args.dry_run:
        print(json.dumps({"video": str(args.video), "source": asdict(meta), "adaptive_config": asdict(config)}, indent=2))
        return 0

    frames_dir = output / "frames"
    sheets_dir = output / "sheets"
    audio_dir = output / "audio"
    frames_dir.mkdir(parents=True, exist_ok=True)
    sheets_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    timestamps = build_timestamps(meta.duration_seconds, config.interval)
    frames: list[FrameInfo] = []
    for index, timestamp in enumerate(timestamps, start=1):
        frame_path = frames_dir / f"frame_{index:04d}_{format_time(timestamp).replace(':', '-')}.jpg"
        extract_frame(args.video, timestamp, frame_path, config.thumb_width)
        if not frame_path.exists() or frame_path.stat().st_size == 0:
            continue
        frames.append(FrameInfo(index=index, timestamp=timestamp, frame_path=frame_path))

    sheets: list[dict[str, object]] = []
    current: list[FrameInfo] = []
    sheet_start_ts = frames[0].timestamp if frames else 0
    for frame in frames:
        span_limit_hit = current and frame.timestamp - sheet_start_ts >= config.segment_seconds
        count_limit_hit = len(current) >= config.max_frames_per_sheet
        if current and (span_limit_hit or count_limit_hit):
            sheet_path = sheets_dir / f"storyboard_{len(sheets) + 1:03d}.jpg"
            create_sheet(current, sheet_path, config.cols, config.label_height)
            for item in current:
                item.sheet_path = sheet_path
            sheets.append(
                {
                    "path": str(sheet_path),
                    "start_seconds": current[0].timestamp,
                    "end_seconds": current[-1].timestamp,
                    "start": format_time(current[0].timestamp),
                    "end": format_time(current[-1].timestamp),
                    "frame_count": len(current),
                }
            )
            current = []
            sheet_start_ts = frame.timestamp
        current.append(frame)
    if current:
        sheet_path = sheets_dir / f"storyboard_{len(sheets) + 1:03d}.jpg"
        create_sheet(current, sheet_path, config.cols, config.label_height)
        for item in current:
            item.sheet_path = sheet_path
        sheets.append(
            {
                "path": str(sheet_path),
                "start_seconds": current[0].timestamp,
                "end_seconds": current[-1].timestamp,
                "start": format_time(current[0].timestamp),
                "end": format_time(current[-1].timestamp),
                "frame_count": len(current),
            }
        )

    audio_path = None
    if args.extract_audio:
        audio_path = audio_dir / "audio.wav"
        extract_audio(args.video, audio_path)

    write_manifest(output, args, meta, config, frames, sheets, audio_path)
    write_prompt(output / "analysis_prompt.md", args.video, config)

    print(f"Output: {output}")
    print(config.rationale)
    print(f"Frames: {len(frames)}")
    print(f"Sheets: {len(sheets)}")
    if audio_path:
        print(f"Audio for ASR: {audio_path}")
    print(f"Prompt: {output / 'analysis_prompt.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
