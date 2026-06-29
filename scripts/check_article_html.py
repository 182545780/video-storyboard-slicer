#!/usr/bin/env python3
"""Fail fast when a final HTML page still reads like an engineering report."""

from __future__ import annotations

import argparse
import re
from html.parser import HTMLParser
from pathlib import Path


BANNED_PATTERNS = [
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


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.text_parts: list[str] = []
        self.img_count = 0
        self.h1_count = 0
        self._current_tag: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        self._current_tag = tag
        if tag in {"script", "style", "template"}:
            self.hidden_depth += 1
        if tag == "img":
            self.img_count += 1
        if tag == "h1":
            self.h1_count += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "template"} and self.hidden_depth:
            self.hidden_depth -= 1
        self._current_tag = None

    def handle_data(self, data: str) -> None:
        if self.hidden_depth:
            return
        text = data.strip()
        if text:
            self.text_parts.append(text)

    @property
    def visible_text(self) -> str:
        return " ".join(self.text_parts)


def check_html(path: Path) -> list[str]:
    parser = VisibleTextParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    text = parser.visible_text
    failures: list[str] = []

    for pattern in BANNED_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            failures.append(f"Visible engineering/report term found: {pattern}")

    if parser.h1_count < 1:
        failures.append("Missing an article headline (`h1`).")
    if parser.img_count < 1:
        failures.append("Missing original-video image evidence (`img`).")
    if len(text) < 700:
        failures.append("Visible article text is too short to feel like a finished public article.")
    if len(re.findall(r"<h2\b", path.read_text(encoding="utf-8", errors="replace"), flags=re.IGNORECASE)) < 2:
        failures.append("Expected at least two narrative sections (`h2`).")
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check that summary.html is article-like, not a dry workflow report.")
    parser.add_argument("html", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.html.exists():
        raise SystemExit(f"HTML file not found: {args.html}")
    failures = check_html(args.html)
    if failures:
        print("Article quality check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Article quality check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
