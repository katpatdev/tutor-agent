"""CLI for sanitized session export (local only; not an HTTP endpoint)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from tutor_agent.observability.session_config import load_session_data_config
from tutor_agent.observability.session_store import SessionStore


def _restrictive_chmod(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def cmd_summary(store: SessionStore) -> int:
    sessions = store.fetch_sessions_for_export(include_redacted_transcripts=False)
    consented = sum(1 for s in sessions if s.get("transcript_consent"))
    print(f"sessions={len(sessions)}")
    print(f"consented_transcript_sessions={consented}")
    print(f"database={store.path}")
    return 0


def cmd_export(
    store: SessionStore,
    *,
    output: Path,
    include_redacted_transcripts: bool,
    overwrite: bool,
) -> int:
    if output.exists() and not overwrite:
        print(
            f"Refusing to overwrite existing file without --overwrite: {output}",
            file=sys.stderr,
        )
        return 2

    rows = store.fetch_sessions_for_export(
        include_redacted_transcripts=include_redacted_transcripts
    )
    if include_redacted_transcripts:
        rows = [r for r in rows if r.get("transcript_consent")]

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=output.name + ".", suffix=".tmp", dir=str(output.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for row in rows:
                # Strip any accidental sensitive keys if present.
                safe = {
                    "session_id": row["session_id"],
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                    "duration_seconds": row["duration_seconds"],
                    "disconnect_reason": row["disconnect_reason"],
                    "transcript_consent": bool(row["transcript_consent"]),
                    "final_lesson_mode": row["final_lesson_mode"],
                    "final_slide_index": row["final_slide_index"],
                    "metrics": row.get("metrics", []),
                    "safety_events": row.get("safety_events", []),
                    "rag_events": row.get("rag_events", []),
                    "transcript_events": [],
                }
                if include_redacted_transcripts:
                    safe["transcript_events"] = row.get("transcript_events", [])
                handle.write(json.dumps(safe, ensure_ascii=False) + "\n")
        os.replace(tmp_path, output)
        _restrictive_chmod(output)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    print(f"exported_sessions={len(rows)}")
    print(f"output={output}")
    print(
        "include_redacted_transcripts="
        + ("true" if include_redacted_transcripts else "false")
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session_export",
        description="Local sanitized session export (not a public HTTP API).",
    )
    parser.add_argument(
        "--db",
        help="Override SESSION_DB_PATH for this invocation",
        default=None,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("summary", help="Print session counts without transcript text")

    export_p = sub.add_parser("export", help="Export sessions as JSONL")
    export_p.add_argument("--output", required=True, type=Path)
    export_p.add_argument(
        "--include-redacted-transcripts",
        action="store_true",
        help="Include already-redacted transcripts for consented sessions only",
    )
    export_p.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting an existing export file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_session_data_config()
    if args.db:
        from dataclasses import replace

        config = replace(config, session_db_path=args.db)
    store = SessionStore(config)
    if args.command == "summary":
        return cmd_summary(store)
    if args.command == "export":
        return cmd_export(
            store,
            output=args.output,
            include_redacted_transcripts=args.include_redacted_transcripts,
            overwrite=args.overwrite,
        )
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
