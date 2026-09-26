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

Original `README.md` **Additional Goals** (verbatim paraphrase with source):

> “Should have a reliable way to ingest additional knowledge so that agent can answer questions better.”  
> — `README.md` § Additional Goals

| Requirement | Original assignment | Current implementation | Classification |
|-------------|---------------------|------------------------|----------------|
| Knowledge ingestion | Explicit (“ingest additional knowledge”) | Upload + chunk + embed + in-memory store | **explicitly required** |
| RAG / retrieval-augmented generation | Not named; implied by “ingest…answer better” | `RetrievalProcessor` after ALLOW | **implied** by original; architecture chosen in Iteration 6 |
| Document upload UI/API | Not specified in original README | `POST /knowledge/documents`, frontend upload | **architectural enhancement** implementing the ingest goal |
| Embeddings | Not named | `text-embedding-3-small` | **architectural enhancement** |
| Source attribution | Not named in original README | `knowledge.retrieval` metadata (label, document_name, page) | **architectural enhancement** (supports grounded answers) |
| Live RAG demo | Not in original README | Live test plan Scenario 9 | **current project / demo requirement** |

Do not equate Iteration 6 design docs with the original assignment text. RAG is the chosen mechanism to satisfy the original knowledge-ingest goal; embeddings and source chips are implementation choices.

## Core goal implementation status

| Requirement | Implementation | Tests | Status |
|-------------|----------------|-------|--------|
| Pipecat-only / OpenAI-only | `tutor_agent.agent`, clients | suite | complete offline |
| Interruption → answer → topic recovery | controller + runtime | narration / 10.2 tests | complete offline; live unverified |
| Final slide → Q&A | runtime | runtime tests | complete offline; live unverified |
| Pause / Resume (segment) | protocol + runtime | frontend + backend | complete (segment); exact resume known limitation |
| Student safety | safety processors | `test_safety.py` | complete offline |
| Knowledge ingest + RAG | knowledge_* + retrieval | `test_knowledge.py` | complete offline; live unverified |
| Disconnect metrics | session_metrics | session tests | complete |
| Transcript flywheel (local) | flywheel / eval | flywheel + eval fixtures | complete (no auto-deploy) |

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
