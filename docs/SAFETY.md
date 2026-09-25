# Student safety guardrails (Iteration 5)

## Intended audience

Classroom-age students learning about natural disasters with a voice tutor. Content should stay calm, scientific, and age-appropriate. The system is **not** a crisis hotline, therapist, doctor, or emergency responder.

## Threat model

| Threat | Mitigation |
|--------|------------|
| Student asks for graphic / sensational harm detail | OpenAI Moderation + policy → `REDIRECT`; blocked text never reaches the LLM |
| Student expresses ordinary fear | Allowed; prompt + calm educational answers |
| Credible self-harm / immediate danger / sexual-minors signals | Fail closed → `SAFETY_HOLD`; pause lesson; disable Resume/navigation |
| Unsafe LLM generation | Buffer full response; moderate before TTS; replace if blocked |
| Moderation timeout / API failure / unknown flag / oversized text | Fail closed → hold or safe replacement |
| Keyword-only false blocks on “earthquake”, “injury”, etc. | Moderation is primary; local detector is narrow first-person distress/danger only |
| Prompt-only “safety” | Prompt is guidance only — **not** sufficient alone |
| Privacy leak via logs / state messages | No raw transcripts in safety events or `lesson.state`; no scores/categories to UI |

## Input moderation flow

Pipeline order (Pipecat 1.11.0):

`transport.input → STT → InputSafetyProcessor → user context aggregator → LLM → OutputSafetyProcessor → TTS → transport.output → assistant aggregator`

1. Only final `TranscriptionFrame` text is moderated (not `InterimTranscriptionFrame` partials).
2. Duplicate finals (same text fingerprint) are moderated once.
3. Control / transport messages pass through without moderation.
4. Decisions:
   - **ALLOW** — release original transcript downstream.
   - **REDIRECT** — do not release transcript; interrupt; speak trusted template (`SAFETY_REDIRECT`); after audible completion, resume interrupted lesson logically.
   - **SAFETY_HOLD** — do not release transcript; pause; speak trusted template (`SAFETY_MESSAGE`); stay on hold (no auto-resume).

Blocked student text is never inserted into LLM context and is never logged.

## Output moderation flow

1. On `LLMFullResponseStartFrame`, begin buffering.
2. Accumulate `LLMTextFrame` text; do **not** forward unmoderated tokens to TTS.
3. On `LLMFullResponseEndFrame`, moderate the complete candidate (bounded by `MODERATION_MAX_CHARACTERS`).
4. If `ALLOW`, release start + aggregated text + end frames toward TTS.
5. Otherwise replace with a predefined `TTSSpeakFrame` template (trusted application output).
6. Oversized, timeout, or error → fail closed (replacement / no original release).
7. Predefined safety templates queued by the runtime bypass LLM output gating (they are source-controlled).

### Latency tradeoff

Full-response buffering means TTS cannot start until the LLM finishes **and** moderation returns. This adds end-to-end latency (LLM completion time + moderation RTT, bounded by `MODERATION_TIMEOUT_SECONDS`) compared with token-streaming TTS. The tradeoff is intentional so students never hear unmoderated model text.

## Decisions

| Decision | Meaning |
|----------|---------|
| `ALLOW` | Age-appropriate educational content |
| `REDIRECT` | Inappropriate / graphic / unrelated unsafe request without clear immediate-danger signal |
| `SAFETY_HOLD` | High-risk, unknown flag, timeout, service failure, or oversized content |

Runtime safety status (not a second lesson FSM): `normal` | `redirecting` | `hold`.

On `hold`: `can_pause` / `can_resume` / `can_navigate` are false; Disconnect remains available; reconnect = fresh session.

## Predefined templates

Stored in `safety_policy.TEMPLATES` (short, calm, no country-specific emergency numbers, no medical claims):

- graphic / inappropriate redirects
- distress support (prompt-side; allow path)
- immediate-danger / self-harm holds
- moderation unavailable
- unsafe output replacement
- oversized content

## Failure behavior

Fail closed: do not send blocked or unchecked content to the LLM or TTS. Prefer hold or a safe template.

## Privacy rules

Safety events (in-memory only) may include: timestamp, session-local event id, decision (`allow`/`redirect`/`hold`), source, reason code, latency, fallback flag.

Must **not** include: raw student/assistant text, API keys, full OpenAI payloads, PII, category score maps in UI/state.

Disk persistence of transcripts/events is deferred to a later iteration.

## False positives / false negatives

- Moderation may over-flag scientific discussion of disasters (false positive) → redirect/hold that a human teacher would allow.
- Moderation may miss cleverly phrased harm (false negative) → prompt + local detector are weak backups only.
- Local regex for distress/danger is deliberately small and cannot replace the API.
- Unknown flagged category names fail closed (may over-hold).

## Why prompts are not enough

System prompts can be ignored, jailbroken, or overridden by model drift. Safety requires **deterministic gates** around STT→LLM and LLM→TTS using the OpenAI Moderations API plus application policy.

## Document upload safety (Iteration 6)

Uploaded classroom documents are untrusted. Before embedding:

- OpenAI Moderation runs on each chunk via the shared moderation abstraction
- A document-ingestion policy rejects inappropriate/graphic/dangerous material
- Timeouts and API failures fail closed (nothing inserted)
- Rejected text is not logged

Retrieval happens **only after** student input safety returns ALLOW. Safety redirect/hold paths never trigger retrieval. See `docs/KNOWLEDGE_RAG.md`.

## Session metrics privacy (Iteration 7)

Disconnect reports and SQLite metric rows are content-free. Transcripts require server enable + consent and are redacted before persistence. See `docs/SESSION_DATA.md`.

## Production remaining work

- Live load / latency measurement under real STT/LLM/TTS
- Metrics persistence and operator dashboards
- Teacher/admin override and audit workflow
- Deployment hardening (auth, rate limits, regional crisis resources chosen by operators—not hardcoded here)
- Transcript retention policy (later iteration)
- Durable / authenticated knowledge storage beyond in-memory demo use
