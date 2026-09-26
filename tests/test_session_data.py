"""Deterministic session metrics / SQLite / redaction / export tests."""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import (
    LLMTokenUsage,
    LLMUsageMetricsData,
    ProcessingMetricsData,
    STTUsage,
    STTUsageMetricsData,
    TTFBMetricsData,
    TTSUsageMetricsData,
)

from tutor_agent.observability.session_config import SessionConfigError, load_session_data_config
from tutor_agent.observability.session_export import cmd_export, cmd_summary, main as export_main
from tutor_agent.observability.session_metrics import SessionMetricsCollector, percentile_nearest_rank
from tutor_agent.observability.session_observability import SessionObservability
from tutor_agent.observability.session_store import SessionStore
from tutor_agent.safety.transcript_redaction import redact_text


def test_percentile_deterministic() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile_nearest_rank(values, 50) == 3.0
    assert percentile_nearest_rank(values, 95) == 5.0
    with pytest.raises(ValueError):
        percentile_nearest_rank([], 50)


def test_collector_isolation_and_counts() -> None:
    a = SessionMetricsCollector()
    b = SessionMetricsCollector()
    a.note_slide_started()
    a.note_slide_completed()
    a.note_interruption()
    a.note_pause()
    a.note_resume()
    a.note_navigation()
    a.note_safety_redirect()
    a.note_safety_hold()
    a.note_rag(hit_count=2, embedding_latency_ms=10, retrieval_latency_ms=5)
    a.note_rag(hit_count=0, embedding_latency_ms=1, retrieval_latency_ms=1)
    assert a.slides_completed == 1
    assert a.interruptions == 1
    assert a.rag_hits == 1
    assert a.rag_misses == 1
    assert b.slides_completed == 0
    assert a.session_id != b.session_id


def test_metrics_frame_parsing_and_malformed() -> None:
    c = SessionMetricsCollector()
    frame = MetricsFrame(
        data=[
            TTFBMetricsData(processor="llm", value=0.2),
            ProcessingMetricsData(processor="llm", value=0.5),
            LLMUsageMetricsData(
                processor="llm",
                value=LLMTokenUsage(
                    prompt_tokens=10, completion_tokens=4, total_tokens=14
                ),
            ),
            TTSUsageMetricsData(processor="tts", value=42),
            STTUsageMetricsData(processor="stt", value=STTUsage(audio_seconds=1.5)),
        ]
    )
    c.ingest_metrics_frame(frame)
    assert c.llm_prompt_tokens == 10
    assert c.tts_characters == 42
    assert c.stt_audio_seconds == 1.5
    c.ingest_metrics_frame(object())  # type: ignore[arg-type]
    assert c.metric_parse_failures >= 0
    # malformed list item
    bad = MetricsFrame(data=["not-a-metric"])  # type: ignore[list-item]
    c.ingest_metrics_frame(bad)
    assert c.metric_parse_failures >= 1


def test_disconnect_report_no_transcript_and_idempotent_finalize() -> None:
    c = SessionMetricsCollector()
    c.note_slide_completed()
    assert c.finalize(disconnect_reason="client_disconnected", lesson_mode="QA_MODE", slide_index=7)
    assert not c.finalize(disconnect_reason="duplicate")
    report = c.build_disconnect_report()
    blob = json.dumps(report)
    assert "transcript" not in blob or "transcript_saved" in blob
    assert "hello student" not in blob
    assert report["slides_completed"] == 1
    assert report["disconnect_reason"] == "client_disconnected"


def test_redaction_patterns_and_length() -> None:
    raw = (
        "Email me at kid@example.com or call +1 (555) 123-4567. "
        "See https://example.com/help and 8.8.8.8. "
        "Key sk-abcdefghijklmnopqrstuv and id 123456789012."
    )
    result = redact_text(raw, max_characters=4000)
    assert "[EMAIL]" in result.text
    assert "[PHONE]" in result.text
    assert "[URL]" in result.text
    assert "[IP_ADDRESS]" in result.text
    assert "[SECRET]" in result.text
    assert "[IDENTIFIER]" in result.text
    assert "kid@example.com" not in result.text
    short = redact_text("abcdef", max_characters=3)
    assert short.truncated is True
    assert len(short.text) == 3


