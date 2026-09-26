"""CLI for local learning-flywheel friction analysis (no OpenAI by default)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tutor_agent.evaluation.curriculum_recommendations import recommendations_from_signals, recommendations_to_dicts
from tutor_agent.evaluation.friction_analyzer import (
    DEFAULT_JACCARD_THRESHOLD,
    build_friction_report,
    report_contains_transcript_text,
)
from tutor_agent.observability.session_config import load_session_data_config
from tutor_agent.observability.session_store import SessionStore, SessionStoreError


class FlywheelError(Exception):
    pass


def _parse_utc_date(value: str, *, end_of_day: bool = False) -> datetime:
    try:
        if "T" in value:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
    except ValueError as exc:
        raise FlywheelError(f"Invalid date: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _atomic_write_json(path: Path, payload: Dict[str, Any], *, overwrite: bool) -> None:
    path = path.resolve()
    if path.exists() and not overwrite:
        raise FlywheelError(f"Refusing to overwrite existing report without --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix=".flywheel_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _write_local_appendix(path: Path, examples: List[Dict[str, Any]], *, overwrite: bool) -> None:
    path = path.resolve()
    if path.exists() and not overwrite:
        raise FlywheelError(f"Refusing to overwrite appendix without --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    payload = {
        "sensitive_local_review_material": True,
        "warning": "Already-redacted transcript examples for local human review only. Never send to OpenAI.",
        "examples": examples,
    }
    fd, tmp_name = tempfile.mkstemp(prefix=".appendix_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def analyze(
    *,
    db_path: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    output: Optional[str] = None,
    overwrite: bool = False,
    include_local_redacted_examples: bool = False,
    jaccard_threshold: float = DEFAULT_JACCARD_THRESHOLD,
    sessions_override: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    start_dt = _parse_utc_date(start) if start else None
    end_dt = _parse_utc_date(end, end_of_day=True) if end else None
    if start_dt and end_dt and start_dt > end_dt:
        raise FlywheelError("Invalid date range: start must be <= end")

    if sessions_override is not None:
        sessions = list(sessions_override)
    else:
        config = load_session_data_config()
        if db_path:
            from dataclasses import replace

            config = replace(config, session_db_path=db_path)
        try:
            store = SessionStore(config)
            sessions = store.list_sessions_for_analysis(start_utc=start_dt, end_utc=end_dt)
        except SessionStoreError as exc:
            raise FlywheelError("Session database unavailable") from exc
        except Exception as exc:  # noqa: BLE001
            raise FlywheelError("Session database unavailable") from exc

    # Collect signals for recommendations
    from tutor_agent.evaluation.friction_analyzer import analyze_session

    all_signals = []
    for s in sessions:
        sf, _ = analyze_session(s, jaccard_threshold=jaccard_threshold)
        all_signals.extend(sf.signals)
    recs = recommendations_to_dicts(recommendations_from_signals(all_signals))

    configuration = {
        "jaccard_threshold": jaccard_threshold,
        "start_utc": start_dt.isoformat() if start_dt else None,
        "end_utc": end_dt.isoformat() if end_dt else None,
        "include_local_redacted_examples": False,  # never in primary report
    }
    report, examples = build_friction_report(
        sessions,
        jaccard_threshold=jaccard_threshold,
        recommendations=recs,
        configuration=configuration,
    )
    payload = report.to_dict()
    if report_contains_transcript_text(payload):
        raise FlywheelError("Internal error: primary report must not contain transcript text")

    out_path = Path(output) if output else Path("data/flywheel/friction-report.json")
    _atomic_write_json(out_path, payload, overwrite=overwrite)

    if include_local_redacted_examples and examples:
        appendix = out_path.with_name(out_path.stem + ".local-redacted-appendix.json")
        _write_local_appendix(appendix, examples, overwrite=overwrite)
        # Never print appendix path contents; only note that it was written.
        sys.stderr.write("Local redacted appendix written (sensitive; not printed).\n")

    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flywheel", description="Local learning flywheel tools")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze_p = sub.add_parser("analyze", help="Analyze consented session friction locally")
    analyze_p.add_argument("--db-path", default=None, help="Override SESSION_DB_PATH")
    analyze_p.add_argument("--start", default=None, help="UTC start date YYYY-MM-DD")
    analyze_p.add_argument("--end", default=None, help="UTC end date YYYY-MM-DD")
    analyze_p.add_argument(
        "--output",
        default="data/flywheel/friction-report.json",
        help="Output JSON path",
    )
    analyze_p.add_argument("--overwrite", action="store_true")
    analyze_p.add_argument(
        "--include-local-redacted-examples",
        action="store_true",
        help="Write sensitive local appendix (never sent to OpenAI; never printed)",
    )
    analyze_p.add_argument(
        "--jaccard-threshold",
        type=float,
        default=DEFAULT_JACCARD_THRESHOLD,
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            analyze(
                db_path=args.db_path,
                start=args.start,
                end=args.end,
                output=args.output,
                overwrite=args.overwrite,
                include_local_redacted_examples=args.include_local_redacted_examples,
                jaccard_threshold=args.jaccard_threshold,
            )
            print(f"Wrote friction report to {args.output}")
            return 0
    except FlywheelError as exc:
        print(f"flywheel error: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
