# Architecture — Lesson control plane

## Ownership boundaries

| Layer | Module | Owns |
|-------|--------|------|
| Deterministic control | `lesson_controller.py` | Mode, slide index, logical cursor, pause/Q&A/finished transitions, duplicate event IDs |
| Runtime adapter | `presentation_runtime.py` | Per-session controller instance, output purpose, event-ID minting, effect → frame actions, pause/resume/goto entry points |
| Pipeline wiring | `agent.py` | Pipecat transport/STT/LLM/TTS, `LessonLifecycleObserver`, connect/disconnect hooks |
| Conversation content | OpenAI LLM via Pipecat | Wording of narration and answers only |

The LLM must not invent slide index, pause, Q&A mode, or narration position.

## Indexing convention

**Slide indexes are zero-based** (`0 … slide_count-1`). Human “Slide 1…8” labels map to `0…7`.

## Runtime event → controller event

| Runtime signal / method | Controller event |
|-------------------------|------------------|
| `start_session()` (once) | `START_LESSON` |
| `BotStoppedSpeaking` after audible slide/resume narration | `SLIDE_COMPLETED` |
| `UserStartedSpeaking` while presenting | `USER_INTERRUPTED` then `ANSWER_STARTED` |
| Answer `BotStoppedSpeaking` with purpose `INTERRUPTION_ANSWER` | `ANSWER_COMPLETED` |
| `pause()` | `PAUSE_REQUESTED` |
| `resume()` | `RESUME_REQUESTED` |
| `go_to_slide(i)` | `GOTO_SLIDE_REQUESTED` |
| `end_session()` / disconnect | `END_SESSION` (if not already finished) |

Silence timers are **not** events.

## Effect → Pipecat / application action

| Effect | Action |
|--------|--------|
| `PRESENT_SLIDE` | Queue slide system prompt via `LLMMessagesAppendFrame(run_llm=True)`; set purpose `SLIDE_NARRATION` |
| `RESUME_NARRATION` | Queue resume instruction with logical cursor; set purpose `RESUMED_NARRATION` |
| `STOP_NARRATION` | Queue `InterruptionFrame`; suppress next bot-stopped completion; clear purpose |
| `BEGIN_ANSWER` | Set purpose `INTERRUPTION_ANSWER` (pipeline answers student turn) |
| `ENTER_QA` | Queue Q&A transition prompt once; set purpose `QA_TRANSITION` |
| `SESSION_FINISHED` | Clear purpose; mark session ended |
| `NO_ACTION` | No frame side effects |

## Output-purpose tracking

`OutputPurpose` is set by the runtime, never inferred from LLM text:

- `SLIDE_NARRATION` / `RESUMED_NARRATION`
- `INTERRUPTION_ANSWER`
- `QA_TRANSITION` / `QA_RESPONSE`
- `NONE`

## Interruption and logical resume

1. User starts speaking during presentation → save logical cursor → `USER_INTERRUPTED` → `STOP_NARRATION` (`InterruptionFrame`) → `ANSWER_STARTED`.
2. Cancelled bot-stop is suppressed (not slide completion).
3. Answer audio completes → `ANSWER_COMPLETED` → `RESUME_NARRATION` from saved cursor.

**Exact audible / byte-offset resume is not implemented.** Cursor fields are logical checkpoints only.

## Final slide → Q&A

Completing slide index `7` → `QA_MODE` + `ENTER_QA` (invite questions). Does **not** dispatch `END_SESSION`, say goodbye as a terminal action, or close the WebSocket.

## Controller modes / events / effects

See Iteration 2 definitions in this document’s historical sections and `lesson_controller.py`. Invalid transitions raise `InvalidLessonTransition` and leave prior immutable state unchanged. Invalid goto indexes are rejected, never clamped.

## Implemented through Iteration 3

- Pure `LessonController` + tests
- `PresentationRuntime` + offline runtime tests
- `agent.py` uses runtime; `PresentationObserver0` / silence timer removed
- Tutor persona + student TTS instructions
- Pipecat pinned to `==1.11.0`

## Intentionally unimplemented / remaining limitations

- Frontend pause/goto/resume controls and WS command protocol
- Exact audio-byte resume
- Underage moderation beyond baseline persona text
- RAG / knowledge ingest, transcript flywheel, disconnect metrics report
- Live OpenAI e2e verification (billable)
