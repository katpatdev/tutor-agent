# Requirements traceability

Status vocabulary:

| Status | Meaning |
|--------|---------|
| complete | Implemented and covered by offline tests/docs |
| partial | Implemented with documented gaps |
| unverified | Depends on human-authorized live OpenAI validation |
| known limitation | Intentionally incomplete or constrained |

Exact playback-offset resume is **not** marked complete.

## Resume accuracy (explicit)

| Item | Status |
|------|--------|
| Deterministic segment-level resume | complete |
| Interrupted segment restarts from its beginning | complete (offline) / unverified (live audio) |
| Short phrase may repeat on resume | complete (documented) |
| Browser playback-offset acknowledgement | known limitation — unavailable in installed Pipecat 1.11.0 + client-js 1.6.0 FastAPI WebSocket/Protobuf path |

## Assignment / README requirements

| Requirement | Implementation | Tests | Docs | Manual demo | Status |
|-------------|----------------|-------|------|-------------|--------|
| Pipecat-only orchestration | `agent.py`, `main.py`, `presentation_runtime.py` | `tests/test_presentation_runtime.py` | `docs/ARCHITECTURE.md`, `docs/PIPECAT_COMPATIBILITY.md` | Architecture overview | complete |
| OpenAI-only external AI | STT/LLM/TTS/Moderation/Embeddings clients | fake clients in safety/RAG/eval tests | README, AGENTS.md | Live STT/LLM/TTS | complete offline; live unverified |
| Eight-slide natural-disaster curriculum | `curriculum.py` | controller/runtime reachability tests | README | Slides 1–8 | complete |
| Student interruption | `lesson_controller.py`, `presentation_runtime.py` | runtime interruption tests, `tests/test_narration.py` | ARCHITECTURE, LIVE_TEST_PLAN | Scenario 4 | complete offline; live unverified |
| Answer then topic recovery | ANSWER_COMPLETED → RESUME_NARRATION + segment replay | narration/runtime tests | ARCHITECTURE, RESUME_ACCURACY | Scenario 4 | complete offline; live unverified |
| Final slide then Q&A | SLIDE_COMPLETED on index 7 → QA_MODE | runtime Q&A tests | LIVE_TEST_PLAN | Scenario 7 | complete offline; live unverified |
| Return to any slide | `goto_slide` protocol + controller | protocol/runtime nav tests | LIVE_TEST_PLAN | Scenario 6 | complete offline; live unverified |
| Frontend Pause / Resume | `frontend/app.ts`, lesson protocol | frontend protocol tests | README, LIVE_TEST_PLAN | Scenarios 2–3 | complete offline; live unverified |
| Resume accuracy (segment-level) | `narration_plan.py`, `presentation_runtime.py` | `tests/test_narration.py` | `docs/RESUME_ACCURACY.md` | Scenario 3 | complete (segment); exact resume known limitation |
| Student safety | `safety_processors.py`, `safety_policy.py`, `moderation_service.py` | `tests/test_safety.py` | `docs/SAFETY.md` | Scenario 8 | complete offline; live unverified |
| Deterministic tests | `tests/*.py` | pytest suite (171) | IMPLEMENTATION_LOG | Show pytest | complete |
| LLM-as-a-judge tests | `eval_harness.py`, `evals/` | offline fixtures | `docs/EVALUATIONS.md` | Show offline fixtures | complete (offline); live judge not required |
| Knowledge ingestion | `knowledge_ingestion.py`, `knowledge_api.py` | `tests/test_knowledge.py` | `docs/KNOWLEDGE_RAG.md` | Scenario 9 | complete offline; live unverified |
| RAG source attribution | `retrieval_processor.py`, frontend knowledge UI | knowledge tests | KNOWLEDGE_RAG | Scenario 9 | complete offline; live unverified |
| Disconnect metrics | `session_metrics.py`, observability | session tests | SESSION_DATA | Scenario 10 | complete offline; live unverified |
| Transcript persistence | consent + redaction + SQLite | `tests/test_session_data.py` | SESSION_DATA | Scenario 11 | complete offline; live unverified |
| Transcript learning flywheel | `friction_analyzer.py`, `flywheel.py`, prompt workflow | `tests/test_flywheel.py` | LEARNING_FLYWHEEL | Show analyze CLI | complete (local; no auto-deploy) |
| Frontend/backend synchronization | protocol sequence + server-authoritative state | protocol tests | ARCHITECTURE | Connect UI | complete |
| Privacy and consent | session configure, redaction, defaults | session tests | SESSION_DATA, SAFETY | Scenario 11 | complete |

## Architecture ownership (verified)

| Owner | Responsibility |
|-------|----------------|
| `LessonController` | Lesson mode, slide index, logical cursor, legal transitions |
| `PresentationRuntime` | Per-session orchestration, output purpose, generation ownership |
| `NarrationPlan` | Segment progression and invalidation |
| Safety processors | Gate student/assistant content before LLM/TTS |
| RAG | Temporary per-turn reference context after ALLOW |
| Protocol modules | Validate envelopes; frontend displays server state |
| Session observability | Metrics + consented redacted storage |
| Prompt registry | Approved prompt loading only |
| Flywheel | Local analysis; never auto-deploys prompts |

## Configuration defaults (non-secret)

See `.env.example`. Notable safe defaults: `TRANSCRIPT_PERSISTENCE_ENABLED=false`, `ALLOW_LIVE_FLYWHEEL_OPENAI=false`, narration segment max 320. No `VITE_` secrets.

## Offline audit snapshot (Iteration 10)

Recorded before any live OpenAI authorization:

- `uv sync --locked` — ok
- `uv run pytest -q` — **171 passed**
- `compileall` — ok
- `eval_harness validate` — 21 cases OK
- `eval_harness run-offline-fixtures` — 13 fixtures OK
- frontend: **29** tests; tsc; vite build — ok
- health live/ready, connect, knowledge status — 200; `/ws` not opened
- preflight — pass; **No OpenAI request was made**
