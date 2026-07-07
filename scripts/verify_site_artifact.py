#!/usr/bin/env python3
"""Validate the generated dashboard before it is committed.

This script is intentionally offline and repository-safe: it only reads the
generated HTML artifact and checks for deployment mistakes such as mojibake,
missing target cards, failed data cards, and obvious secret/local-path leaks.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


DEFAULT_REQUIRED_TEXT = (
    "每日市場報告",
    "今日市場",
    "市場雷達",
    "標的總覽",
    "三大法人",
    "資料來源",
    "overviewSearch",
    "overviewCount",
)

SENSITIVE_PATTERNS = (
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "secret pattern"),
    (re.compile(r"ghp_[A-Za-z0-9_]{20,}"), "GitHub classic token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{30,}"), "GitHub fine-grained token"),
    (re.compile(r"AIza[0-9A-Za-z_-]{20,}"), "secret pattern"),
    (re.compile(r"(?i)\b(openai_api_key|finmind_token|api[_-]?secret|password\s*=|token\s*=)\b"), "secret-like text"),
    (re.compile(r"(?i)\b([A-Z]:\\Users\\|[A-Z]:\\[^<>'\"]+\\|USER_PLAN)\b"), "local project text"),
)

FAILURE_TEXTS = (
    "資料暫時無法取得",
    "處理失敗",
    "資料擷取失敗",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify generated dashboard HTML.")
    parser.add_argument("--html", default="index.html", help="Generated HTML file to inspect.")
    parser.add_argument("--expected-cards", type=int, default=60, help="Expected target card count.")
    parser.add_argument("--min-size", type=int, default=200_000, help="Minimum acceptable HTML bytes.")
    parser.add_argument("--max-failure-cards", type=int, default=0, help="Allowed failed target cards.")
    return parser.parse_args()


def count_target_cards(html: str) -> int:
    return len(re.findall(r'class="sc target-card(?:\s|")', html))


def main() -> int:
    args = parse_args()
    path = Path(args.html)
    errors: list[str] = []
    warnings: list[str] = []

    if not path.exists():
        print(f"VERIFY_FAIL: missing HTML file: {path}", file=sys.stderr)
        return 1

    raw = path.read_bytes()
    if len(raw) < args.min_size:
        errors.append(f"HTML is unexpectedly small: {len(raw):,} bytes")

    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"VERIFY_FAIL: HTML is not valid UTF-8: {exc}", file=sys.stderr)
        return 1

    if "\ufffd" in html:
        errors.append("HTML contains replacement characters, likely encoding corruption")

    for text in DEFAULT_REQUIRED_TEXT:
        if text not in html:
            errors.append(f"missing required text/marker: {text}")

    for pattern, label in SENSITIVE_PATTERNS:
        if pattern.search(html):
            errors.append(f"possible sensitive/local content leaked: {label}")

    card_count = count_target_cards(html)
    if card_count != args.expected_cards:
        errors.append(f"target card count mismatch: expected {args.expected_cards}, got {card_count}")

    failure_count = sum(html.count(text) for text in FAILURE_TEXTS)
    if failure_count > args.max_failure_cards:
        errors.append(f"failed data card markers exceed limit: {failure_count} > {args.max_failure_cards}")

    na_count = html.count("N/A")
    if na_count:
        warnings.append(f"N/A occurrences: {na_count} (not failed; some markets may legitimately lack fields)")

    print(
        "VERIFY_SUMMARY: "
        f"bytes={len(raw):,}; cards={card_count}; failures={failure_count}; na={na_count}"
    )

    for warning in warnings:
        print(f"VERIFY_WARN: {warning}")

    if errors:
        for error in errors:
            print(f"VERIFY_FAIL: {error}", file=sys.stderr)
        return 1

    print("VERIFY_OK: site artifact passed checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
