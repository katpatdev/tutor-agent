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

## Live run 3 — Iteration 10.2 path (user report) / pre–10.3

**Partial live session (UI evidence).** The user reported reaching **slide 8** and entering **Q&A**. Q&A answers were informative and sounded reasonably continuous. Slide narration quality was **unacceptable**: slide 1 had several short spoken sections with gaps; slides 2–8 often sounded like one generic sentence each with repetitive “Natural disasters…” openings; curriculum points were under-covered.

Distinction:

| Source | What it supports |
|--------|------------------|
| UI log (`Bot:` text events, lesson state lines) | Text/event sequencing and mode transitions; **not** precise audible gap timing |
| User audio perception | Within-slide choppiness and thin slide content |
| Server events in this log | Not a complete server-side TTS “no audio” evidence set for this particular run |

### Scenario status (run 3)

| Scenario | Status | Notes |
|----------|--------|-------|
| Slide progression to 8 | **PASS (UI)** | Reached slide 8 |
| Slide 8 → Q&A | **PASS (UI)** | Entered Q&A |
| Q&A answer quality | **PASS (user)** | Liked continuity/content |
| Narration curriculum coverage | **FAIL** | Thin / repetitive; Iteration 10.3 targets this |
| Within-slide continuity | **FAIL** | Gaps / short bursts |
| Interruption / RAG / consent / safety | **NOT CONFIRMED** | Do not mark PASS without dedicated results |

## Live run 4 — Iteration 10.3 narration retest (user report)

**Partial live session with matching server log.** Narration content improved (connected explanations, fewer repetitive openings). Interruption detection, answer quality/speed, and Q&A style were satisfactory to the user.

Remaining failures (verified against server log):

- Multiple `OpenAITTSService … completed with no audio` ErrorFrames
- UI showed approved `Bot:` text while audio was missing or incomplete
- Lesson stalled waiting for audible BotStopped that never arrived
- Speaking again unstuck the session but replayed interrupted segments
- Spoken “go to slide 6” / “next slide” was answered by the LLM only; slide index did not change

## Live run 5 — Iteration 10.4 / pre-10.5 live session (2026-09-26)

**Partial PASS with remaining defects (user UI log + server evidence).**

| Area | Result | Notes |
|------|--------|-------|
| Connection | PASS | localhost:7860 |
| Reached slide 8 → Q&A | PASS | |
| Narration content | IMPROVED | Connected explanations |
| No permanent TTS freeze | PASS | no-audio ErrorFrames still occurred; recovery prevented permanent freeze |
| Natural voice nav (“then, if possible”, “jump back”) | FAIL | Fullmatch grammar miss → LLM promise without slide change |
| Acknowledgements / “repeat this line” | FAIL | Re-taught via LLM |
| Q&A end after “that’s all” | FAIL | Stayed in QA_MODE until disconnect |
| Q&A text duplicated in UI log | NOT OBSERVED | Single generated answer in transcript |
| Audible answer duplication | UNVERIFIED | Pending TTS retry correlation; not proven from UI lines alone |

## Live run 6 — Iteration 10.5 retest

**NOT RUN.** Offline classroom-control + Q&A wind-down prepared; awaiting manual microphone confirmation.

### Checklist (blank until user confirms)

```text
Connection: PASS/FAIL
Narration content: PASS/FAIL
No permanent TTS freeze: PASS/FAIL

“I got it” avoids re-explanation: PASS/FAIL
“Repeat this line” replays only the segment: PASS/FAIL
“Continue” resumes correctly: PASS/FAIL

“Let’s move to the next slide then, if possible”: PASS/FAIL
“Can we jump back to the previous slide?”: PASS/FAIL
“Go to slide six”: PASS/FAIL
“What is on slide six?” does not navigate: PASS/FAIL

Interruption answer quality unchanged: PASS/FAIL
Return to narration: PASS/FAIL
No unintended repeated answer audio: PASS/FAIL

Slide 8 → Q&A: PASS/FAIL
First Q&A silence reminder: PASS/FAIL
Speaking after reminder cancels closing: PASS/FAIL
“That’s all from my end” closes politely: PASS/FAIL
Ambiguous “No, not that type” keeps Q&A open: PASS/FAIL
Automatic silence closing: PASS/FAIL
Session finishes once: PASS/FAIL

Notes:
```

```text
Connection: PASS/FAIL
All slide content audible: PASS/FAIL
No unexplained long silence: PASS/FAIL
Automatic TTS retry observed: PASS/FAIL/NOT OBSERVED
Lesson recovered without speaking: PASS/FAIL/NOT TESTED
No duplicated segment after recovery: PASS/FAIL
Interruption stops narration: PASS/FAIL
Answer quality unchanged: PASS/FAIL
Return to narration: PASS/FAIL
“Next slide” navigation: PASS/FAIL
“Go to slide 6” navigation: PASS/FAIL
“Previous slide” navigation: PASS/FAIL
Pause/Resume: PASS/FAIL
Slide 8 → Q&A: PASS/FAIL
Q&A response: PASS/FAIL
Audio warning shown on exhausted failure: PASS/FAIL/NOT OBSERVED
Approximate longest unexplained silence:
Notes:
```
