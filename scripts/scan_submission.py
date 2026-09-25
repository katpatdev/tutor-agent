#!/usr/bin/env python3
"""Scan a submission directory or ZIP for disallowed private artifacts.

Never prints secret values — only path, optional line number, and reason.
Exit code 0 = clean; 1 = findings; 2 = usage/IO error.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterable, List, Tuple

DISALLOWED_NAMES = {
    ".env",
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
}

DISALLOWED_SUFFIXES = {
    ".sqlite3",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".pyc",
}

DISALLOWED_NAME_SUBSTRINGS = (
    "tutor_sessions",
    "friction-report",
    "prompt_candidates",
    "local-redacted-appendix",
    "live_test_report",
    "exported_sessions",
    "eval_reports",
)

# Placeholder-safe: match likely live OpenAI keys, not empty .env.example lines.
SECRET_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("openai_sk_live_or_proj_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("bearer_token", re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._\-]+")),
]

TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".json",
    ".md",
    ".txt",
    ".toml",
    ".yml",
    ".yaml",
    ".env",
    ".example",
    ".html",
    ".css",
    ".lock",
}


Finding = Tuple[str, str, str]  # path, location, reason


def _is_allowed_env_example(path: str) -> bool:
    name = Path(path).name
    return name == ".env.example" or name.endswith(".env.example")


def _path_is_disallowed(rel: str) -> str | None:
    parts = Path(rel).parts
    for part in parts:
        if part in DISALLOWED_NAMES:
            if part == "dist" and "frontend" in parts:
                return f"disallowed path component: {part}"
            if part != "dist":
                return f"disallowed path component: {part}"
        if part.startswith(".") and part in {".git", ".venv", ".pytest_cache", ".mypy_cache"}:
            return f"disallowed path component: {part}"
    name = Path(rel).name
    if name == ".env" or name.startswith(".env.") and name != ".env.example":
        if not _is_allowed_env_example(rel):
            return "environment secret file"
    lower = rel.lower().replace("\\", "/")
    for suf in DISALLOWED_SUFFIXES:
        if lower.endswith(suf):
            return f"disallowed suffix: {suf}"
    for sub in DISALLOWED_NAME_SUBSTRINGS:
        if sub in lower:
            return f"disallowed artifact name contains: {sub}"
    return None


def _scan_text(path: str, data: bytes) -> List[Finding]:
    out: List[Finding] = []
    # Unit tests intentionally contain synthetic secret-shaped strings for redaction
    # coverage. Still enforce path-based disallow rules; skip content patterns here.
    if path.replace("\\", "/").startswith("tests/") or "/tests/" in path.replace("\\", "/"):
        return out
    if _is_allowed_env_example(path):
        # Allow empty OPENAI_API_KEY= placeholders only.
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            return out
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("OPENAI_API_KEY=") and len(stripped) > len("OPENAI_API_KEY="):
                # Non-empty value in example is suspicious
                val = stripped.split("=", 1)[1].strip()
                if val and val not in {'""', "''"}:
                    out.append((path, f"line {i}", "non-empty API key in example file"))
            for reason, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    out.append((path, f"line {i}", reason))
        return out

    suffix = Path(path).suffix.lower()
    if suffix not in TEXT_SUFFIXES and Path(path).name not in {".env", ".python-version", ".nvmrc"}:
        return out
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return out
    for i, line in enumerate(text.splitlines(), start=1):
        for reason, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                out.append((path, f"line {i}", reason))
    return out


def scan_directory(root: Path) -> List[Finding]:
    findings: List[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        reason = _path_is_disallowed(rel)
        if reason:
            findings.append((rel, "-", reason))
            continue
        findings.extend(_scan_text(rel, path.read_bytes()))
    return findings


def scan_zip(zip_path: Path) -> List[Finding]:
    findings: List[Finding] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            name = info.filename.replace("\\", "/")
            if name.endswith("/"):
                continue
            reason = _path_is_disallowed(name)
            if reason:
                findings.append((name, "-", reason))
                continue
            data = zf.read(info)
            findings.extend(_scan_text(name, data))
    return findings


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan submission artifacts for private data")
    parser.add_argument("target", help="Directory or ZIP to scan")
    args = parser.parse_args(argv)
    target = Path(args.target)
    try:
        if target.is_dir():
            findings = scan_directory(target)
        elif target.is_file() and target.suffix.lower() == ".zip":
            findings = scan_zip(target)
        else:
            print(f"scan_submission error: not a directory or zip: {target}", file=sys.stderr)
            return 2
    except OSError as exc:
        print(f"scan_submission error: {exc}", file=sys.stderr)
        return 2

    if not findings:
        print(f"PASS: no disallowed artifacts in {target}")
        return 0

    print(f"FAIL: {len(findings)} finding(s) in {target}")
    for path, loc, reason in findings:
        print(f" - {path} ({loc}): {reason}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
