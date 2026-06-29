#!/usr/bin/env python3
"""Package a final HTML article and remove video-workflow engineering artifacts."""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse


LOCAL_MEDIA_RE = re.compile(
    r"(?P<prefix>\b(?:src|poster)\s*=\s*)(?P<quote>[\"'])(?P<value>[^\"']+)(?P=quote)",
    re.IGNORECASE,
)
REMOTE_SCHEMES = {"http", "https", "data", "mailto", "tel", "javascript"}
DEFAULT_KEEP_NAMES = {"assets", "summary.html", "summary-long.png"}
ARTICLE_BANNED_PATTERNS = [
    r"\bSource Screenshots\b",
    r"\bFocused Text-Moment Frames\b",
    r"\bStoryboard Sheets\b",
    r"\bTranscript Excerpt\b",
    r"\bAnalysis Report\b",
    r"\bMetadata\b",
    r"\bArtifacts?\b",
    r"\bManifest\b",
    r"\bsummary_context\.json\b",
    r"\bai_html_prompt\.md\b",
    r"\bmoment_selection_prompt\.md\b",
    r"\bcandidate_moments\.json\b",
    r"\bselected_screenshots\b",
    r"\brelative_frame_path\b",
    r"\bframe_count\b",
    r"\btranscript_segments\b",
    r"\bNo focused moment frames extracted yet\b",
    r"工程总结",
    r"工作流报告",
    r"开发者",
    r"开发文档",
    r"元数据",
    r"转录节选",
    r"素材清单",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Keep only the final summary HTML, referenced assets, and optional long screenshot."
    )
    parser.add_argument("bundle", type=Path, help="Video context/output directory containing summary.html.")
    parser.add_argument("--html", default="summary.html", help="HTML file name inside the bundle.")
    parser.add_argument("--assets-dir", default="assets", help="Final image asset directory inside the bundle.")
    parser.add_argument("--keep", action="append", default=[], help="Additional top-level file or directory name to keep.")
    parser.add_argument("--skip-article-check", action="store_true", help="Skip public-article quality checks.")
    parser.add_argument("--apply", action="store_true", help="Actually rewrite/copy/delete. Omit for dry-run.")
    return parser.parse_args()


