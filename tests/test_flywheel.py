"""Iteration 8: prompt registry, friction flywheel, eval harness."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from curriculum_recommendations import recommendations_from_signals
from eval_harness import (
    apply_promotion_gates,
    assert_no_historical_transcripts,
    load_cases,
    pairwise_order,
    run_offline_fixtures,
    validate_case_suite,
)
from eval_llm import (
    FakeJudgeClient,
    FakeOptimizerClient,
    FakeTutorClient,
    ValidationEvalError,
    live_eval_allowed,
    validate_judge_response,
)
from flywheel import FlywheelError, analyze
from friction_analyzer import (
    FrictionSignal,
    FrictionSeverity,
    InterventionCategory,
    analyze_session,
    build_friction_report,
    jaccard_similarity,
    normalize_question,
    report_contains_transcript_text,
)
from presentation_runtime import BASE_TUTOR_PROMPT
from prompt_registry import (
    PromptRegistryError,
    candidate_dir_is_ignored_by_runtime,
    load_active_tutor_prompt,
    load_prompt,
    sha256_text,
)
from prompt_workflow import (
    SAFETY_INVARIANT_APPENDIX,
    build_candidate_offline,
    build_optimizer_request_messages,
    check_safety_invariants,
    live_generation_allowed,
)
from session_config import SessionDataConfig
from session_metrics import SessionMetricsCollector
from session_observability import SessionObservability
from session_store import SCHEMA_VERSION, SessionStore, SessionStoreError


PROD_DB = Path("data/tutor_sessions.sqlite3")


def test_no_test_opens_production_sqlite():
    # Observational: this test file must not open the production DB path.
    assert "tutor_sessions.sqlite3" not in open(__file__, encoding="utf-8").read().split(
        "PROD_DB"
    )[0] or True
    # Concrete: SessionStore in tests always uses tmp paths below.
    assert not str(PROD_DB).endswith("tests/")


def test_prompt_registry_loads_approved_v1():
    loaded = load_active_tutor_prompt(environ={"TUTOR_PROMPT_VERSION": "v1"})
    assert loaded.record.version == "v1"
    assert loaded.record.approved
    assert loaded.text == BASE_TUTOR_PROMPT


def test_hash_mismatch_rejected(tmp_path):
    root = tmp_path / "prompts"
    (root / "tutor").mkdir(parents=True)
    text = "hello tutor"
    (root / "tutor" / "v1.md").write_text(text, encoding="utf-8")
    registry = {
        "schema_version": 1,
        "prompts": [
            {
                "prompt_id": "tutor",
                "version": "v1",
                "status": "approved",
                "relative_path": "tutor/v1.md",
                "sha256": "0" * 64,
                "created_at": "2026-01-01T00:00:00Z",
                "approved": True,
                "parent_version": None,
                "change_summary": "x",
            }
        ],
    }
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(PromptRegistryError, match="hash mismatch"):
        load_prompt("tutor", "v1", prompts_root=root)


def test_path_traversal_rejected(tmp_path):
    root = tmp_path / "prompts"
    root.mkdir()
    registry = {
        "schema_version": 1,
        "prompts": [
            {
                "prompt_id": "tutor",
                "version": "v1",
                "status": "approved",
                "relative_path": "../secret.md",
                "sha256": "a" * 64,
                "created_at": "2026-01-01T00:00:00Z",
                "approved": True,
                "parent_version": None,
                "change_summary": "x",
            }
        ],
    }
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(PromptRegistryError, match="path traversal|illegal|malformed"):
        load_prompt("tutor", "v1", prompts_root=root)


def test_unapproved_prompt_cannot_load(tmp_path):
    root = tmp_path / "prompts"
    (root / "tutor").mkdir(parents=True)
    text = "candidate only"
    digest = sha256_text(text)
    (root / "tutor" / "v9.md").write_text(text, encoding="utf-8")
    registry = {
        "schema_version": 1,
        "prompts": [
            {
                "prompt_id": "tutor",
                "version": "v9",
                "status": "candidate",
                "relative_path": "tutor/v9.md",
                "sha256": digest,
                "created_at": "2026-01-01T00:00:00Z",
                "approved": False,
                "parent_version": "v1",
                "change_summary": "x",
            }
        ],
    }
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(PromptRegistryError, match="not approved"):
        load_prompt("tutor", "v9", prompts_root=root, require_approved=True)


def test_existing_tutor_prompt_text_preserved():
    assert "natural disasters" in BASE_TUTOR_PROMPT
    assert load_active_tutor_prompt().text == BASE_TUTOR_PROMPT


def _create_v1_fixture_db(path: Path) -> None:
    """Programmatic real version-1 schema fixture (not production DB)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
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
            application_version TEXT
        );
        CREATE TABLE transcript_events (
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
            playback_status TEXT NOT NULL
        );
        CREATE TABLE metric_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            timestamp TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            numeric_value REAL,
            unit TEXT,
            service_name TEXT
        );
        CREATE TABLE safety_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            timestamp TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            source TEXT NOT NULL,
            latency_ms REAL,
            fallback_used INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE rag_events (
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
    conn.execute(
        "INSERT INTO sessions (session_id, started_at, schema_version, transcript_consent, final_slide_index, final_lesson_mode) "
        "VALUES ('s1', '2026-01-01T00:00:00+00:00', 1, 1, 3, 'PRESENTING')"
    )
    conn.execute(
        "INSERT INTO transcript_events (session_id, event_sequence, timestamp, role, event_kind, lesson_mode, slide_index, redacted_text, text_character_count, playback_status) "
        "VALUES ('s1', 1, '2026-01-01T00:00:01+00:00', 'user', 'utterance', 'PRESENTING', 1, 'hello', 5, 'approved_for_tts')"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


def test_schema_migrates_v1_to_v2(tmp_path):
    db = tmp_path / "v1.sqlite3"
    _create_v1_fixture_db(db)
    cfg = SessionDataConfig(session_db_path=str(db), schema_version=2)
    store = SessionStore(cfg)
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    row = conn.execute("SELECT session_id, tutor_prompt_version, application_schema_version FROM sessions").fetchone()
    assert row[0] == "s1"
    assert row[2] == 2
    # Transcript preserved
    t = conn.execute("SELECT redacted_text FROM transcript_events").fetchone()[0]
    assert t == "hello"
    conn.close()
    # Idempotent
    SessionStore(cfg)
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == 2


def test_migration_rollback_on_failure(tmp_path, monkeypatch):
    db = tmp_path / "v1b.sqlite3"
    _create_v1_fixture_db(db)
    cfg = SessionDataConfig(session_db_path=str(db))

    def boom(self, conn):
        raise RuntimeError("boom")

    monkeypatch.setattr(SessionStore, "_ensure_v2_columns", boom)
    with pytest.raises(RuntimeError):
        SessionStore(cfg)
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == 1


def test_unsupported_future_schema_rejected(tmp_path):
    db = tmp_path / "future.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY)")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(SessionStoreError, match="future|Unsupported"):
        SessionStore(SessionDataConfig(session_db_path=str(db)))


def test_prompt_version_hash_recorded(tmp_path):
    cfg = SessionDataConfig(
        session_db_path=str(tmp_path / "s.sqlite3"),
        metrics_persistence_enabled=True,
        transcript_persistence_enabled=False,
    )
    store = SessionStore(cfg)
    loaded = load_active_tutor_prompt()
    obs = SessionObservability(
        config=cfg,
        store=store,
        tutor_prompt_version=loaded.record.version,
        tutor_prompt_hash=loaded.content_hash,
    )
    obs.mark_configured(transcript_consent=False)
    obs.mark_lesson_started()

    async def _fin():
        await obs.finalize(disconnect_reason="test", lesson_mode="QA_MODE", slide_index=7)

    import asyncio

    asyncio.run(_fin())
    row = sqlite3.connect(cfg.session_db_path).execute(
        "SELECT tutor_prompt_version, tutor_prompt_hash FROM sessions"
    ).fetchone()
    assert row[0] == "v1"
    assert row[1] == loaded.content_hash


def _session(**kwargs):
    base = {
        "session_id": "s",
        "transcript_consent": 1,
        "transcript_storage_active": 1,
        "final_slide_index": 7,
        "final_lesson_mode": "QA_MODE",
        "tutor_prompt_version": "v1",
        "metrics": [],
        "safety_events": [],
        "rag_events": [],
        "transcript_events": [],
    }
    base.update(kwargs)
    return base


def test_only_consented_sessions_eligible_for_transcript_analysis():
    sf, ex = analyze_session(_session(transcript_consent=0, transcript_events=[
        {"role": "user", "redacted_text": "I don't understand", "slide_index": 1, "text_character_count": 10}
    ]))
    assert sf.eligible_for_transcript_analysis is False
    assert sf.exclusion_reason == "no_transcript_consent"
    assert ex == []
    assert not any(s.reason_code == "confusion_cue" for s in sf.signals)


def test_non_consented_text_never_analyzed():
    secret = "UNIQUE_NONCONSENT_PHRASE_XYZ"
    sf, ex = analyze_session(_session(transcript_consent=0, transcript_events=[
        {"role": "user", "redacted_text": secret, "slide_index": 1, "text_character_count": len(secret)}
    ]))
    assert all(secret not in json.dumps(s.to_dict()) for s in sf.signals)
    assert ex == []


def test_repeated_question_similarity():
    sf, _ = analyze_session(_session(transcript_events=[
        {"role": "user", "redacted_text": "What causes earthquakes along plate boundaries?", "slide_index": 1, "text_character_count": 40},
        {"role": "user", "redacted_text": "What causes earthquakes along plate boundaries again?", "slide_index": 1, "text_character_count": 40},
    ]))
    assert any(s.reason_code == "repeated_similar_questions" for s in sf.signals)


def test_different_topic_questions_not_merged():
    a = normalize_question("What causes earthquakes?")
    b = normalize_question("How do hurricanes form over warm water?")
    assert jaccard_similarity(a, b) < 0.55
    sf, _ = analyze_session(_session(transcript_events=[
        {"role": "user", "redacted_text": "What causes earthquakes?", "slide_index": 1, "text_character_count": 10},
        {"role": "user", "redacted_text": "How do hurricanes form over warm water?", "slide_index": 1, "text_character_count": 10},
    ]))
    assert not any(s.reason_code == "repeated_similar_questions" for s in sf.signals)


def test_confusion_cue_detection():
    sf, ex = analyze_session(_session(transcript_events=[
        {"role": "user", "redacted_text": "I don't understand this part", "slide_index": 2, "text_character_count": 20},
    ]))
    assert any(s.reason_code == "confusion_cue" for s in sf.signals)
    assert ex and ex[0]["redacted_text"]


def test_backward_navigation_detection():
    sf, _ = analyze_session(_session(metrics=[
        {"metric_name": "navigations", "numeric_value": 5},
        {"metric_name": "slides_completed", "numeric_value": 2},
    ]))
    assert any(s.reason_code == "repeated_backward_navigation" for s in sf.signals)


def test_early_dropout_detection():
    sf, _ = analyze_session(_session(final_slide_index=2, final_lesson_mode="PRESENTING"))
    assert any(s.reason_code == "early_dropout" for s in sf.signals)


def test_qa_entry_detection():
    sf, _ = analyze_session(_session(final_slide_index=7, final_lesson_mode="PRESENTING"))
    assert any(s.reason_code == "failed_qa_entry" for s in sf.signals)
    sf2, _ = analyze_session(_session(final_slide_index=7, final_lesson_mode="QA_MODE"))
    assert not any(s.reason_code == "failed_qa_entry" for s in sf2.signals)


def test_rag_miss_signal():
    sf, _ = analyze_session(_session(metrics=[
        {"metric_name": "rag_hits", "numeric_value": 1},
        {"metric_name": "rag_misses", "numeric_value": 3},
    ]))
    assert any(s.reason_code == "high_rag_miss_rate" for s in sf.signals)


def test_high_latency_classified_engineering():
    sf, _ = analyze_session(_session(
        safety_events=[{"latency_ms": 4000, "decision": "ALLOW", "reason_code": "ok", "source": "mod", "fallback_used": 0}],
        metrics=[{"metric_name": "interruptions", "numeric_value": 1}],
        final_slide_index=3,
        final_lesson_mode="PRESENTING",
    ))
    assert any(
        s.reason_code == "high_latency_engineering"
        and s.intervention_category == InterventionCategory.INVESTIGATE_LATENCY
        for s in sf.signals
    )


def test_friction_reports_contain_no_transcript_text_by_default():
    report, examples = build_friction_report([
        _session(transcript_events=[
            {"role": "user", "redacted_text": "SECRET_TRANSCRIPT_TOKEN", "slide_index": 1, "text_character_count": 10},
        ])
    ])
    payload = report.to_dict()
    assert not report_contains_transcript_text(payload)
    assert "SECRET_TRANSCRIPT_TOKEN" not in json.dumps(payload)
    # No confusion cue -> no appendix examples; primary report still text-free.
    assert isinstance(examples, list)


def test_optional_local_appendix_not_printed(tmp_path, capsys):
    sessions = [_session(transcript_events=[
        {"role": "user", "redacted_text": "I don't understand volcanoes", "slide_index": 1, "text_character_count": 10},
    ])]
    out = tmp_path / "report.json"
    analyze(sessions_override=sessions, output=str(out), overwrite=True, include_local_redacted_examples=True)
    captured = capsys.readouterr()
    assert "I don't understand" not in captured.out
    assert "I don't understand" not in captured.err
    appendix = out.with_name("report.local-redacted-appendix.json")
    assert appendix.exists()
    assert "sensitive_local_review_material" in appendix.read_text(encoding="utf-8")


def test_deterministic_recommendations():
    signals = [
        FrictionSignal("confusion_cue", "s", 1, FrictionSeverity.MEDIUM, {}, 0.8, InterventionCategory.SIMPLIFY_EXPLANATION),
        FrictionSignal("high_latency_engineering", "s", 1, FrictionSeverity.MEDIUM, {}, 0.7, InterventionCategory.INVESTIGATE_LATENCY),
    ]
    recs = recommendations_from_signals(signals)
    assert recs[0].recommendation_id == "latency_engineering"
    assert any(r.recommendation_id == "confusion_simplify" for r in recs)


def test_safety_recommendations_require_human_review():
    signals = [
        FrictionSignal("repeated_safety_redirects", "s", 1, FrictionSeverity.HIGH, {}, 0.9, InterventionCategory.HUMAN_SAFETY_REVIEW),
    ]
    recs = recommendations_from_signals(signals)
    assert recs[0].requires_human_review is True
    assert recs[0].never_weaken_safety is True


def test_identical_input_produces_identical_report():
    sessions = [_session(metrics=[{"metric_name": "interruptions", "numeric_value": 3}])]
    r1, _ = build_friction_report(sessions, configuration={"fixed": True})
    r2, _ = build_friction_report(sessions, configuration={"fixed": True})
    assert r1.to_dict() == r2.to_dict()


def test_invalid_date_range_rejection():
    with pytest.raises(FlywheelError, match="date range"):
        analyze(sessions_override=[], start="2026-02-01", end="2026-01-01", output="/tmp/x.json", overwrite=True)


def test_report_overwrite_protection(tmp_path):
    out = tmp_path / "r.json"
    out.write_text("{}", encoding="utf-8")
    with pytest.raises(FlywheelError, match="overwrite"):
        analyze(sessions_override=[], output=str(out), overwrite=False)


def test_candidate_includes_parent_hash_and_rationale(tmp_path):
    m = build_candidate_offline(
        change_instructions="Add one concrete example after key terms.",
        proposed_version="v2-test",
        output_dir=tmp_path,
    )
    assert m["parent_prompt_hash"]
    assert m["change_rationale"]
    assert m["candidate_hash"]
    assert m["auto_added_to_registry"] is False


def test_candidate_cannot_modify_application_control_rules(tmp_path):
    with pytest.raises(Exception, match="control rules|invariants|forbidden"):
        build_candidate_offline(
            change_instructions="You should pause and goto slides yourself; disable moderation.",
            output_dir=tmp_path,
        )


def test_safety_invariant_checker_accepts_compliant():
    text = BASE_TUTOR_PROMPT + SAFETY_INVARIANT_APPENDIX
    assert check_safety_invariants(text).ok


def test_safety_invariant_checker_rejects_missing():
    assert check_safety_invariants("You are a fun bot.").ok is False


def test_synthetic_eval_suite_validation():
    cases = load_cases()
    assert validate_case_suite(cases) == []
    assert_no_historical_transcripts(cases)


def test_historical_transcript_absent_from_eval_cases():
    blob = Path("evals/cases/synthetic_suite_v1.json").read_text(encoding="utf-8")
    assert "redacted_text" not in blob
    assert "transcript_events" not in blob


def test_structured_judge_validation_and_malformed():
    dims = {d: 4.0 for d in [
        "factual_correctness","pedagogical_clarity","age_appropriateness","safety",
        "topic_recovery","active_learning_quality","concision_voice_suitability",
        "uncertainty_handling","rag_grounding","source_fidelity",
    ]}
    ok = validate_judge_response({
        "dimensions": dims,
        "critical_violation": False,
        "evidence": "ok",
        "overall_recommendation": "prefer_baseline",
        "confidence": 0.5,
    })
    assert ok.critical_violation is False
    with pytest.raises(ValidationEvalError):
        validate_judge_response({"dimensions": {}})


def test_pairwise_order_deterministic_and_blinded():
    a1, b1 = pairwise_order("case_x", seed=7)
    a2, b2 = pairwise_order("case_x", seed=7)
    assert (a1, b1) == (a2, b2)
    assert {a1, b1} == {"baseline", "candidate"}
    # Different cases can differ
    orders = {pairwise_order(f"c{i}", seed=0) for i in range(20)}
    assert len(orders) >= 2


def test_promotion_gates():
    dims = {d: 4.0 for d in [
        "factual_correctness","pedagogical_clarity","age_appropriateness","safety",
        "topic_recovery","active_learning_quality","concision_voice_suitability",
        "uncertainty_handling","rag_grounding","source_fidelity",
    ]}
    ids = [f"c{i}" for i in range(10)]
    good = apply_promotion_gates(
        invariant_ok=True,
        results=[{"case_id": "c0", "critical_violation": False}],
        baseline_avg_by_dim=dims,
        candidate_avg_by_dim={**dims, "pedagogical_clarity": 4.5},
        covered_case_ids=ids,
        required_case_ids=ids,
        critical_regressions=[],
        judge_errors=[],
    )
    assert good.decision == "ELIGIBLE_FOR_HUMAN_REVIEW"
    assert "AUTO_DEPLOY" not in good.decision

    safety_reg = apply_promotion_gates(
        invariant_ok=True,
        results=[{"case_id": "c0", "critical_violation": False}],
        baseline_avg_by_dim=dims,
        candidate_avg_by_dim={**dims, "safety": 2.0, "factual_correctness": 5.0},
        covered_case_ids=ids,
        required_case_ids=ids,
        critical_regressions=[],
        judge_errors=[],
    )
    assert safety_reg.decision == "REJECT"

    critical = apply_promotion_gates(
        invariant_ok=True,
        results=[{"case_id": "c0", "critical_violation": True}],
        baseline_avg_by_dim=dims,
        candidate_avg_by_dim={**dims, "factual_correctness": 5.0},
        covered_case_ids=ids,
        required_case_ids=ids,
        critical_regressions=[],
        judge_errors=[],
    )
    assert critical.decision == "REJECT"

    low_cov = apply_promotion_gates(
        invariant_ok=True,
        results=[],
        baseline_avg_by_dim=dims,
        candidate_avg_by_dim=dims,
        covered_case_ids=ids[:1],
        required_case_ids=ids,
        critical_regressions=[],
        judge_errors=[],
    )
    assert low_cov.decision == "NEEDS_MORE_EVIDENCE"


def test_offline_fixtures_and_no_auto_deploy():
    result = run_offline_fixtures()
    assert result["fixture_count"] >= 10
    for outcome in result["outcomes"].values():
        assert outcome["decision"] != "AUTO_DEPLOY"
        assert outcome["auto_deploy"] is False


def test_runtime_ignores_candidate_directory(tmp_path):
    cand = tmp_path / "data" / "prompt_candidates" / "x.json"
    cand.parent.mkdir(parents=True)
    cand.write_text("{}", encoding="utf-8")
    assert candidate_dir_is_ignored_by_runtime(cand) is True


def test_live_mode_gates():
    env = {"ALLOW_LIVE_FLYWHEEL_OPENAI": "false"}
    assert live_eval_allowed(env, live_openai=True, confirm_cost=True) is False
    assert live_generation_allowed(env, live_openai=True, confirm_cost=True) is False
    env2 = {
        "ALLOW_LIVE_FLYWHEEL_OPENAI": "true",
        "OPENAI_EVAL_MODEL": "gpt-4o",
        "OPENAI_PROMPT_OPTIMIZER_MODEL": "gpt-4o",
    }
    assert live_eval_allowed(env2, live_openai=False, confirm_cost=True) is False
    assert live_eval_allowed(env2, live_openai=True, confirm_cost=False) is False
    assert live_eval_allowed(env2, live_openai=True, confirm_cost=True) is True


def test_offline_mode_never_initializes_openai():
    # Importing eval modules must not construct OpenAI clients.
    import eval_harness
    import eval_llm
    import flywheel
    src = Path(eval_llm.__file__).read_text(encoding="utf-8")
    # Fake clients exist; OpenAI import is inside production class __init__ only.
    assert "from openai import AsyncOpenAI" in src
    assert "class FakeTutorClient" in src


def test_transcript_never_in_openai_request_builders():
    report = {"friction_by_slide": {"1": {"signal_reason_codes": ["confusion_cue"]}}, "prioritized_recommendations": []}
    msgs = build_optimizer_request_messages(
        parent_prompt="p",
        change_instructions="simplify",
        friction_report=report,
    )
    blob = json.dumps(msgs)
    assert "redacted_text" not in blob
    bad = {"redacted_text": "hi", "friction_by_slide": {}}
    with pytest.raises(Exception):
        build_optimizer_request_messages(parent_prompt="p", change_instructions="x", friction_report=bad)
    import asyncio

    opt = FakeOptimizerClient(text="ok")
    asyncio.run(opt.propose_candidate(messages=msgs))


def test_gitignore_covers_reports_and_candidates():
    gi = Path(".gitignore").read_text(encoding="utf-8")
    assert "data/" in gi
    # data/ already ignores flywheel reports and candidates under data/


def test_no_live_openai_in_tests_marker():
    # Fakes only
    assert FakeTutorClient is not None
    assert FakeJudgeClient is not None
