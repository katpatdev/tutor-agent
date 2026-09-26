# Pipecat 1.11.0 compatibility notes

Verified against the locally installed package:

`pipecat-ai==1.11.0`  
Path: `.venv/lib/python3.11/site-packages/pipecat/`

Pinned in `pyproject.toml` as:

```toml
pipecat-ai[openai,silero,websocket]==1.11.0
```

Frontend packages inspected:

- `@pipecat-ai/client-js@1.6.0`
- `@pipecat-ai/websocket-transport@1.6.0`

## Voice pipeline classes and frames

| Symbol | Installed module path | Role |
|--------|------------------------|------|
| `Pipeline` | `pipecat.pipeline.pipeline` | Audio/LLM graph |
| `PipelineTask` / `PipelineParams` | `pipecat.pipeline.task` | `queue_frames`, `cancel` |
| `PipelineRunner` | `pipecat.pipeline.runner` | Runs the task |
| `BaseObserver` / `FramePushed` | `pipecat.observers.base_observer` | Lifecycle + inbound message observer |
| `BotStartedSpeakingFrame` | `pipecat.frames.frames` | Audible bot output started |
| `BotStoppedSpeakingFrame` | `pipecat.frames.frames` | Audible bot output finished |
| `UserStartedSpeakingFrame` | `pipecat.frames.frames` | User turn / interruption signal |
| `InterruptionFrame` | `pipecat.frames.frames` | Cancel pending bot output |
| `LLMMessagesAppendFrame` | `pipecat.frames.frames` | Append system instructions |
| `InputTransportMessageFrame` | `pipecat.frames.frames` | Inbound application message from client |
| `OutputTransportMessageUrgentFrame` | `pipecat.frames.frames` | Outbound urgent application message |
| `ProtobufFrameSerializer` | `pipecat.serializers.protobuf` | Serializes audio **and** transport messages (`ignore_rtvi_messages=False`) |
| `FastAPIWebsocketTransport` | `pipecat.transports.websocket.fastapi` | WS media + connect/disconnect events |

## Application messaging (Iteration 4)

### Python server path

1. Client bytes deserialize via `ProtobufFrameSerializer.deserialize` → `InputTransportMessageFrame(message=...)`.
2. `FastAPIWebsocketTransport` broadcasts `InputTransportMessageFrame` into the pipeline.
3. `LessonLifecycleObserver.on_push_frame` receives the frame and calls `LessonProtocolSession.handle_transport_message`.
4. Outbound replies use `PipelineTask.queue_frames([OutputTransportMessageUrgentFrame(message=...)])`.
5. Serializer wraps the message as protobuf `MessageFrame` JSON for the WebSocket.

RTVI envelope constants (from `pipecat.processors.frameworks.rtvi.models`):

- `MESSAGE_LABEL = "rtvi-ai"`
- inbound type `"client-message"` with payload `{ "t": "<msgType>", "d": <data> }`
- outbound type `"server-message"` with `{ "label": "rtvi-ai", "type": "server-message", "data": <lesson envelope> }`

No second WebSocket is used. Messaging shares the existing Protobuf media transport.

### JavaScript client path

From `@pipecat-ai/client-js` declarations (`dist/index.d.ts`):

- `PipecatClient.sendClientMessage(msgType: string, data?: unknown): void`
  - Sends RTVI `client-message` with `{ t: msgType, d: data }`
- Callback / event: `onServerMessage: (data: any) => void` and `RTVIEvent.ServerMessage = "serverMessage"`
  - Receives the `data` field of an RTVI `server-message`

Lesson commands use:

```ts
pcClient.sendClientMessage('lesson.command', commandEnvelope)
```

## Narration completion / interruption

Iteration 9 uses audible `BotStartedSpeaking`/`BotStoppedSpeaking` pairs for
one deterministic narration segment at a time. `UserStartedSpeaking` plus
`InterruptionFrame` handles barge-in/pause, and cancelled stops are suppressed.

Exact playback-offset resume is not available:

- inspected `BotStoppedSpeakingFrame` fields contain frame metadata but no
  played sample/time/byte/word offset;
- `OutputAudioRawFrame` contains produced audio bytes, sample rate, channels,
  and frame count, but no acknowledgement of browser playback;
- client-js callbacks are `botStoppedSpeaking: () => void` and
  `trackStarted: (track: MediaStreamTrack, participant?: Participant) => void`;
- the FastAPI WebSocket + Protobuf application protocol has no played-offset
  acknowledgement.

The truthful contract is `resume_accuracy: "segment"`; see
`docs/RESUME_ACCURACY.md`.

