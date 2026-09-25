"""Local SQLite session store (metrics + consented redacted transcripts)."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from session_config import SessionDataConfig
from session_metrics import SessionMetricsCollector


SCHEMA_VERSION = 2
APPLICATION_VERSION = "tutor-agent-iter8"
CURRICULUM_VERSION = "natural-disasters-v1"


class SessionStoreError(Exception):
    pass


@dataclass(frozen=True)
class TranscriptEventRecord:
    sequence: int
    timestamp: float
    role: str  # user | assistant
    event_kind: str
    lesson_mode: str
    slide_index: int
    redacted_text: str
    text_character_count: int
    is_safety_template: bool
    playback_status: str  # approved_for_tts | playback_completion_unknown | playback_completed


@dataclass(frozen=True)
class SafetyEventRecord:
    timestamp: float
    decision: str
    reason_code: str
    source: str
    latency_ms: Optional[float]
    fallback_used: bool


@dataclass(frozen=True)
class RagEventRecord:
    timestamp: float
    attempted: bool
    hit_count: int
    source_ids: tuple[str, ...]
    embedding_latency_ms: Optional[float]
    retrieval_latency_ms: Optional[float]
    fallback_reason: Optional[str]


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _restrictive_chmod(path: Path, mode: int = 0o600) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


class SessionStore:
    """Process-local SQLite persistence. Safe to share; writes serialized."""

    def __init__(self, config: SessionDataConfig):
        self._config = config
        self._path = Path(config.session_db_path)
        self._lock = threading.RLock()
        self._init_db()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._path,
            timeout=5.0,
            check_same_thread=False,
            isolation_level=None,  # manual transactions
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.Error:
            pass
        return conn

    def _init_db(self) -> None:
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass
        with self._lock:
            created = not self._path.exists()
            conn = self._connect()
            try:
                if created:
                    _restrictive_chmod(self._path)
                version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if version == 0:
                    # executescript auto-commits; do not wrap in an explicit transaction.
                    self._create_schema(conn)
                    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                elif version == SCHEMA_VERSION:
                    # Idempotent: ensure v2 columns exist (no-op if already present).
                    self._ensure_v2_columns(conn)
                elif version == 1:
                    self._migrate_v1_to_v2(conn)
                elif version > SCHEMA_VERSION:
                    raise SessionStoreError(
                        f"Unsupported future session DB schema version {version}"
                    )
                else:
                    raise SessionStoreError(
                        f"Unsupported session DB schema version {version}; expected 0-2"
                    )
            finally:
                conn.close()

    def _ensure_v2_columns(self, conn: sqlite3.Connection) -> None:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        needed = {
            "tutor_prompt_version": "TEXT",
            "tutor_prompt_hash": "TEXT",
            "curriculum_version": "TEXT",
            "application_schema_version": "INTEGER",
        }
        for name, coltype in needed.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {coltype}")

    def _migrate_v1_to_v2(self, conn: sqlite3.Connection) -> None:
        """Upgrade schema 1 → 2 in a single transaction. Idempotent if re-run after success."""
        conn.execute("BEGIN")
        try:
            self._ensure_v2_columns(conn)
            # Backfill content-free metadata only; never touch transcript text.
            conn.execute(
                """
                UPDATE sessions
                SET application_schema_version = COALESCE(application_schema_version, 2),
                    curriculum_version = COALESCE(curriculum_version, ?),
                    tutor_prompt_version = COALESCE(tutor_prompt_version, 'v1'),
                    tutor_prompt_hash = COALESCE(tutor_prompt_hash, '')
                """,
                (CURRICULUM_VERSION,),
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_seconds REAL,
                disconnect_reason TEXT,
                transcript_consent INTEGER NOT NULL DEFAULT 0,
                transcript_storage_active INTEGER NOT NULL DEFAULT 0,
                final_lesson_mode TEXT,
                final_slide_index INTEGER,
                schema_version INTEGER NOT NULL,
                completion_status TEXT,
                application_version TEXT,
                tutor_prompt_version TEXT,
                tutor_prompt_hash TEXT,
                curriculum_version TEXT,
                application_schema_version INTEGER
            );

            CREATE TABLE IF NOT EXISTS transcript_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                event_sequence INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                role TEXT NOT NULL,
                event_kind TEXT NOT NULL,
                lesson_mode TEXT,
                slide_index INTEGER,
                redacted_text TEXT NOT NULL,
                text_character_count INTEGER NOT NULL,
                is_safety_template INTEGER NOT NULL DEFAULT 0,
                playback_status TEXT NOT NULL,
                UNIQUE(session_id, event_sequence)
            );

            CREATE TABLE IF NOT EXISTS metric_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                numeric_value REAL,
                unit TEXT,
                service_name TEXT
            );

            CREATE TABLE IF NOT EXISTS safety_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                source TEXT NOT NULL,
                latency_ms REAL,
                fallback_used INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS rag_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                attempted INTEGER NOT NULL,
                hit_count INTEGER NOT NULL,
                source_ids_json TEXT NOT NULL,
                embedding_latency_ms REAL,
                retrieval_latency_ms REAL,
                fallback_reason TEXT
            );
            """
        )

    def apply_retention(self) -> int:
        cutoff = _utc_now() - timedelta(days=self._config.session_retention_days)
        cutoff_iso = cutoff.isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                cur = conn.execute(
                    "DELETE FROM sessions WHERE started_at < ?",
                    (cutoff_iso,),
                )
                deleted = cur.rowcount if cur.rowcount is not None else 0
                conn.execute("COMMIT")
                return max(0, deleted)
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def persist_finalized_session(
        self,
        collector: SessionMetricsCollector,
        *,
        transcript_events: Sequence[TranscriptEventRecord] = (),
        safety_events: Sequence[SafetyEventRecord] = (),
        rag_events: Sequence[RagEventRecord] = (),
        persist_metrics: bool = True,
        persist_transcripts: bool = False,
        tutor_prompt_version: Optional[str] = None,
        tutor_prompt_hash: Optional[str] = None,
        curriculum_version: Optional[str] = None,
    ) -> None:
        report = collector.build_disconnect_report()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sessions (
                        session_id, started_at, ended_at, duration_seconds,
                        disconnect_reason, transcript_consent, transcript_storage_active,
                        final_lesson_mode, final_slide_index, schema_version,
                        completion_status, application_version,
                        tutor_prompt_version, tutor_prompt_hash,
                        curriculum_version, application_schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        collector.session_id,
                        report["started_at"],
                        report["ended_at"],
                        report["duration_seconds"],
                        collector.disconnect_reason,
                        1 if collector.transcript_consent else 0,
                        1 if collector.transcript_storage_active else 0,
                        collector.lesson_mode,
                        collector.final_slide_index,
                        SCHEMA_VERSION,
                        "completed",
                        APPLICATION_VERSION,
                        tutor_prompt_version,
                        tutor_prompt_hash,
                        curriculum_version or CURRICULUM_VERSION,
                        SCHEMA_VERSION,
                    ),
                )

                if persist_metrics:
                    self._insert_metric_summaries(conn, collector)
                    for ev in safety_events:
                        conn.execute(
                            """
                            INSERT INTO safety_events (
                                session_id, timestamp, decision, reason_code, source,
                                latency_ms, fallback_used
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                collector.session_id,
                                datetime.fromtimestamp(ev.timestamp, tz=timezone.utc).isoformat(),
                                ev.decision,
                                ev.reason_code,
                                ev.source,
                                ev.latency_ms,
                                1 if ev.fallback_used else 0,
                            ),
                        )
                    for ev in rag_events:
                        conn.execute(
                            """
                            INSERT INTO rag_events (
                                session_id, timestamp, attempted, hit_count, source_ids_json,
                                embedding_latency_ms, retrieval_latency_ms, fallback_reason
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                collector.session_id,
                                datetime.fromtimestamp(ev.timestamp, tz=timezone.utc).isoformat(),
                                1 if ev.attempted else 0,
                                ev.hit_count,
                                json.dumps(list(ev.source_ids)),
                                ev.embedding_latency_ms,
                                ev.retrieval_latency_ms,
                                ev.fallback_reason,
                            ),
                        )

                if persist_transcripts and collector.transcript_storage_active:
                    for ev in transcript_events:
                        conn.execute(
                            """
                            INSERT INTO transcript_events (
                                session_id, event_sequence, timestamp, role, event_kind,
                                lesson_mode, slide_index, redacted_text, text_character_count,
                                is_safety_template, playback_status
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                collector.session_id,
                                ev.sequence,
                                datetime.fromtimestamp(ev.timestamp, tz=timezone.utc).isoformat(),
                                ev.role,
                                ev.event_kind,
                                ev.lesson_mode,
                                ev.slide_index,
                                ev.redacted_text,
                                ev.text_character_count,
                                1 if ev.is_safety_template else 0,
                                ev.playback_status,
                            ),
                        )

                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def _insert_metric_summaries(
        self, conn: sqlite3.Connection, collector: SessionMetricsCollector
    ) -> None:
        now = _utc_now().isoformat()
        sid = collector.session_id

        def add(name: str, value: float, unit: str, service: str = "session") -> None:
            conn.execute(
                """
                INSERT INTO metric_events (session_id, timestamp, metric_name, numeric_value, unit, service_name)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sid, now, name, value, unit, service),
            )

        add("slides_completed", collector.slides_completed, "count")
        add("interruptions", collector.interruptions, "count")
        add("pauses", collector.pauses, "count")
        add("resumes", collector.resumes, "count")
        add("navigations", collector.navigations, "count")
        add("safety_redirects", collector.safety_redirects, "count")
        add("safety_holds", collector.safety_holds, "count")
        add("rag_hits", collector.rag_hits, "count")
        add("rag_misses", collector.rag_misses, "count")
        add("llm_prompt_tokens", collector.llm_prompt_tokens, "tokens", "llm")
        add("llm_completion_tokens", collector.llm_completion_tokens, "tokens", "llm")
        add("llm_total_tokens", collector.llm_total_tokens, "tokens", "llm")
        add("tts_characters", collector.tts_characters, "characters", "tts")
        add("stt_audio_seconds", collector.stt_audio_seconds, "seconds", "stt")
        add("duration_seconds", collector.duration_seconds(), "seconds")

    async def persist_finalized_session_async(self, *args, **kwargs) -> None:
        await asyncio.to_thread(self.persist_finalized_session, *args, **kwargs)

    async def apply_retention_async(self) -> int:
        return await asyncio.to_thread(self.apply_retention)

    def count_sessions(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()
                return int(row["c"])
            finally:
                conn.close()

    def list_sessions_for_analysis(
        self,
        *,
        start_utc: Optional[datetime] = None,
        end_utc: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Load session rows + related events for local friction analysis.

        Never prints transcript text. Caller must respect consent before using
        redacted transcript fields.
        """
        with self._lock:
            conn = self._connect()
            try:
                clauses: List[str] = []
                params: List[Any] = []
                if start_utc is not None:
                    clauses.append("started_at >= ?")
                    params.append(start_utc.astimezone(timezone.utc).isoformat())
                if end_utc is not None:
                    clauses.append("started_at < ?")
                    params.append(end_utc.astimezone(timezone.utc).isoformat())
                where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
                sessions = conn.execute(
                    f"SELECT * FROM sessions{where} ORDER BY started_at ASC",
                    params,
                ).fetchall()
                out: List[Dict[str, Any]] = []
                for row in sessions:
                    item: Dict[str, Any] = dict(row)
                    sid = item["session_id"]
                    item["metrics"] = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT metric_name, numeric_value, unit, service_name "
                            "FROM metric_events WHERE session_id = ?",
                            (sid,),
                        ).fetchall()
                    ]
                    item["safety_events"] = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT timestamp, decision, reason_code, source, latency_ms, fallback_used "
                            "FROM safety_events WHERE session_id = ?",
                            (sid,),
                        ).fetchall()
                    ]
                    item["rag_events"] = [
                        {
                            "timestamp": r["timestamp"],
                            "attempted": bool(r["attempted"]),
                            "hit_count": r["hit_count"],
                            "source_ids": json.loads(r["source_ids_json"] or "[]"),
                            "embedding_latency_ms": r["embedding_latency_ms"],
                            "retrieval_latency_ms": r["retrieval_latency_ms"],
                            "fallback_reason": r["fallback_reason"],
                        }
                        for r in conn.execute(
                            "SELECT * FROM rag_events WHERE session_id = ?",
                            (sid,),
                        ).fetchall()
                    ]
                    # Only attach redacted transcript rows when consent was given.
                    if item.get("transcript_consent"):
                        item["transcript_events"] = [
                            {
                                "event_sequence": r["event_sequence"],
                                "timestamp": r["timestamp"],
                                "role": r["role"],
                                "event_kind": r["event_kind"],
                                "lesson_mode": r["lesson_mode"],
                                "slide_index": r["slide_index"],
                                "redacted_text": r["redacted_text"],
                                "text_character_count": r["text_character_count"],
                                "is_safety_template": bool(r["is_safety_template"]),
                                "playback_status": r["playback_status"],
                            }
                            for r in conn.execute(
                                "SELECT * FROM transcript_events WHERE session_id = ? "
                                "ORDER BY event_sequence ASC",
                                (sid,),
                            ).fetchall()
                        ]
                    else:
                        item["transcript_events"] = []
                    out.append(item)
                return out
            finally:
                conn.close()

    def fetch_sessions_for_export(
        self, *, include_redacted_transcripts: bool
    ) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                sessions = conn.execute(
                    "SELECT * FROM sessions ORDER BY started_at ASC"
                ).fetchall()
                out: List[Dict[str, Any]] = []
                for row in sessions:
                    item: Dict[str, Any] = dict(row)
                    sid = item["session_id"]
                    item["metrics"] = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT metric_name, numeric_value, unit, service_name, timestamp "
                            "FROM metric_events WHERE session_id = ?",
                            (sid,),
                        ).fetchall()
                    ]
                    item["safety_events"] = [
                        {
                            "timestamp": r["timestamp"],
                            "decision": r["decision"],
                            "reason_code": r["reason_code"],
                            "source": r["source"],
                            "latency_ms": r["latency_ms"],
                            "fallback_used": bool(r["fallback_used"]),
                        }
                        for r in conn.execute(
                            "SELECT * FROM safety_events WHERE session_id = ?",
                            (sid,),
                        ).fetchall()
                    ]
                    item["rag_events"] = [
                        {
                            "timestamp": r["timestamp"],
                            "attempted": bool(r["attempted"]),
                            "hit_count": r["hit_count"],
                            "source_ids": json.loads(r["source_ids_json"] or "[]"),
                            "embedding_latency_ms": r["embedding_latency_ms"],
                            "retrieval_latency_ms": r["retrieval_latency_ms"],
                            "fallback_reason": r["fallback_reason"],
                        }
                        for r in conn.execute(
                            "SELECT * FROM rag_events WHERE session_id = ?",
                            (sid,),
                        ).fetchall()
                    ]
                    if include_redacted_transcripts and item.get("transcript_consent"):
                        item["transcript_events"] = [
                            {
                                "event_sequence": r["event_sequence"],
                                "timestamp": r["timestamp"],
                                "role": r["role"],
                                "event_kind": r["event_kind"],
                                "lesson_mode": r["lesson_mode"],
                                "slide_index": r["slide_index"],
                                "redacted_text": r["redacted_text"],
                                "text_character_count": r["text_character_count"],
                                "is_safety_template": bool(r["is_safety_template"]),
                                "playback_status": r["playback_status"],
                            }
                            for r in conn.execute(
                                "SELECT * FROM transcript_events WHERE session_id = ? "
                                "ORDER BY event_sequence ASC",
                                (sid,),
                            ).fetchall()
                        ]
                    else:
                        item["transcript_events"] = []
                    out.append(item)
                return out
            finally:
                conn.close()
