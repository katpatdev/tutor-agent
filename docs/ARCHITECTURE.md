# Architecture — Lesson control plane

## Deterministic control vs LLM conversation

| Concern | Owner | Notes |
|---------|--------|------|
| Lesson mode, slide index, pause, Q&A, logical narration cursor | **Deterministic `LessonController`** | Source of truth; never inferred by the LLM |
| Spoken wording, answers to student questions, tone | **LLM (OpenAI via Pipecat)** | Generates conversation only; must follow controller effects |
| Audio transport, STT/TTS | **Pipecat + OpenAI** | Integration intentionally **not** done in Iteration 2 |

The LLM must not invent or silently change slide index, pause state, Q&A mode, or narration position.

## Indexing convention

**Slide indexes are zero-based** internally (`0 … slide_count-1`).

For the default eight-slide natural-disaster lesson:

| Index | Role |
|------:|------|
| 0–6 | Intermediate slides; completion advances to the next slide |
| 7 | Final slide; completion enters `QA_MODE` (not `FINISHED`) |

Human-facing “Slide 1…8” labels map to indexes `0…7`.

## State definitions (`LessonMode`)

| Mode | Meaning |
|------|---------|
| `IDLE` | Session not started |
| `PRESENTING` | Narrating the current slide |
| `INTERRUPTED` | User interrupted; cursor saved; awaiting answer start |
| `ANSWERING` | Handling the student’s question |
| `PAUSED` | Explicit pause; prior mode stored in `mode_before_pause` |
| `QA_MODE` | Post-presentation open Q&A |
| `FINISHED` | Session closed by explicit `END_SESSION` only |

`LessonState` (immutable) also holds:

- `cursor: NarrationCursor` — `slide_index`, `segment_index`, `word_index` (logical only)
- `slide_count`
- `mode_before_pause`
- `interruption_cursor` — checkpoint for answer→resume
- `processed_event_ids` / `last_event_id` — duplicate protection

## Event definitions (`LessonEventType`)

| Event | Purpose |
|-------|---------|
| `START_LESSON` | `IDLE` → `PRESENTING` at slide 0 |
| `SLIDE_COMPLETED` | Advance slide, or final → `QA_MODE` |
| `USER_INTERRUPTED` | Save cursor → `INTERRUPTED` |
| `ANSWER_STARTED` | `INTERRUPTED` → `ANSWERING` |
| `ANSWER_COMPLETED` | Restore saved cursor → `PRESENTING` |
| `PAUSE_REQUESTED` | Enter `PAUSED` (idempotent if already paused) |
| `RESUME_REQUESTED` | Restore prior mode (idempotent if not paused) |
| `GOTO_SLIDE_REQUESTED` | Jump to validated index from `PRESENTING` or `QA_MODE` |
| `END_SESSION` | Enter `FINISHED` |

Every event carries a unique `event_id`. Replaying the same id yields `NO_ACTION` and does not advance state.

Silence timers are **not** state-machine events.

## Effects (`LessonEffect`)

Returned to the application layer; the controller never performs I/O.

| Effect | Intended application action |
|--------|------------------------------|
| `PRESENT_SLIDE` | Begin/continue narration for `state.cursor.slide_index` |
| `STOP_NARRATION` | Stop speaking (interrupt or pause) |
| `BEGIN_ANSWER` | Start answering the student |
| `RESUME_NARRATION` | Resume lesson speech from logical cursor |
| `ENTER_QA` | Switch prompts/behavior to Q&A |
| `SESSION_FINISHED` | Tear down session |
| `NO_ACTION` | Duplicate or idempotent no-op |

## Error behavior

Invalid transitions raise `InvalidLessonTransition`. The controller’s previous immutable `LessonState` snapshot is left unchanged. Invalid slide indexes are **rejected**, never clamped.

## State-transition table (summary)

| From | Event | To | Effect |
|------|-------|----|--------|
| IDLE | START_LESSON | PRESENTING (slide 0) | PRESENT_SLIDE |
| PRESENTING | SLIDE_COMPLETED (not last) | PRESENTING (next) | PRESENT_SLIDE |
| PRESENTING | SLIDE_COMPLETED (last) | QA_MODE | ENTER_QA |
| PRESENTING | USER_INTERRUPTED | INTERRUPTED | STOP_NARRATION |
| INTERRUPTED | ANSWER_STARTED | ANSWERING | BEGIN_ANSWER |
| ANSWERING | ANSWER_COMPLETED | PRESENTING (saved cursor) | RESUME_NARRATION |
| PRESENTING / INTERRUPTED / ANSWERING / QA_MODE | PAUSE_REQUESTED | PAUSED | STOP_NARRATION |
| PAUSED | PAUSE_REQUESTED | PAUSED | NO_ACTION |
| PAUSED | RESUME_REQUESTED | prior mode | RESUME_NARRATION / BEGIN_ANSWER / ENTER_QA / … |
| not PAUSED | RESUME_REQUESTED | unchanged mode | NO_ACTION |
| PRESENTING / QA_MODE | GOTO_SLIDE_REQUESTED (valid) | PRESENTING (target, seg/word 0) | PRESENT_SLIDE |
| active modes | END_SESSION | FINISHED | SESSION_FINISHED |
| FINISHED | any new event | *(error)* | — |
| any | duplicate `event_id` | unchanged | NO_ACTION |

## Final-slide → Q&A

Completing slide index `slide_count - 1` enters `QA_MODE` with effect `ENTER_QA`. It must **not** skip the final slide, say goodbye as a terminal action, enter `FINISHED`, or close the session.

## Interruption recovery

`USER_INTERRUPTED` stores the exact logical cursor in `interruption_cursor`. After `ANSWER_COMPLETED`, that cursor is restored and `RESUME_NARRATION` is emitted. Pause/answer paths must not silently rewrite the checkpoint.

## Pause / resume

Pause preserves the full cursor and records `mode_before_pause`. Resume restores that mode and keeps the cursor. This is a **logical** contract only.

## Duplicate-event handling

`processed_event_ids` records every accepted event id. A repeat id returns the same logical outcome (`NO_ACTION`) without advancing slides or modes.

## Implemented in Iteration 2

- Pure `lesson_controller.py` (stdlib dataclasses/enums)
- Pytest suite under `tests/`
- This architecture document

## Intentionally unimplemented

- Pipecat / `agent.py` / `main.py` integration
- Frontend pause/goto controls and WebSocket command handlers
- Exact audible / browser audio resume (byte or playback offsets)
- Underage guardrails, RAG/knowledge ingest, metrics, transcript flywheel
- LLM-as-judge evals

**Exact audible resume is not complete.** Iteration 2 only establishes the logical cursor and transition contract for later pipeline wiring.
