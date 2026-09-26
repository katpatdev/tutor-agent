# Architecture — Lesson control plane

## Ownership boundaries

| Layer | Module | Owns |
|-------|--------|------|
| Curriculum metadata | `tutor_agent.lesson.curriculum` | Slide index ↔ title ↔ prompt (8 slides) |
| Deterministic control | `tutor_agent.lesson.lesson_controller` | Mode, cursor, transitions, duplicate event IDs |
| Narration plans | `tutor_agent.narration.narration_plan` | Deterministic segmentation, generation IDs, segment progress, resume-accuracy contract |
| Runtime adapter | `tutor_agent.audio.presentation_runtime` | Per-session controller, narration plan lifecycle, output purpose, safety status gate, Pipecat effects, **per-session asyncio.Lock** |
| Control protocol | `tutor_agent.lesson.lesson_protocol` | Validate commands, dedupe `request_id`, state snapshots, WS URL helper |
| Moderation abstraction | `tutor_agent.safety.moderation_service` | OpenAI Moderations client + normalized `ModerationResult` (no raw text stored) |
| Safety policy | `tutor_agent.safety.safety_policy` | ALLOW / REDIRECT / SAFETY_HOLD, templates, in-memory safety events |
| Safety processors | `tutor_agent.safety.safety_processors` | Input (post-STT) and output (pre-TTS) Pipecat processors |
| Embeddings | `tutor_agent.knowledge.embedding_service` | OpenAI Embeddings client + RAG config |
| Knowledge ingestion | `tutor_agent.knowledge.knowledge_ingestion` | Parse/chunk/moderate/embed (atomic) |
| Knowledge store | `tutor_agent.knowledge.knowledge_store` | Process-local in-memory vectors |
| Retrieval | `tutor_agent.knowledge.retrieval_processor` | Post-safety temporary RAG context |
| Knowledge HTTP | `tutor_agent.knowledge.knowledge_api` | `/knowledge/documents`, `/knowledge/status` |
| Session metrics | `tutor_agent.observability.session_metrics` | Per-session collectors + disconnect report |
| Session store | `tutor_agent.observability.session_store` | Local SQLite persistence |
| Transcript redaction | `tutor_agent.safety.transcript_redaction` | Deterministic PII-ish placeholders |
| Session observability | `tutor_agent.observability.session_observability` | Narrow facade for metrics + optional persistence |
| Session export CLI | `tutor_agent.observability.session_export` | Local sanitized JSONL export |
| Prompt registry | `tutor_agent.evaluation.prompt_registry` + `prompts/` | Approved versioned tutor/judge prompts |
| Friction / flywheel | `tutor_agent.evaluation.friction_analyzer`, `curriculum_recommendations`, `flywheel` | Local consented analysis (no OpenAI on transcripts) |
| Prompt candidates | `tutor_agent.evaluation.prompt_workflow` | Reviewable candidates under `data/` (never auto-activate) |
| Eval harness | `tutor_agent.evaluation.eval_llm`, `eval_harness`, `evals/` | Synthetic suite + offline fixtures + gated live |
| Pipeline wiring | `tutor_agent.agent` | Transport/STT/LLM/TTS, observer, RTVI message bridge, safety + retrieval + session lifecycle |
| HTTP entry | `tutor_agent.main` (+ root `main.py` launcher) | FastAPI app, health/connect/knowledge routes, lifespan |
| Frontend protocol | `frontend/lessonProtocol.ts` | Parse/create envelopes, sequence tracking (no DOM) |
| Frontend knowledge | `frontend/knowledgeProtocol.ts` | Upload/status/retrieval parsing (no secrets) |
| Frontend UI | `frontend/app.ts` | Controls + safety notice + knowledge upload; **server state is authoritative** |
| Conversation content | OpenAI via Pipecat | Spoken wording only (still gated by moderation) |

## Control-message flow

1. Browser calls `PipecatClient.sendClientMessage('lesson.command', envelope)`.
2. Protobuf WebSocket delivers `InputTransportMessageFrame`.
3. Observer → `LessonProtocolSession` validates → `PresentationRuntime` method under session lock.
4. Runtime may queue `InterruptionFrame` / slide prompts.
5. Server sends `OutputTransportMessageUrgentFrame` with RTVI `server-message` containing `lesson.command_result` and/or `lesson.state`.
6. Browser `onServerMessage` / `RTVIEvent.ServerMessage` updates UI only from newer `sequence` values.

Commands are **never** inferred from student transcript text.

## Safety flow (Iteration 5)

See `docs/SAFETY.md` for the full threat model.

- **Input:** final `TranscriptionFrame` → OpenAI Moderation → policy → allow / redirect / hold.
- **Output:** buffer one LLM response → moderate → TTS only if allowed; else trusted template.
- **`lesson.state`** includes `safety_status` and `safety_notice` only (no scores, categories, or raw text).
- On `hold`, Resume and navigation are rejected server-side; Disconnect stays available.

## Knowledge RAG flow (Iteration 6)

See `docs/KNOWLEDGE_RAG.md`.

