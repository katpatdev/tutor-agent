# Session metrics, consent, and local SQLite persistence (Iteration 7)

## What is collected

Per WebSocket session (in memory always; optionally persisted):

- Session timing, disconnect reason, final lesson mode/slide
- Slides started/completed, interruptions, answers, pause/resume/navigation, Q&A entry
- Safety redirect/hold counts and content-free safety events
- RAG query/hit/miss counts and content-free RAG events
- Verified Pipecat metrics: TTFB, processing duration, LLM token usage, TTS characters, STT audio seconds
- Moderation/RAG latency summaries (count/min/mean/p50/p95/max)
- Whether a redacted transcript was saved

Monetary OpenAI cost is **not** calculated.

## Verified Pipecat metric sources (1.11.0)

See `docs/PIPECAT_COMPATIBILITY.md`. Frames: `MetricsFrame` carrying `TTFBMetricsData`, `ProcessingMetricsData`, `LLMUsageMetricsData`, `TTSUsageMetricsData`, `STTUsageMetricsData`.

## Transcript consent flow

1. Server sends `session.ready` (includes whether transcript persistence is available).
2. Frontend sends `session.configure` with `transcript_consent` Boolean (default unchecked).
3. Server acknowledges `session.configure_result` and starts the lesson.
4. If configure does not arrive before `SESSION_CONFIGURATION_TIMEOUT_SECONDS`, the lesson starts with consent **false**.

Global `TRANSCRIPT_PERSISTENCE_ENABLED=false` by default. Student consent cannot enable storage when the server flag is off.

Consent cannot change after the lesson starts; reconnect to change it.

## What is stored

| Data | When |
|------|------|
| Session metadata + content-free metrics | `METRICS_PERSISTENCE_ENABLED=true` |
| Safety/RAG reason codes (no raw text) | with metrics persistence |
| Redacted user/assistant transcript events | server flag **and** consent |

## What is never stored

- IP addresses, user agents, names, emails as identity fields
- Blocked unsafe student input
- Unmoderated assistant output / partial STT
- System prompts, retrieved chunks, embeddings, moderation scores
- Uploaded knowledge documents (remain in the separate in-memory knowledge store)
- API keys

## Redaction

Deterministic placeholders: `[EMAIL]`, `[PHONE]`, `[URL]`, `[IP_ADDRESS]`, `[SECRET]`, `[IDENTIFIER]`.

Limitations: cannot reliably strip all names/locations/PII. Consent remains required.

## SQLite

- Path: `SESSION_DB_PATH` (default `data/tutor_sessions.sqlite3`)
- Schema version: `PRAGMA user_version = 1`
- Foreign keys + WAL + busy timeout
- `data/` and SQLite sidecars are gitignored
- Restrictive directory/file permissions where the OS allows

## Retention

`SESSION_RETENTION_DAYS` (default 30, must be ≥ 1). Deletes expired **sessions** (cascades related rows). Logs only the deleted count.

## Metrics without consent

Content-free metrics/session rows may persist when metrics persistence is enabled even if transcript consent is false. The UI distinguishes metrics-only storage from transcript storage.

## Disconnect report

Printed once to the server console; never includes transcript text. Finalization is idempotent.

## Export CLI (local only)

```bash
uv run python -m session_export summary
uv run python -m session_export export --output exported_sessions.jsonl
uv run python -m session_export export --include-redacted-transcripts --overwrite --output exported_sessions.jsonl
```

Not an HTTP API. Default export excludes transcript text. Atomic write + overwrite guard.

## Failure behavior

Voice tutoring continues if metrics/transcript persistence fails. Transcript writes disable for the session after redaction/store failure. Disconnect cleanup and pipeline cancel still run. Errors are not sent verbatim to the frontend.

## Production limitations

- Local single-process SQLite is not multi-tenant hardened
- No authentication for exports (operator machine only)
- Redaction is incomplete by nature
- Playback completion is labeled honestly (`approved_for_tts` / `playback_completion_unknown`)
- Ready for Iteration 8 flywheel analysis of exported/consented data — not implemented here
