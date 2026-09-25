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

Unchanged from Iteration 3: audible `BotStartedSpeaking`/`BotStoppedSpeaking` pairs; `UserStartedSpeaking` + `InterruptionFrame` for barge-in/pause; suppress cancelled stops.

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

## Still requires a real OpenAI session to verify

- End-to-end audio quality and live control timing under real TTS load
- Exact audible/byte resume (not implemented; logical cursor only)
- Live moderation latency under production network conditions
