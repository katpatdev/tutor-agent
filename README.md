## Tutor Bot
Tutor bot is an MVP for an AI agent which teaches a class of students about natural disasters.

### About this repo
Code is not guaranteed to work, rather should be treated as starting point of the project. A submission for this assignment may change anything and everything about this code, with following limitations:
- Submission must not use any other framework, other than pipecat.
- Submission must not connect to any other external service, other than Open AI.

### Acceptance Criteria
Update existing agent code in such a way that following goals are met, while keeping conversation human-like and safe for students.

### Goals
- Should smoothly return back to topic once questions are answered.
- Should reliably end the presentation when last slide is finished and enter QnA mode, i.e., simple 2 way conversation, while also being able to return back at any point in presentation based on user request.
- Should allow pauses sent from frontend, i.e., agent stops speaking. The
  implemented guarantee resumes at the deterministic narration-segment
  boundary; exact browser playback-offset resume is not available in the
  current Pipecat/client transport APIs.

### Additional Goals
- Should have guard rails w.r.t underage users.
- Should have deterministic tests for all the business logic.
- Should have eval-style tests, i.e., LLM-as-a-judge tests.
- Should have a reliable way to ingest additional knowledge so that agent can answer questions better.
- Should log a metric report with information such as average latency, tokens consumed, etc. on console after user disconnects.

### Open Ended Goals
There must exist some way where agent's own old transcripts can be used to improve it's behavior.

### Local Setup
#### Setup Agent
Environment file (`.env` at root — never commit secrets)
```shell
# .env file
OPENAI_API_KEY=
# Optional non-secret overrides: see .env.example (moderation + RAG)
```
Starting Python Agent
```shell
uv sync
python main.py
```
Starting Frontend
```shell
cd frontend
yarn install
yarn dev
```

#### Knowledge upload (Iteration 6)

With the backend running:

1. Open the frontend and use **Classroom knowledge** to upload `.txt`, `.md`, or text-based `.pdf`.
2. Or call `POST /knowledge/documents` (multipart `file`) and `GET /knowledge/status`.
3. Knowledge is stored **in memory only** until the server process restarts.
4. Uploaded text is moderated before embedding; rejected documents are not stored.
5. Student questions that pass safety may retrieve temporary reference context for that turn only.

Do not put `OPENAI_API_KEY` in frontend / `VITE_` variables.

#### Session metrics & optional transcripts (Iteration 7)

- A disconnect metrics report prints to the **server console** (no transcript text).
- Optional local SQLite persistence is configured via `.env.example` (`SESSION_DB_PATH`, retention, consent flags).
- Transcript saving requires **both** `TRANSCRIPT_PERSISTENCE_ENABLED=true` and the student consent checkbox (off by default).
- Export locally only: `uv run python -m session_export summary` / `export` (not an HTTP API).
- See `docs/SESSION_DATA.md`.

#### Learning flywheel & evaluations (Iteration 8)

- Prompt registry: `prompts/` + `TUTOR_PROMPT_VERSION` (default `v1`). Candidates under `data/prompt_candidates/` never auto-activate.
- Local friction analysis (no OpenAI on historical transcripts): `uv run python -m flywheel analyze`
- Offline eval validation: `uv run python -m eval_harness validate` / `run-offline-fixtures`
- See `docs/LEARNING_FLYWHEEL.md` and `docs/EVALUATIONS.md`.

#### Segment-level narration and resume (Iteration 9)

- Safety-approved slide narration is split into deterministic, generation-scoped segments.
- Pause/barge-in replays the interrupted segment; slide navigation invalidates stale plans.
- `lesson.state` exposes one-based content-free segment progress.
- Exact playback-offset/word/audio-byte resume is **not available**. See `docs/RESUME_ACCURACY.md`.
- Offline validation: `uv run pytest tests/test_narration.py tests/test_presentation_runtime.py -q`.
- Live checklist: `docs/LIVE_TEST_PLAN.md`. **LIVE OPENAI VALIDATION NOT RUN.**
- Preflight only (no OpenAI request): `uv run python scripts/live_test_preflight.py`.
