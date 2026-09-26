# Resume accuracy

## Supported guarantee

Exact playback-offset resume is **not available** in this application.

The supported guarantee is deterministic **segment-level resume**:

- approved slide narration is split into deterministic text segments;
- application state owns the active segment and generation ID;
- a completed segment advances exactly once;
- an interrupted active segment is replayed from its beginning;
- navigation invalidates the prior generation so stale completion events cannot advance it.

`ResumeAccuracy.SEGMENT` is therefore the runtime and protocol value. The
`EXACT_PLAYBACK` enum/protocol value is reserved for a future implementation
and must not be presented as a current capability.

## TTS delivery recovery (Iteration 10.4)

Pipecat 1.11.0 `OpenAITTSService` can finish a context with **no audio**. The
transport then never emits `BotStoppedSpeaking` (it requires received TTS audio).
Application-owned speech units track purpose, generation, segment/answer index,
and attempt number. Verified no-audio `ErrorFrame`s (TTS processor + pinned
message) trigger one automatic retry of the same moderated text, then
deterministic skip/exhaustion with a content-free UI `audio_warning`.

Missing first-audio and missing-completion watchdogs use the same retry budget.
Inferred completions are counted separately from verified segment completions.
Resume remains **segment-level**.

## Spoken navigation and classroom controls (Iteration 10.5)

Clear commands are parsed locally after input safety and applied through
`LessonController` / `PresentationRuntime` without RAG or the conversational LLM.

| Kind | Examples | Effect |
|------|----------|--------|
| Navigation | “Let’s move to the next slide then, if possible”, “jump back to the previous slide”, “go to slide six” | Authoritative slide change once |
| Question (not control) | “What is on slide 6?”, “Are we repeating this slide?” | Conversational LLM path |
| Acknowledgement | “I got it”, “Yeah, I got that point” | Skip current/interrupted segment as `user_acknowledged` (not verified playback) |
| Continuation | “Continue”, “Go ahead” | Resume interrupted segment (may briefly re-hear) |
| Repeat | “Repeat this line”, “Say that again” | Replay exact moderated segment text |
| Q&A completion | “That’s all from my end” | Closing message → `FINISHED` |

Q&A wind-down defaults: `QA_SILENCE_TIMEOUT_SECONDS=12` per stage (invitation or
post-answer follow-up → reminder → closing). Ambiguous replies such as
“No, I would not like to know about a specific type” do not end the session.

## Why exact resume cannot be claimed

The following APIs were inspected in the installed dependency versions
(`pipecat-ai==1.11.0`, `@pipecat-ai/client-js@1.6.0`):

1. `BotStoppedSpeakingFrame` has only inherited frame metadata fields:
   `id`, `name`, `pts`, `broadcast_sibling_id`, `metadata`,
   `transport_source`, and `transport_destination`. It has no played byte,
   sample, time, word, or character offset.
2. `OutputAudioRawFrame` contains `audio: bytes`, `sample_rate`,
   `num_channels`, and derived `num_frames`. These describe server output
   audio, not how much the browser actually played.
3. Client-js declares `botStoppedSpeaking: () => void` and
   `trackStarted: (track: MediaStreamTrack, participant?: Participant) =>
   void`. Neither callback reports a playback offset.
4. The FastAPI WebSocket transport and `ProtobufFrameSerializer` carry audio
   and application messages, but this application protocol has no
   client-to-server acknowledgement for played bytes, samples, timestamps,
   words, or characters.

Server-produced audio duration or `pts` cannot substitute for a browser
playback acknowledgement: buffering, cancellation, device scheduling, and
transport delay can make produced and actually heard audio differ.

## Runtime behavior

- Pause or barge-in during a segment: stop output and retain that segment as
  interrupted.
- Resume: replay the interrupted segment from its beginning.
- Completed segment: queue the next segment.
- Final completed segment: complete the slide and either present the next
  slide or enter Q&A.
- Slide navigation/session end: invalidate the current narration generation.

Public `lesson.state.narration` metadata is content-free and one-based:
`current_segment`, `total_segments`, `resume_accuracy`, and optional safe
`error`. It never contains narration text.

## Future requirements for exact playback resume

An exact claim would require a verified client acknowledgement protocol tied
to a narration generation and segment, with a defined played sample/time
offset, monotonic sequencing, cancellation handling, and deterministic
server-side mapping back to resumable audio or text. That protocol does not
exist in the current stack.