def visible_text_from_html(text: str) -> str:
    text = re.sub(r"<(script|style|template)\b.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def article_quality_warnings(html_text: str) -> list[str]:
    visible_text = visible_text_from_html(html_text)
    warnings = []
    for pattern in ARTICLE_BANNED_PATTERNS:
        if re.search(pattern, visible_text, flags=re.IGNORECASE):
            warnings.append(f"Visible engineering/report term found: {pattern}")
    if not re.search(r"<h1\b", html_text, flags=re.IGNORECASE):
        warnings.append("Missing an article headline (`h1`).")
    if not re.search(r"<img\b", html_text, flags=re.IGNORECASE):
        warnings.append("Missing original-video image evidence (`img`).")
    if len(visible_text) < 700:
        warnings.append("Visible article text is too short to feel like a finished public article.")
    if len(re.findall(r"<h2\b", html_text, flags=re.IGNORECASE)) < 2:
        warnings.append("Expected at least two narrative sections (`h2`).")
    return warnings


def local_path_and_suffix(value: str) -> tuple[str, str] | None:
    value = html.unescape(value.strip())
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in REMOTE_SCHEMES or value.startswith("#"):
        return None
    if parsed.scheme == "file":
        return unquote(parsed.path), ""
    path = parsed.path if parsed.path else value
    suffix = ""
    if parsed.query:
        suffix += "?" + parsed.query
    if parsed.fragment:
        suffix += "#" + parsed.fragment
    return unquote(path), suffix


def resolve_media_path(raw_path: str, bundle: Path) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = bundle / path
    return path.resolve()


def asset_name(index: int, source: Path, used_names: set[str]) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip("-._") or "image"
    stem = stem[:48]
    suffix = source.suffix.lower() or ".jpg"
    candidate = f"image_{index:03d}_{stem}{suffix}"
    counter = 2
    while candidate in used_names:
        candidate = f"image_{index:03d}_{stem}_{counter}{suffix}"
        counter += 1
    used_names.add(candidate)
    return candidate


def build_html_plan(bundle: Path, html_path: Path, assets_dir: Path) -> tuple[str, list[tuple[Path, Path]], list[str]]:
    original = html_path.read_text(encoding="utf-8")
    replacements: list[tuple[int, int, str]] = []
    copies: list[tuple[Path, Path]] = []
    warnings: list[str] = []
    source_to_asset: dict[Path, str] = {}
    used_names: set[str] = set()

    for match in LOCAL_MEDIA_RE.finditer(original):
        value = match.group("value")
        parsed = local_path_and_suffix(value)
        if parsed is None:
            continue
        raw_path, suffix = parsed
        source = resolve_media_path(raw_path, html_path.parent)
        if not source.exists() or not source.is_file():
            warnings.append(f"Missing local media reference: {value}")
            continue
        if source not in source_to_asset:
            name = asset_name(len(source_to_asset) + 1, source, used_names)
            source_to_asset[source] = name
            copies.append((source, assets_dir / name))
        new_value = f"{assets_dir.name}/{source_to_asset[source]}{suffix}"
        replacements.append((match.start("value"), match.end("value"), new_value))

    rewritten = original
    for start, end, value in reversed(replacements):
        rewritten = rewritten[:start] + value + rewritten[end:]
    return rewritten, copies, warnings


def planned_removals(bundle: Path, keep_names: set[str]) -> list[Path]:
    removals = []
    for child in sorted(bundle.iterdir(), key=lambda path: path.name):
        if child.name in keep_names:
            continue
        removals.append(child)
    return removals


def replace_assets(copies: list[tuple[Path, Path]], assets_dir: Path) -> None:
    temp_dir = assets_dir.with_name(f".{assets_dir.name}.packaging-tmp")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    for source, destination in copies:
        shutil.copy2(source, temp_dir / destination.name)
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    temp_dir.rename(assets_dir)


def remove_paths(paths: list[Path]) -> None:
    for path in paths:
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def verify_references(html_path: Path, bundle: Path) -> list[str]:
    warnings = []
    text = html_path.read_text(encoding="utf-8")
    for match in LOCAL_MEDIA_RE.finditer(text):
        parsed = local_path_and_suffix(match.group("value"))
        if parsed is None:
            continue
        raw_path, _ = parsed
        path = resolve_media_path(raw_path, html_path.parent)
        try:
            path.relative_to(bundle)
        except ValueError:
            warnings.append(f"Reference points outside final bundle: {match.group('value')}")
            continue
        if not path.exists():
            warnings.append(f"Missing final media reference: {match.group('value')}")
    return warnings


def main() -> int:
    args = parse_args()
    bundle = args.bundle.expanduser().resolve()
    html_path = (bundle / args.html).resolve()
    assets_dir = (bundle / args.assets_dir).resolve()

    if not bundle.exists() or not bundle.is_dir():
        raise SystemExit(f"Bundle directory not found: {bundle}")
    if not html_path.exists():
        raise SystemExit(f"HTML file not found: {html_path}")
    try:
        html_path.relative_to(bundle)
        assets_dir.relative_to(bundle)
    except ValueError as exc:
        raise SystemExit("HTML and assets directory must live inside the bundle directory.") from exc

    rewritten_html, copies, warnings = build_html_plan(bundle, html_path, assets_dir)
    quality_warnings = [] if args.skip_article_check else article_quality_warnings(rewritten_html)
    keep_names = set(DEFAULT_KEEP_NAMES)
    keep_names.add(html_path.name)
    keep_names.add(assets_dir.name)
    keep_names.update(args.keep)
    removals = planned_removals(bundle, keep_names)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}")
    print(f"Bundle: {bundle}")
    print(f"HTML: {html_path.name}")
    print(f"Assets: {assets_dir.name}/")
    print(f"Referenced local media to package: {len(copies)}")
    for source, destination in copies:
        print(f"  {os.path.relpath(source, bundle)} -> {os.path.relpath(destination, bundle)}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  {warning}")
    if quality_warnings:
        print("Article quality warnings:")
        for warning in quality_warnings:
            print(f"  {warning}")
    print(f"Top-level paths to remove: {len(removals)}")
    for path in removals:
        print(f"  {path.name}")

    if not args.apply:
        print("No changes written. Re-run with --apply after reviewing this plan.")
        return 0
    if warnings:
        raise SystemExit("Aborting because local media references are missing. Fix the HTML or assets first.")
    if quality_warnings:
        raise SystemExit("Aborting because the HTML still reads like an engineering/workflow report. Rewrite it as a public article first.")

    replace_assets(copies, assets_dir)
    html_path.write_text(rewritten_html, encoding="utf-8")
    remove_paths(removals)
    final_warnings = verify_references(html_path, bundle)
    if final_warnings:
        for warning in final_warnings:
            print(f"Warning: {warning}")
        raise SystemExit("Final HTML reference verification failed.")
    print("Final package ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
