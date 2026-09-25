# Architecture — Lesson control plane

## Ownership boundaries

| Layer | Module | Owns |
|-------|--------|------|
| Curriculum metadata | `curriculum.py` | Slide index ↔ title ↔ prompt (8 slides) |
| Deterministic control | `lesson_controller.py` | Mode, cursor, transitions, duplicate event IDs |
| Runtime adapter | `presentation_runtime.py` | Per-session controller, output purpose, safety status gate, Pipecat effects, **per-session asyncio.Lock** |
| Control protocol | `lesson_protocol.py` | Validate commands, dedupe `request_id`, state snapshots, WS URL helper |
| Moderation abstraction | `moderation_service.py` | OpenAI Moderations client + normalized `ModerationResult` (no raw text stored) |
| Safety policy | `safety_policy.py` | ALLOW / REDIRECT / SAFETY_HOLD, templates, in-memory safety events |
| Safety processors | `safety_processors.py` | Input (post-STT) and output (pre-TTS) Pipecat processors |
| Embeddings | `embedding_service.py` | OpenAI Embeddings client + RAG config |
| Knowledge ingestion | `knowledge_ingestion.py` | Parse/chunk/moderate/embed (atomic) |
| Knowledge store | `knowledge_store.py` | Process-local in-memory vectors |
| Retrieval | `retrieval_processor.py` | Post-safety temporary RAG context |
| Knowledge HTTP | `knowledge_api.py` | `/knowledge/documents`, `/knowledge/status` |
| Session metrics | `session_metrics.py` | Per-session collectors + disconnect report |
| Session store | `session_store.py` | Local SQLite persistence |
| Transcript redaction | `transcript_redaction.py` | Deterministic PII-ish placeholders |
| Session observability | `session_observability.py` | Narrow facade for metrics + optional persistence |
| Session export CLI | `session_export.py` | Local sanitized JSONL export |
| Pipeline wiring | `agent.py` | Transport/STT/LLM/TTS, observer, RTVI message bridge, safety + retrieval + session lifecycle |
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

## Protocol envelopes (version 1)

- `lesson.command` — client→server (`pause` | `resume` | `goto_slide` | `get_state`)
- `lesson.state` — server→client snapshot (mode, slide, flags, `safety_status`, `safety_notice`)
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
- **Resume:** restore prior mode; continue from **logical** cursor (not audio-byte resume). Rejected during safety hold.
- **Goto:** validate transition first; stop narration with suppression; present requested slide. Rejected during safety hold.
- **get_state:** ack + snapshot; no mutation.

## Frontend controls

Connect/Disconnect, Pause/Resume, slide selector (1–8) + Go to Slide, `aria-live` safety notice. Buttons follow server `can_pause` / `can_resume` / `can_navigate`. No optimistic lesson-state updates.

## `/connect` URL

`build_ws_url` maps HTTP→`ws://` and HTTPS→`wss://` using request host (and `X-Forwarded-Proto` when present). Frontend uses public `VITE_BOT_API_URL` only (never OpenAI secrets).

## Remaining limitations

- Logical resume only (not audio-byte)
- In-memory RAG only (lost on restart; not multi-worker)
- Local SQLite session store is demo-grade (not multi-tenant auth)
- Learning flywheel / LLM-as-judge not implemented yet
- Output TTS waits for full LLM response + moderation (latency tradeoff)
- Live OpenAI e2e control timing not validated in offline CI