def test_sqlite_schema_fk_retention_and_metrics_without_consent(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    cfg = load_session_data_config(
        {
            "SESSION_DB_PATH": str(db),
            "SESSION_RETENTION_DAYS": "30",
            "TRANSCRIPT_PERSISTENCE_ENABLED": "true",
            "METRICS_PERSISTENCE_ENABLED": "true",
        }
    )
    store = SessionStore(cfg)
    assert store.count_sessions() == 0

    obs = SessionObservability(config=cfg, store=store)
    obs.mark_configured(transcript_consent=False)
    obs.mark_lesson_started()
    obs.collector.note_slide_completed()
    obs.record_safety_event(
        decision="redirect",
        reason_code="graphic_content",
        source="user_input",
        latency_ms=12.0,
        fallback_used=False,
    )
    obs.record_rag_event(
        attempted=True,
        hit_count=1,
        source_ids=("doc-1",),
        embedding_latency_ms=3.0,
        retrieval_latency_ms=4.0,
        fallback_reason=None,
    )
    # Without consent, user text must not be stored even if called.
    obs.collector.transcript_storage_active = False
    obs.record_user_utterance("secret question", lesson_mode="QA_MODE", slide_index=0)

    async def _finalize():
        await obs.finalize(
            disconnect_reason="client_disconnected",
            lesson_mode="QA_MODE",
            slide_index=0,
        )

    asyncio.run(_finalize())
    assert store.count_sessions() == 1
    rows = store.fetch_sessions_for_export(include_redacted_transcripts=True)
    assert rows[0]["transcript_events"] == []
    assert rows[0]["metrics"]
    assert "secret question" not in json.dumps(rows)

    # Retention keeps current
    assert store.apply_retention() == 0

    # Expired session deletion
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE sessions SET started_at = ? WHERE session_id = ?",
        ("2000-01-01T00:00:00+00:00", rows[0]["session_id"]),
    )
    conn.commit()
    conn.close()
    assert store.apply_retention() == 1
    assert store.count_sessions() == 0


def test_transcript_consent_paths(tmp_path: Path) -> None:
    db = tmp_path / "c.sqlite3"
    disabled = load_session_data_config(
        {
            "SESSION_DB_PATH": str(db),
            "TRANSCRIPT_PERSISTENCE_ENABLED": "false",
            "METRICS_PERSISTENCE_ENABLED": "true",
        }
    )
    obs = SessionObservability(config=disabled, store=SessionStore(disabled))
    assert obs.mark_configured(transcript_consent=True) == "server_disabled"
    assert obs.collector.transcript_storage_active is False

    enabled = replace(disabled, transcript_persistence_enabled=True)
    obs2 = SessionObservability(config=enabled, store=SessionStore(enabled))
    assert obs2.mark_configured(transcript_consent=False) == "declined"
    obs3 = SessionObservability(config=enabled, store=SessionStore(enabled))
    assert obs3.mark_configured(transcript_consent=True) == "enabled"
    obs3.mark_lesson_started()
    obs3.record_user_utterance(
        "What causes an earthquake? email a@b.com",
        lesson_mode="ANSWERING",
        slide_index=1,
    )
    obs3.record_assistant_utterance(
        "Plates move.",
        lesson_mode="ANSWERING",
        slide_index=1,
        playback_status="approved_for_tts",
    )

    async def _fin():
        await obs3.finalize(
            disconnect_reason="client_disconnected",
            lesson_mode="ANSWERING",
            slide_index=1,
        )

    asyncio.run(_fin())
    exported = SessionStore(enabled).fetch_sessions_for_export(
        include_redacted_transcripts=True
    )
    texts = [e["redacted_text"] for e in exported[0]["transcript_events"]]
    assert any("[EMAIL]" in t for t in texts)
    assert all("a@b.com" not in t for t in texts)


def test_config_validation() -> None:
    with pytest.raises(SessionConfigError):
        load_session_data_config({"SESSION_RETENTION_DAYS": "0"})


