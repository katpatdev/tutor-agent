# Architecture — Lesson control plane

## Ownership boundaries

| Layer | Module | Owns |
|-------|--------|------|
| Curriculum metadata | `curriculum.py` | Slide index ↔ title ↔ prompt (8 slides) |
| Deterministic control | `lesson_controller.py` | Mode, cursor, transitions, duplicate event IDs |
| Runtime adapter | `presentation_runtime.py` | Per-session controller, output purpose, Pipecat effects, **per-session asyncio.Lock** |
| Control protocol | `lesson_protocol.py` | Validate commands, dedupe `request_id`, state snapshots, WS URL helper |
| Pipeline wiring | `agent.py` | Transport/STT/LLM/TTS, observer, RTVI message bridge |
| Frontend protocol | `frontend/lessonProtocol.ts` | Parse/create envelopes, sequence tracking (no DOM) |
| Frontend UI | `frontend/app.ts` | Controls + display; **server state is authoritative** |
| Conversation content | OpenAI via Pipecat | Spoken wording only |

## Control-message flow

1. Browser calls `PipecatClient.sendClientMessage('lesson.command', envelope)`.
2. Protobuf WebSocket delivers `InputTransportMessageFrame`.
3. Observer → `LessonProtocolSession` validates → `PresentationRuntime` method under session lock.
4. Runtime may queue `InterruptionFrame` / slide prompts.
5. Server sends `OutputTransportMessageUrgentFrame` with RTVI `server-message` containing `lesson.command_result` and/or `lesson.state`.
6. Browser `onServerMessage` / `RTVIEvent.ServerMessage` updates UI only from newer `sequence` values.

Commands are **never** inferred from student transcript text.

## Protocol envelopes (version 1)

- `lesson.command` — client→server (`pause` | `resume` | `goto_slide` | `get_state`)
- `lesson.state` — server→client snapshot (mode, slide index/number/total/title, flags)
- `lesson.command_result` — ack / structured error tied to `request_id`

Slide indexes in the protocol are **zero-based**. UI displays one-based numbers.

## Validation & deduplication

Reject malformed objects, bad versions, missing `request_id`, unknown commands, non-integer/boolean slide indexes, out-of-range indexes. Invalid commands do not mutate lesson state.

Duplicate `request_id` values return the cached result (bounded history, default 128).

## Per-session serialization

Each `PresentationRuntime` has its own `asyncio.Lock` covering lifecycle handlers and pause/resume/goto/end. No global cross-session lock.

## State sequence handling

Monotonic `sequence` per WebSocket session. Frontend `LessonStateTracker` ignores `sequence <= latest`.

## Pause / resume / navigation

- **Pause:** runtime pause → `InterruptionFrame` + suppress cancelled bot-stop → publish state. Logical cursor preserved.
- **Resume:** restore prior mode; continue from **logical** cursor (not audio-byte resume).
- **Goto:** validate transition first; stop narration with suppression; present requested slide.
- **get_state:** ack + snapshot; no mutation.

## Frontend controls

Connect/Disconnect, Pause/Resume, slide selector (1–8) + Go to Slide. Buttons follow server `can_pause` / `can_resume` / `can_navigate`. No optimistic lesson-state updates.

## `/connect` URL

`build_ws_url` maps HTTP→`ws://` and HTTPS→`wss://` using request host (and `X-Forwarded-Proto` when present). Frontend uses public `VITE_BOT_API_URL` only (never OpenAI secrets).

## Remaining limitations

- Logical resume only (not audio-byte)
- No RAG / flywheel / full moderation / production metrics
- Live OpenAI e2e control timing not validated in offline CI
