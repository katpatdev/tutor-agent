# Iteration 9 live test plan

> **LIVE OPENAI VALIDATION NOT RUN.**

These scenarios require a human-authorized session with valid OpenAI access,
microphone/speaker monitoring, browser observation, and a disposable classroom
knowledge document. Offline tests cannot establish real STT/LLM/TTS latency,
audio quality, or browser playback timing. Exact playback-offset resume is not
a pass criterion; the supported behavior is segment-level replay.

Record the actual result in each **Pass/Fail** and **Notes** field.

## 1. Connect and hear slide 1

- **Expected UI:** Connected; slide 1 of 8; one-based narration segment progress appears.
- **Expected audio:** One calm slide 1 narration stream starts.
- **Expected server:** Lesson is PRESENTING on slide index 0 with one active narration plan.
- **Expected metric/event:** One plan is created; generated segment count increases.
- **Pass/Fail:** Not run
- **Notes:** —

## 2. Pause in the middle of a narration segment

- **Expected UI:** PAUSED; Resume enabled; current segment retained.
- **Expected audio:** Active speech stops promptly.
- **Expected server:** Active segment is interrupted/incomplete; cancellation stop is suppressed.
- **Expected metric/event:** Pause and segment-interruption counters increase once.
- **Pass/Fail:** Not run
- **Notes:** —

## 3. Wait five seconds and confirm no further audio

- **Expected UI:** Remains PAUSED on the same slide and segment.
- **Expected audio:** Silence for the full five seconds; no next segment starts.
- **Expected server:** No segment completion, queueing, or slide transition.
- **Expected metric/event:** Completed/generated counts remain unchanged during the wait.
- **Pass/Fail:** Not run
- **Notes:** —

## 4. Resume and verify replay or accurate resume

- **Expected UI:** PRESENTING; status says segment-level resume accuracy.
- **Expected audio:** Interrupted segment restarts from its beginning. Exact word/byte resume is not expected.
- **Expected server:** Existing valid generation resumes and queues exactly one segment.
- **Expected metric/event:** Resume and segment-replay counters increase once.
- **Pass/Fail:** Not run
- **Notes:** —

## 5. Interrupt with a normal disaster question

- **Expected UI:** ANSWERING on the same slide.
- **Expected audio:** Narration stops and the tutor answers the question.
- **Expected server:** Interruption cursor preserves the active slide/segment; safety allows the normal question.
- **Expected metric/event:** Interruption count increases; RAG event may occur if knowledge matches.
- **Pass/Fail:** Not run
- **Notes:** —

## 6. Verify answer then return to the same slide

- **Expected UI:** Returns to PRESENTING on the interrupted slide and segment.
- **Expected audio:** Answer completes, then the interrupted segment replays and narration continues.
- **Expected server:** Answer completion changes output ownership to resumed narration without advancing the slide.
- **Expected metric/event:** Answer and segment-replay counters increase; no premature slide completion.
- **Pass/Fail:** Not run
- **Notes:** —

## 7. Interrupt the answer again

- **Expected UI:** Remains in a valid answer flow on the same slide.
- **Expected audio:** Prior answer audio stops cleanly; no answer/narration overlap.
- **Expected server:** Second interruption is serialized; cancelled stop cannot complete narration.
- **Expected metric/event:** Interruption/suppression counts reflect accepted events only.
- **Pass/Fail:** Not run
- **Notes:** —

## 8. Navigate to another slide during narration

- **Expected UI:** Requested slide appears and narration progress resets for its new plan.
- **Expected audio:** Old narration stops; requested slide narration begins once.
- **Expected server:** Target is validated, old generation invalidated, and one new plan becomes active.
- **Expected metric/event:** Navigation and plan counters increase; old slide does not complete.
- **Pass/Fail:** Not run
- **Notes:** —

## 9. Confirm no stale previous-slide audio

- **Expected UI:** Remains on the requested slide.
- **Expected audio:** No previous-slide segment or overlap is heard.
- **Expected server:** Late old-generation/cancelled completion is ignored.
- **Expected metric/event:** Stale-ignored or cancelled-suppressed counter increases if a late event arrives.
- **Pass/Fail:** Not run
- **Notes:** —

## 10. Navigate to slide 8

- **Expected UI:** Slide 8 of 8 with narration segment progress.
- **Expected audio:** Slide 8 narration starts normally.
- **Expected server:** PRESENTING on slide index 7; not yet in Q&A.
- **Expected metric/event:** Navigation and plan counters increase; Q&A count remains unchanged.
- **Pass/Fail:** Not run
- **Notes:** —

## 11. Let every slide 8 segment finish