- Upload via HTTP; moderate chunks; embed with OpenAI; store in process memory.
- Voice path: after input safety ALLOW → retrieve → temporary system reference message → LLM.
- `knowledge.retrieval` server messages carry source metadata only.

## Session metrics & consent (Iteration 7)

See `docs/SESSION_DATA.md`.

- `session.ready` / `session.configure` handshake before lesson start.
- Content-free metrics always collected in memory; optional SQLite persistence.
- Redacted transcripts only with server enable **and** student consent.

## Learning flywheel & evaluations (Iteration 8)

See `docs/LEARNING_FLYWHEEL.md` and `docs/EVALUATIONS.md`.

- Active tutor prompt loaded from `prompts/` registry (`TUTOR_PROMPT_VERSION`).
- Session DB schema version **2** adds prompt/curriculum metadata (migrates from v1).
- Friction analysis and recommendations are local/CLI-only (not a public HTTP API).
- Prompt candidates and live LLM-as-judge runs never auto-deploy; human approval required.

## Segment-level narration (Iteration 9)

See `docs/RESUME_ACCURACY.md` and `docs/LIVE_TEST_PLAN.md`.

1. Output safety approves one complete slide-narration response.
2. `accept_approved_narration` creates a deterministic, generation-scoped plan.
3. The runtime queues one `TTSSpeakFrame` segment at a time.
4. An audible bot-stop completes only the active segment. Only the final
   segment dispatches `SLIDE_COMPLETED`.
5. Pause/barge-in replays an interrupted segment from its beginning. Navigation
   invalidates the old generation.

Exact browser playback offsets are unavailable. The supported and reported
resume accuracy is **segment-level**, never exact word/audio-byte resume.

Slide system instructions are temporary and marker-scoped: prior slide
instructions are stripped before each new slide turn so only the current
curriculum instruction controls narration (Iteration 10.3). The base tutor
prompt’s brief Q&A guidance (tutor v3: normally two to four sentences on the
exact question) applies to interruption and open Q&A answers; slide turns use
connected curriculum coverage.

TTS units are application-owned (Iteration 10.4). Silent OpenAI TTS contexts
are detected via Pipecat `ErrorFrame` from the TTS processor, retried once,
then skipped with a content-free `audio_warning`. Late ErrorFrame/watchdog races
are idempotent (Iteration 10.5).

Deterministic classroom controls (Iteration 10.5) run after input safety and
before RAG/LLM: navigation, acknowledgement (“I got it”), continuation,
segment repeat, and Q&A completion. Natural navigation phrases tolerate
politeness and trailing fillers; informational questions about slides do not
navigate. After slide 8, Q&A uses a two-stage silence wind-down (reminder, then
closing message) before `FINISHED`, or an explicit completion phrase skips to
closing. Closing audio must finish (or exhaust TTS recovery) before the session ends.

## Protocol envelopes (version 1)

- `lesson.command` — client→server (`pause` | `resume` | `goto_slide` | `get_state`)
- `lesson.state` — server→client snapshot (mode, slide, flags, safety metadata, and content-free narration progress)
- `lesson.command_result` — ack / structured error tied to `request_id`

Slide indexes in the protocol are **zero-based**. UI displays one-based numbers.

## Validation & deduplication

Reject malformed objects, bad versions, missing `request_id`, unknown commands, non-integer/boolean slide indexes, out-of-range indexes. Invalid commands do not mutate lesson state.

Duplicate `request_id` values return the cached result (bounded history, default 128).

## Per-session serialization

Each `PresentationRuntime` has its own `asyncio.Lock` covering lifecycle handlers and pause/resume/goto/end. No global cross-session lock. Safety status is per-session.

## State sequence handling

Monotonic `sequence` per WebSocket session. Frontend `LessonStateTracker` ignores `sequence <= latest`.

## Pause / resume / navigation

- **Pause:** runtime pause → `InterruptionFrame` + suppress cancelled bot-stop → publish state. Logical cursor preserved.
- **Resume:** restore prior mode; replay an interrupted narration segment or
  continue the next pending segment. This is deterministic segment-level
  resume, not exact playback-offset resume. Rejected during safety hold.
- **Goto:** validate transition first; stop narration with suppression; present requested slide. Rejected during safety hold.
- **get_state:** ack + snapshot; no mutation.

## Frontend controls

Connect/Disconnect, Pause/Resume, slide selector (1–8) + Go to Slide, `aria-live` safety notice. Buttons follow server `can_pause` / `can_resume` / `can_navigate`. No optimistic lesson-state updates.

## `/connect` URL

`build_ws_url` maps HTTP→`ws://` and HTTPS→`wss://` using request host (and `X-Forwarded-Proto` when present). Frontend uses public `VITE_BOT_API_URL` only (never OpenAI secrets).

## Remaining limitations

- Segment-level resume only (not exact playback-offset/word/audio-byte resume)
- In-memory RAG only (lost on restart; not multi-worker)
- Local SQLite session store is demo-grade (not multi-tenant auth)
- Output TTS waits for full LLM response + moderation (latency tradeoff)
- Manual live confirmation still required for narration naturalness, interruption races, RAG, consent, and safety scenarios without their own PASS results
