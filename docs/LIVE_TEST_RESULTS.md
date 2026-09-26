# Live test results

> Contains **no** transcript text, document chunks, or secrets.

Configured models (code / `.env.example` defaults):

| Role | Model |
|------|-------|
| STT | gpt-4o-transcribe |
| LLM | gpt-4o |
| TTS | gpt-4o-mini-tts |
| Moderation | omni-moderation-latest |
| Embeddings | text-embedding-3-small |

Resume wording under test: **sentence-section / segment-level** (not exact playback-offset resume).

## Live run 1 — first manual browser validation (pre–Iteration 10.1)

Authorization: user-authorized controlled live OpenAI session.  
Content-free session end state (approximate):

| Field | Value |
|-------|-------|
| Duration | ~406 s |
| Lesson mode | `ANSWERING` |
| Slide | 3 |
| Interruptions | 1 |
| Answers | 0 |
| Q&A entries | 0 |
| Pauses / resumes | 1 / 1 |
| Segment interruptions | 1 |
| Cancelled completions suppressed | 2 |
| Moderation mean | ~0.44 s |
| TTFB mean / max | ~0.96 s / ~2.49 s |
| Processing mean / max | ~1.54 s / ~3.91 s |

### Scenarios (run 1)

| Scenario | Status | Observed (no transcript text) |
|----------|--------|-------------------------------|
| Connection | **PASS** | Session connected; health OK |
| Initial narration | **PASS** | First-slide audio played |
| Student interruption detection | **PASS** | Narration stopped on student speech |
| Interruption answer latency | **FAIL / NEEDS IMPROVEMENT** | ~3–4+ seconds before spoken answer |
| Return to same slide | **PARTIAL / FAIL** | Session ended still in `ANSWERING` with `answers = 0` |
| Pause | **PARTIAL** | Felt unreliable; speech sometimes seemed required |
| Resume | **PARTIAL** | Felt unreliable; speech sometimes seemed required |
| Slide progression | **FAIL / PARTIAL** | Appeared stuck around slide 3 |
| Q&A transition | **NOT FULLY TESTED** | — |
| Q&A answer | **NOT FULLY TESTED** | — |
| Disconnect | **PASS** | Content-free metrics report produced |

### Defects recorded (run 1)

1. **Stuck `ANSWERING` / answers=0** — Pause during answer then Resume restored `ANSWERING` + `BEGIN_ANSWER` without re-queuing TTS; required student speech to progress.
2. **No-answer / silence** — Tutor question waited indefinitely with no timeout continuation.
3. **Speech pace** — Tutor voice felt too slow for classroom use.
4. **Answer latency** — Serial STT → input moderation → (optional RAG) → full LLM → output moderation → TTS; no streaming moderation bypass.
5. **UI confusion** — Browser/OS microphone chrome may look like an app control; app owns labeled Connect/Disconnect/Pause/Resume/Go to Slide.

## Live run 2 — Iteration 10.1 retest (partial)

Observed stuck again after slide 6 interruption (`final_lesson_mode=ANSWERING`, `answers` never completed). Root cause refined: generic `_suppress_next_bot_stopped` set on pre-audio interrupt consumed the answer's legitimate `BotStoppedSpeaking`. Consent was `false` this session. Sentence gaps traced to Pipecat OpenAI TTS **sentence aggregation** (one API call per short sentence).

## Live run 3 — Iteration 10.2 retest

**NOT RUN.** Offline fixes prepared; awaiting manual confirmation.

```text
Pre-audio interruption recovery: PASS/FAIL
Mid-narration interruption recovery: PASS/FAIL
Returned from ANSWERING: PASS/FAIL
Sentence gaps: PASS/FAIL
Speech naturalness: PASS/FAIL
Slide progression: PASS/FAIL
Slide 8 → Q&A: PASS/FAIL
Q&A: PASS/FAIL

RAG empty-store behavior: PASS/FAIL
RAG document retrieval: PASS/FAIL
RAG source attribution: PASS/FAIL
RAG unrelated-query behavior: PASS/FAIL

Consent display: PASS/FAIL

Approx first-answer delay:
Approx sentence gap:
Notes:
```