- **Expected UI:** Segment number advances one-based until the final segment.
- **Expected audio:** Every segment plays in order without omission.
- **Expected server:** Non-final completions advance only the segment; final completion dispatches slide completion once.
- **Expected metric/event:** Completed segments equal generated segments for the plan.
- **Pass/Fail:** Not run
- **Notes:** —

## 12. Confirm one Q&A transition

- **Expected UI:** Enters Q&A and remains connected.
- **Expected audio:** Exactly one warm Q&A invitation.
- **Expected server:** One final slide completion and one Q&A transition.
- **Expected metric/event:** Q&A entry increases once; duplicate stop does not add another.
- **Pass/Fail:** Not run
- **Notes:** —

## 13. Ask a Q&A question

- **Expected UI:** Remains in Q&A.
- **Expected audio:** One relevant answer; slide narration does not restart.
- **Expected server:** Output purpose is Q&A response.
- **Expected metric/event:** Answer/token/latency metrics may increase; narration-plan counters do not.
- **Pass/Fail:** Not run
- **Notes:** —

## 14. Trigger a safe graphic-content redirect

- **Expected UI:** Shows a safe redirect notice without exposing categories or text.
- **Expected audio:** Trusted age-appropriate redirect is spoken; graphic answer is not.
- **Expected server:** Input/output safety policy chooses REDIRECT, not normal narration completion.
- **Expected metric/event:** Safety redirect count/event increases without raw content.
- **Pass/Fail:** Not run
- **Notes:** —

## 15. Verify safety redirect does not advance slides

- **Expected UI:** Same slide/Q&A state remains authoritative.
- **Expected audio:** Redirect completion does not start or complete an unrelated slide.
- **Expected server:** Safety output ownership absorbs its bot-stop; no `SLIDE_COMPLETED`.
- **Expected metric/event:** Safety count changes; slide/segment completion does not.
- **Pass/Fail:** Not run
- **Notes:** —

## 16. Upload one small test knowledge document

- **Expected UI:** Upload succeeds and source metadata appears; no secret or raw moderation data appears.
- **Expected audio:** No automatic narration or answer is triggered by upload alone.
- **Expected server:** Document is parsed, moderated, embedded, and stored in process memory.
- **Expected metric/event:** Document-upload count increases; rejected/failed uploads do not partially insert.
- **Pass/Fail:** Not run
- **Notes:** Use synthetic, non-sensitive classroom text only.

## 17. Ask a grounded question and verify source display

- **Expected UI:** Relevant source label/metadata is displayed.
- **Expected audio:** Answer uses the uploaded classroom facts and remains safe.
- **Expected server:** Retrieval runs only after input safety ALLOW; temporary context is isolated to the turn.
- **Expected metric/event:** RAG query/hit and retrieved-chunk counts increase.
- **Pass/Fail:** Not run
- **Notes:** —

## 18. Disconnect and inspect the content-free metrics report

- **Expected UI:** Disconnected cleanly.
- **Expected audio:** Playback stops.
- **Expected server:** Session finalizes once.
- **Expected metric/event:** Report includes narration counters and `resume_accuracy=segment`, but no narration/transcript text.
- **Pass/Fail:** Not run
- **Notes:** —

## 19. Reconnect with transcript consent disabled

- **Expected UI:** Consent remains unchecked; metrics-only/storage wording is accurate.
- **Expected audio:** Lesson operates normally.
- **Expected server:** Session config records consent false.
- **Expected metric/event:** Content-free metrics may persist; no transcript events are stored.
- **Pass/Fail:** Not run
- **Notes:** Verify using approved local inspection/export procedures only.

## 20. Reconnect with consent enabled and verify redacted approved text

- **Expected UI:** Consent acknowledgement is clear and connection succeeds.
- **Expected audio:** Lesson operates normally; blocked unsafe text is never repeated.
- **Expected server:** Transcript persistence activates only when the server flag and student consent are both true.
- **Expected metric/event:** Only redacted, approved transcript events are stored; blocked input, unmoderated output, prompts, narration plans, and secrets are absent.
- **Pass/Fail:** Not run
- **Notes:** Use synthetic PII markers and inspect only through the local sanitized export path.

## Human-authorized commands for later

First ensure transcript persistence settings match scenarios 19–20, then run:

```bash
uv run python scripts/live_test_preflight.py
uv run python main.py
```

In a second terminal:

```bash
cd frontend
yarn dev
```

Open the printed frontend URL and execute scenarios 1–20 in order. These
commands are documented only; they were not executed for live validation in
Iteration 9. Stop immediately if credentials, moderation behavior, consent, or
student-safety expectations are unclear.