## Safety processors (Iteration 5)

| Symbol | Installed module path | Role in tutor-agent |
|--------|------------------------|---------------------|
| `TranscriptionFrame` | `pipecat.frames.frames` | Final STT text moderated by `InputSafetyProcessor` |
| `InterimTranscriptionFrame` | `pipecat.frames.frames` | Partials pass through; **not** moderated |
| `LLMFullResponseStartFrame` / `LLMFullResponseEndFrame` | `pipecat.frames.frames` | Bound one complete LLM response for output buffering |
| `LLMTextFrame` / `TextFrame` | `pipecat.frames.frames` | Candidate text held until moderated; released only if allowed |
| `TTSSpeakFrame` | `pipecat.frames.frames` | Trusted safety templates and replacements toward TTS |
| `SystemFrame` | `pipecat.frames.frames` | Passed through during buffering without releasing text |
| `FrameProcessor` | `pipecat.processors.frame_processor` | Base for `InputSafetyProcessor` / `OutputSafetyProcessor` |

Pipeline placement:

`stt → input_safety → user aggregator → llm → output_safety → tts`

OpenAI Moderation (SDK `openai==3.19.2`): `AsyncOpenAI.moderations.create(input=..., model=...)`. Default model: `omni-moderation-latest` (official Moderations guide + SDK `ModerationModel` literal).

## Temporary RAG context frames (Iteration 6)

| Symbol | Role |
|--------|------|
| `LLMMessagesTransformFrame` | Strip prior RAG system messages (`strip_rag_messages`) before a new turn |
| `LLMMessagesAppendFrame` | Inject untrusted reference system message for the current question only (`run_llm=False`) |

Pipeline placement:

`stt → input_safety → retrieval → user aggregator → llm → output_safety → tts`

OpenAI Embeddings (SDK `openai==3.19.2`): `AsyncOpenAI.embeddings.create(input=..., model=...)`. Default model: `text-embedding-3-small`.

## Metrics frames (Iteration 7)

Verified against installed `pipecat-ai==1.11.0`:

| Symbol | Module | Fields consumed |
|--------|--------|-----------------|
| `MetricsFrame` | `pipecat.frames.frames` | `data: list[MetricsData]` |
| `MetricsData` | `pipecat.metrics.metrics` | `processor`, optional `model` |
| `TTFBMetricsData` | `pipecat.metrics.metrics` | `value` (seconds) |
| `ProcessingMetricsData` | `pipecat.metrics.metrics` | `value` (seconds) |
| `LLMUsageMetricsData` / `LLMTokenUsage` | `pipecat.metrics.metrics` | `prompt_tokens`, `completion_tokens`, `total_tokens` |
| `TTSUsageMetricsData` | `pipecat.metrics.metrics` | `value` (characters) |
| `STTUsageMetricsData` / `STTUsage` | `pipecat.metrics.metrics` | `audio_seconds` |

Also present but **not** aggregated by this app: `TTFAMetricsData`, `TTFATMetricsData`, `TextAggregationMetricsData`, `TurnMetricsData` / deprecated `SmartTurnMetricsData`.

Observers available in install: `MetricsLogObserver`,
`ServiceMetricsObserver`. Application uses custom `LessonLifecycleObserver`
to ingest `MetricsFrame` and to forward speaking/lifecycle and inbound
transport messages **once per physical frame** (Pipecat notifies observers on
every pipeline hop).


`PipelineParams(enable_metrics=True, enable_usage_metrics=True)` enables emission.

Limitations: metric availability depends on each OpenAI STT/LLM/TTS service implementation; empty/malformed entries are ignored without crashing.

## Still requires a real OpenAI session to verify

- End-to-end audio quality and live control timing under real TTS load
- Exact playback-offset resume (not available; deterministic segment-level resume is implemented)
- Live moderation / embedding / metrics latency under production network conditions
- Narration naturalness and curriculum coverage for all eight slides (manual content review)
- TTS no-audio retry and voice-navigation under live conditions (Iteration 10.4)
- Interruption, RAG, consent, and safety scenarios that lack their own confirmed live PASS

## TTS no-audio (verified in pipecat-ai==1.11.0)

`TTSService._record_context_audio_outcome` pushes
`ErrorFrame(error="TTS context {uuid} completed with no audio", processor=<TTS>)`.
`BaseOutputTransport` emits `BotStoppedSpeaking` from `TTSStoppedFrame` only when
TTS audio was received. Application recovery observes `ErrorFrame` once per frame
id, matches the TTS processor, and retries the owned unit without regenerating
LLM text or re-running moderation.