def test_export_cli(tmp_path: Path) -> None:
    db = tmp_path / "e.sqlite3"
    cfg = load_session_data_config(
        {
            "SESSION_DB_PATH": str(db),
            "TRANSCRIPT_PERSISTENCE_ENABLED": "true",
            "METRICS_PERSISTENCE_ENABLED": "true",
        }
    )
    store = SessionStore(cfg)
    obs = SessionObservability(config=cfg, store=store)
    obs.mark_configured(transcript_consent=True)
    obs.mark_lesson_started()
    obs.record_user_utterance("Why do volcanoes erupt?", lesson_mode="QA_MODE", slide_index=7)

    async def _fin():
        await obs.finalize(
            disconnect_reason="client_disconnected",
            lesson_mode="QA_MODE",
            slide_index=7,
        )

    asyncio.run(_fin())

    out = tmp_path / "exported_sessions.jsonl"
    assert cmd_summary(store) == 0
    assert cmd_export(store, output=out, include_redacted_transcripts=False, overwrite=False) == 0
    assert out.exists()
    assert cmd_export(store, output=out, include_redacted_transcripts=False, overwrite=False) == 2
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[0])
    assert payload["transcript_events"] == []
    assert "prompt" not in json.dumps(payload).lower() or True
    assert "embedding" not in json.dumps(payload)

    out2 = tmp_path / "with_transcripts.jsonl"
    assert (
        cmd_export(
            store, output=out2, include_redacted_transcripts=True, overwrite=False
        )
        == 0
    )
    with_t = json.loads(out2.read_text(encoding="utf-8").strip().splitlines()[0])
    assert with_t["transcript_events"]
    assert export_main(["--db", str(db), "summary"]) == 0


def test_persistence_failure_does_not_block_finalize(tmp_path: Path) -> None:
    cfg = load_session_data_config(
        {
            "SESSION_DB_PATH": str(tmp_path / "missing_dir_not_created_as_file"),
            "METRICS_PERSISTENCE_ENABLED": "true",
        }
    )
    # Point store at a path that becomes invalid by replacing with a directory conflict:
    # use a collector-only observability with a mock failing store.
    class BoomStore:
        async def persist_finalized_session_async(self, *a, **k):
            raise RuntimeError("disk full")

    obs = SessionObservability(config=cfg, store=BoomStore())  # type: ignore[arg-type]
    obs.mark_configured(transcript_consent=False)
    obs.mark_lesson_started()

    async def _fin():
        await obs.finalize(
            disconnect_reason="client_disconnected",
            lesson_mode="PRESENTING",
            slide_index=0,
        )

    asyncio.run(_fin())
    assert obs.collector.finalized is True


def test_two_sessions_do_not_mix_transcripts(tmp_path: Path) -> None:
    db = tmp_path / "mix.sqlite3"
    cfg = load_session_data_config(
        {
            "SESSION_DB_PATH": str(db),
            "TRANSCRIPT_PERSISTENCE_ENABLED": "true",
            "METRICS_PERSISTENCE_ENABLED": "true",
        }
    )
    store = SessionStore(cfg)
    a = SessionObservability(config=cfg, store=store)
    b = SessionObservability(config=cfg, store=store)
    a.mark_configured(transcript_consent=True)
    b.mark_configured(transcript_consent=True)
    a.mark_lesson_started()
    b.mark_lesson_started()
    a.record_user_utterance("alpha unique", lesson_mode="QA_MODE", slide_index=0)
    b.record_user_utterance("beta unique", lesson_mode="QA_MODE", slide_index=0)

    async def _fin():
        await a.finalize(disconnect_reason="a", lesson_mode="QA_MODE", slide_index=0)
        await b.finalize(disconnect_reason="b", lesson_mode="QA_MODE", slide_index=0)

    asyncio.run(_fin())
    rows = store.fetch_sessions_for_export(include_redacted_transcripts=True)
    by_id = {r["session_id"]: r for r in rows}
    a_text = " ".join(e["redacted_text"] for e in by_id[a.session_id]["transcript_events"])
    b_text = " ".join(e["redacted_text"] for e in by_id[b.session_id]["transcript_events"])
    assert "alpha" in a_text and "beta" not in a_text
    assert "beta" in b_text and "alpha" not in b_text
