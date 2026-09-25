# Pipecat 1.11.0 compatibility notes

Verified against the locally installed package:

`pipecat-ai==1.11.0`  
Path: `.venv/lib/python3.11/site-packages/pipecat/`

Pinned in `pyproject.toml` as:

```toml
pipecat-ai[openai,silero,websocket]==1.11.0
```

## Classes and frames used

| Symbol | Installed module path | Role in Iteration 3 |
|--------|------------------------|---------------------|
| `Pipeline` | `pipecat.pipeline.pipeline` | Audio/LLM graph |
| `PipelineTask` / `PipelineParams` | `pipecat.pipeline.task` | Task lifecycle; `queue_frames`, `cancel` |
| `PipelineRunner` | `pipecat.pipeline.runner` | Runs the task |
| `BaseObserver` / `FramePushed` | `pipecat.observers.base_observer` | Thin lifecycle observer (no slide ownership) |
| `BotStartedSpeakingFrame` | `pipecat.frames.frames` | Audible bot output started |
| `BotStoppedSpeakingFrame` | `pipecat.frames.frames` | Audible bot output finished |
| `UserStartedSpeakingFrame` | `pipecat.frames.frames` | User turn / interruption signal |
| `InterruptionFrame` | `pipecat.frames.frames` | Cancel pending bot output (pause / interrupt) |
| `LLMMessagesAppendFrame` | `pipecat.frames.frames` | Append system instructions; `run_llm=True` |
| `FastAPIWebsocketTransport` | `pipecat.transports.websocket.fastapi` | WS media + `on_client_connected` / `on_client_disconnected` |
| OpenAI STT/LLM/TTS | `pipecat.services.openai.*` | Unchanged models from starter |

Inspected APIs (local `inspect` / source):

- `PipelineTask.queue_frames(self, frames, direction=DOWNSTREAM)`
- `PipelineTask.cancel(self, *, reason=None)`
- Transport event handlers: `on_client_connected(transport, websocket)`, `on_client_disconnected(transport, websocket)`

## Narration completion signal

**Primary signal:** `BotStoppedSpeakingFrame` after a matching `BotStartedSpeakingFrame` for the active `OutputPurpose`.

The runtime requires an audible start (`_utterance_audible`) before treating a stop as completion. Text-only generation without transport audio does not advance slides.

## Interruption detection

**Signal:** `UserStartedSpeakingFrame` while lesson mode is `PRESENTING` and output purpose is slide/resumed narration (or idle between utterances).

**Cancellation:** queue `InterruptionFrame` via `STOP_NARRATION` effect. Suppress the following `BotStoppedSpeakingFrame` so cancelled speech is not treated as slide completion.

With `PipelineParams(allow_interruptions=True)`, Pipecat also participates in interruption handling; the runtime still owns lesson state transitions.

## Avoiding duplicate observer advances

- Stable event IDs such as `slide-complete-{slide}-u{utterance}` feed `LessonController` duplicate protection.
- Completion ignored unless `_utterance_audible` is true.
- Interrupted stops set `_suppress_next_bot_stopped`.
- `start_session()` is single-shot per runtime instance.
- Q&A transition prompt is queued once (`_qa_transition_queued`).

## Still requires a real OpenAI session to verify

- End-to-end STT → LLM → TTS latency and audio quality
- Real VAD false-positive rate for `UserStartedSpeakingFrame`
- Exact ordering of interruption vs bot-stopped under live transport load
- Word-level / byte-level resume (not implemented; logical cursor only)
