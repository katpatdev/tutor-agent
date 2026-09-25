# Live test results (Iteration 10)

> **LIVE OPENAI VALIDATION NOT RUN** (offline phase only).  
> This file contains **no** transcript text, document chunks, or secrets.

Authorization gate: live scenarios remain `NOT RUN` until the user explicitly authorizes billable OpenAI validation.

Configured models (from `.env.example` / code defaults; live confirmation pending):

| Role | Model |
|------|-------|
| STT | gpt-4o-transcribe |
| LLM | gpt-4o |
| TTS | gpt-4o-mini-tts |
| Moderation | omni-moderation-latest |
| Embeddings | text-embedding-3-small |

Resume wording under test: **sentence-section / segment-level** (not exact playback-offset resume).

## Scenarios

| # | Scenario | Status | Expected | Observed (no transcript text) | Content-free metric | Defect ID | Retest |
|---|----------|--------|----------|-------------------------------|---------------------|-----------|--------|
| 1 | Connection and first slide | NOT RUN | Connect; slide 1; audio; segment progress | — | — | — | — |
| 2 | Pause during narration | NOT RUN | Audio stops; paused; no advance | — | — | — | — |
| 3 | Resume | NOT RUN | Interrupted segment restarts; no exact-resume claim | — | — | — | — |
| 4 | Student interruption | NOT RUN | Answer then resume same segment | — | — | — | — |
| 5 | Second interruption | NOT RUN | Deterministic; no overlapping audio | — | — | — | — |
| 6 | Slide navigation | NOT RUN | Target authoritative; no stale advance | — | — | — | — |
| 7 | Slide 8 and Q&A | NOT RUN | All segments; one Q&A invite | — | — | — | — |
| 8 | Safety redirect | NOT RUN | Safe redirect; no slide advance | — | — | — | — |
| 9 | RAG | NOT RUN | Upload + source metadata; grounded answer | — | — | — | — |
| 10 | Disconnect report | NOT RUN | One content-free metrics report | — | — | — | — |
| 11 | Consent | NOT RUN | No rows without consent; redacted with consent | — | — | — | — |

## Usage notes

Approximate token/character usage will be recorded from verified Pipecat metrics after live runs. Monetary cost is **not** calculated.

## Defects

None recorded (live phase not started).
