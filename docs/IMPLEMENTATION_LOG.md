# Implementation log

This file is **append-only**.

- Append a new `## Iteration N` section after each implementation iteration.
- Never rewrite, delete, or reorder prior entries to hide failures, uncertainties, or decisions.
- Record failed validations honestly.

Template for every iteration:

```markdown
## Iteration N: Title

- Date:
- Objective:
- Starting commit:
- Starting Git status:

### Before

### Changes made

### Commands executed

### Dependency changes

### Validation results

### Problems or uncertainties

### Result after iteration

### Next recommended step
```

---

## Iteration 1: Environment setup and repository governance

- Date: 2026-09-25
- Objective: Configure exact Python/Node/Yarn toolchains, install backend and frontend dependencies reproducibly, add governance docs (`AGENTS.md`, `docs/SETUP.md`, implementation log), and run non-paid baseline validation. No tutor feature work.
- Starting commit: `5754a57a3bb13d7e13d466ccbc260917754cec27`
- Starting Git status: clean working tree on branch `main` (no local modifications before this iteration).

### Before

- Repository contained starter source (`agent.py`, `main.py`, `frontend/*`) with no `.venv`, no `uv.lock`, no `frontend/node_modules`.
- System Python was `3.12.3`; project requires exact `3.11.14` via `pyproject.toml` (`requires-python = "==3.11.14"`) while `.python-version` was only `3.11`.
- `uv` was not installed. NVM was present. Node was `v24.14.0`. Yarn was not installed.
- `.gitignore` ignored `uv.lock`, preventing lockfile tracking.
- Host: WSL2 Ubuntu 24.04.2 LTS (`Linux kathpc 6.18.33.2-microsoft-standard-WSL2`).

### Changes made

- File: `.python-version`
  - Previous state: `3.11`
  - New state: `3.11.14`
  - Reason: Pin the exact required CPython version for `uv`.

- File: `.gitignore`
  - Previous state: contained a `uv.lock` ignore rule under the UV section.
  - New state: `uv.lock` ignore rule removed; all other rules unchanged.
  - Reason: Make Python dependency resolution reproducible and trackable.

- File: `uv.lock`
  - Previous state: absent
  - New state: created by `uv sync --python 3.11.14`
  - Reason: Lock resolved Python dependencies (authoritative detailed record).

- File: `.venv/`
  - Previous state: absent
  - New state: created locally (gitignored)
  - Reason: Project virtual environment for Python 3.11.14.

- File: `.nvmrc`
  - Previous state: absent
  - New state: `22`
  - Reason: Pin Node major version for NVM.

- File: `frontend/package.json`
  - Previous state: no `packageManager` / `engines`
  - New state: added `"packageManager": "yarn@1.22.22"` and `"engines": {"node": ">=22.0.0 <23.0.0"}`; scripts and dependencies preserved.
  - Reason: Document Yarn Classic and Node 22 constraints without migrating lockfile format.

- File: `frontend/node_modules/`
  - Previous state: absent
  - New state: created by `yarn install --frozen-lockfile` (gitignored)
  - Reason: Frontend dependency install.

- File: `.env.example`
  - Previous state: absent
  - New state: `OPENAI_API_KEY=` only
  - Reason: Document required env var without secrets.

- File: `AGENTS.md`
  - Previous state: absent
  - New state: project mission, constraints, evidence rules, architecture rules, change discipline, verification, iteration logging.
  - Reason: Repository governance for humans and agents.

- File: `docs/SETUP.md`
  - Previous state: absent
  - New state: WSL/Linux setup guide matching commands used in this iteration.
  - Reason: Reproducible onboarding.

- File: `docs/IMPLEMENTATION_LOG.md`
  - Previous state: absent
  - New state: append-only log with Iteration 1 entry.
  - Reason: Required iteration logging.

- File: `frontend/dist/`
  - Previous state: absent
  - New state: produced by `yarn vite build` (gitignored via `dist`)
  - Reason: Frontend build validation artifact only; not intended for commit.

Application source files (`agent.py`, `main.py`, `frontend/app.ts`, etc.) were **not** modified.

### Commands executed

Meaningful commands (abbreviated list):

- Baseline inspection: `pwd`, `uname -a`, `cat /etc/os-release`, `git status`, `git rev-parse`, `git ls-files`, version probes for python/uv/node/npm/yarn/nvm
- `curl -LsSf https://astral.sh/uv/install.sh | sh`
- `uv python install 3.11.14`
- `uv sync --python 3.11.14`
- `uv run python --version`
- `uv pip show pipecat-ai`
- `uv pip freeze`
- `nvm install 22` / `nvm use 22` / `nvm alias default 22`
- `npm install --global yarn@1.22.22`
- First `yarn install --frozen-lockfile` (failed: `ENETUNREACH`)
- `yarn config set registry https://registry.npmjs.org/` (wrote user `~/.yarnrc`, outside repo)
- Retry with `NODE_OPTIONS=--dns-result-order=ipv4first` then `yarn install --frozen-lockfile` (succeeded)
- `yarn tsc --noEmit`
- `yarn vite build`
- `uv run python -c "import pipecat, fastapi, uvicorn; ..."`
- `uv run python -c "import agent, main; ..."`
- Controlled `uv run python main.py` smoke test + `POST/GET http://127.0.0.1:7860/connect`, then process stopped

### Dependency changes

- Python version: `3.11.14` (`uv run python --version`)
- Resolved Pipecat version: `pipecat-ai==1.11.0` (from range `>=0.0.102`; `pyproject.toml` ranges not changed)
- Node version: `v22.23.3`
- npm version: `10.9.9`
- Yarn version: `1.22.22`
- Lockfiles:
  - `uv.lock` **created**
  - `frontend/yarn.lock` **unchanged** (sha256 `1c754f9dfa239da1de1fb5ceb8abd37db76b2dda742065de95f456e6b898188f`)
- uv tool version used: `0.12.19`
- Transitive notable packages resolved with Pipecat: `fastapi==0.141.1`, `uvicorn==0.54.0`, `openai==3.19.2`, `loguru==0.7.3`

### Validation results

- Command: `uv run python --version`
  - Pass
  - Output: `Python 3.11.14`

- Command: `uv sync --python 3.11.14`
  - Pass
  - Created `.venv` and `uv.lock`; installed 60 packages

- Command: `yarn install --frozen-lockfile` (first attempt)
  - Fail
  - Error: `AggregateError [ENETUNREACH]` while fetching packages

- Command: `yarn install --frozen-lockfile` (retry with IPv4-first DNS + npm registry)
  - Pass
  - `yarn.lock` remained unchanged

- Command: `yarn tsc --noEmit`
  - Pass
  - Exit 0

- Command: `yarn vite build`
  - Pass
  - Exit 0; wrote gitignored `frontend/dist/`

- Command: `uv run python -c "import pipecat, fastapi, uvicorn; print('Core backend imports OK')"`
  - Pass
  - Printed `Core backend imports OK` (Pipecat logs version 1.11.0)

- Command: `uv run python -c "import agent, main; print('Application imports OK')"`
  - Pass
  - Printed `Application imports OK`

- Command: controlled backend smoke (`uv run python main.py`, then HTTP probes, then stop)
  - Pass
  - Uvicorn listened on `0.0.0.0:7860`
  - `POST /connect` → HTTP 200, body `{"ws_url":"ws://localhost:7860/ws"}`
  - `GET /connect` → HTTP 405 `Method Not Allowed` (matches FastAPI route declaring POST only)
  - No OpenAI STT/LLM/TTS calls exercised; no `.env` created or inspected

### Problems or uncertainties

- First Yarn install failed with `ENETUNREACH` (likely IPv6/network path). Retry succeeded after preferring IPv4 DNS results and pointing Yarn at `https://registry.npmjs.org/`. User-level `~/.yarnrc` was updated outside the repo.
- Resolved Pipecat is `1.11.0`, far newer than the declared minimum `0.0.102`. APIs in starter code may or may not match this major line; this was not audited against Pipecat 1.x docs in Iteration 1.
- No billable OpenAI end-to-end voice test was run (blocked until a valid `.env` exists and the user approves paid API calls).
- ChatGPT Plus does not substitute for OpenAI API billing; live voice features remain blocked without an API key.

### Result after iteration

What works now:

- Exact Python 3.11.14 environment via `uv` + `.venv` + trackable `uv.lock`
- Node 22 + Yarn Classic 1.22.22 toolchain for frontend
- Frontend deps installed; typecheck and Vite production build succeed
- Backend core/application imports succeed
- Non-paid `/connect` smoke test succeeds and process was stopped

What has not been implemented (by design for Iteration 1):

- Topic return, Q&A mode, pause/resume, underage guardrails, tests, knowledge ingestion, disconnect metrics reporting, transcript flywheel
- No application behavior changes
- No Git commit/push

### Next recommended step

**Iteration 2:** Design and implement a deterministic presentation state machine (slide index, PRESENTING/QA/PAUSED) owned by application code—not the LLM—plus control-message contracts for pause/resume and slide navigation, without yet wiring full knowledge/flywheel features.

---

## Iteration 2: Deterministic lesson state machine

- Date: 2026-09-25
- Objective: Implement and unit-test a framework-independent presentation state machine that owns lesson mode, slide index, logical narration cursor, interruption checkpoint, pause/resume, final-slide→Q&A, explicit session completion, and duplicate-event protection. No Pipecat/frontend integration; no OpenAI calls.
- Starting commit: `5754a57a3bb13d7e13d466ccbc260917754cec27`
- Starting Git status: **not clean** — Iteration 1 artifacts still uncommitted (`M .gitignore`, `M .python-version`, `M frontend/package.json`, untracked `.env.example`, `.nvmrc`, `AGENTS.md`, `docs/`, `uv.lock`). No ignored `.env` present at start of this iteration. Iteration 1 files were left intact; no commit/push.

### Before

- Lesson progression in `agent.py` was silence-timer driven via `PresentationObserver0` with no explicit mode enum, no Q&A terminal distinction from goodbye, no pause/resume cursor contract, and no duplicate-event protection.
- No `lesson_controller` module, no pytest suite, no `docs/ARCHITECTURE.md`.
- Python 3.11.14 / pipecat-ai 1.11.0 from Iteration 1 remained in place.

### Changes made

- File: `lesson_controller.py`
  - Previous state: absent
  - New state: `LessonMode`, `LessonEventType`, `LessonEffect`, `NarrationCursor`, `LessonEvent`, `LessonState`, `TransitionResult`, `InvalidLessonTransition`, `LessonController`
  - Reason: Deterministic domain ownership of lesson control plane.

- File: `tests/__init__.py`
  - Previous state: absent
  - New state: empty package marker
  - Reason: Pytest package root.

- File: `tests/test_lesson_controller.py`
  - Previous state: absent
  - New state: 20 deterministic tests covering required acceptance cases
  - Reason: Prove controller behavior without Pipecat/network/audio/OpenAI.

- File: `docs/ARCHITECTURE.md`
  - Previous state: absent
  - New state: control-vs-LLM split, states, events, effects, transition table, indexing, limitations
  - Reason: Document the Iteration 2 contract.

- File: `pyproject.toml`
  - Previous state: runtime deps only
  - New state: `[dependency-groups] dev = ["pytest>=9.1.1"]` via `uv add --dev pytest`
  - Reason: Deterministic test runner as a development dependency.

- File: `uv.lock`
  - Previous state: Iteration 1 lockfile (no pytest)
  - New state: updated by `uv` to include pytest 9.1.1 and transitive test deps
  - Reason: Reproducible dev dependency resolution.

- File: `docs/IMPLEMENTATION_LOG.md`
  - Previous state: Iteration 1 entry only
  - New state: Iteration 2 entry appended
  - Reason: Append-only iteration logging.

**Not modified:** `agent.py`, `main.py`, any `frontend/*` application sources (Iteration 1 `frontend/package.json` governance fields unchanged in this iteration).

### Commands executed

- Prep: `git status --short`, `git rev-parse HEAD`, `uv run python --version`, `uv pip show pipecat-ai`
- `uv add --dev pytest`
- Implemented domain module + tests + architecture doc
- `uv run pytest -q`
- `uv run python -m compileall lesson_controller.py tests`
- `uv run python -c "import agent, main; print('Application imports OK')"`
- `git diff --check`, `git status --short`, `git diff --stat`

### Dependency changes

- Python version: 3.11.14 (unchanged)
- Resolved Pipecat version: 1.11.0 (unchanged)
- Development dependency added: **pytest 9.1.1** (plus transitive: iniconfig, pluggy, pygments)
- Node / Yarn: not touched this iteration
- Lockfile: `uv.lock` updated by `uv` (not hand-edited)

### Validation results

- Command: `uv run pytest -q`
  - Pass
  - Output: `20 passed in 0.07s`

- Command: `uv run python -m compileall lesson_controller.py tests`
  - Pass

- Command: `uv run python -c "import agent, main; print('Application imports OK')"`
  - Pass
  - Output: `Application imports OK`

- Command: `git diff --check`
  - Pass (no whitespace errors reported)

### Problems or uncertainties

- Working tree was not clean at Iteration 2 start because Iteration 1 was never committed; proceeded without committing or overwriting Iteration 1 governance files.
- Exact audible/browser audio resume remains unimplemented by design; only logical cursor contracts exist.
- Application layer still uses silence-based slide advancement in `agent.py`; controller is not wired yet.

### Design decisions

- Invalid transitions raise `InvalidLessonTransition` and leave the prior immutable state object unchanged (documented alternative to a Result-error union).
- Duplicate protection uses a `frozenset` of `processed_event_ids` for the session.
- `USER_INTERRUPTED` / `PAUSE_REQUESTED` may carry an optional `cursor` payload so the application can report the logical position at interrupt/pause time without a separate progress event.
- Pause allowed from `PRESENTING`, `INTERRUPTED`, `ANSWERING`, `QA_MODE`; resume restores `mode_before_pause`.
- Final slide completion → `QA_MODE` + `ENTER_QA`; only `END_SESSION` → `FINISHED`.

### Known limitations

- Not integrated with Pipecat pipeline or frontend control messages.
- No audio-byte / WebRTC playback offset tracking.
- No guardrails, RAG, metrics, or transcript flywheel.
- Cursor segment/word advancement during live narration is not driven by a dedicated progress event yet (application must supply cursor on interrupt/pause).

### Result after iteration

- Deterministic lesson controller exists and is fully covered by 20 passing unit tests.
- Architecture contract documented.
- Starter agent/frontend behavior unchanged.

### Next recommended step

**Iteration 3:** Integrate the tested `LessonController` into the Pipecat pipeline (replace silence-timer slide ownership), map transport/control messages to lesson events, and honor returned effects—without yet completing full knowledge ingest or transcript flywheel.

---

## Iteration 3: Pipecat runtime integration (replace silence timer)

- Date: 2026-09-25
- Objective: Integrate `LessonController` with the Pipecat backend via a per-session runtime; remove `PresentationObserver0` silence-timer progression; add offline runtime tests; pin `pipecat-ai==1.11.0`. No frontend controls, RAG, flywheel, or full metrics. No live OpenAI calls during validation.
- Starting commit: `8d0ba5183468c8f02f8ecd35c3f9dd012c5cf403`
- Starting Git status: clean working tree on `main` after Iteration 1–2 checkpoint commit (local `.env` present, gitignored).

### Before

- `agent.py` used `PresentationObserver0` with a 3.0s `call_later` silence timer to advance slides and a final “Say goodbye and end the presentation.” branch that skipped reliable Q&A.
- TTS instructions referred to “business people” / “Speak fast.”
- `LessonController` existed but was not wired.
- Pipecat dependency was open-ended `>=0.0.102` (resolved 1.11.0).

### API-key preflight

- `.env` exists: yes
- `API key loaded:` **True** (Boolean only; key never printed)
- `.env` gitignored and not tracked

### Files created

- `presentation_runtime.py` — per-session runtime, `OutputPurpose`, `FrameSink`, test doubles
- `tests/test_presentation_runtime.py` — 20 offline runtime/integration tests
- `docs/PIPECAT_COMPATIBILITY.md` — inspected Pipecat 1.11.0 APIs and lifecycle mapping

### Files modified

- `agent.py` — removed `PresentationObserver0`; wired `PresentationRuntime` + `LessonLifecycleObserver`; base tutor prompt; student TTS instructions; connect starts lesson; disconnect ends session + cancel
- `docs/ARCHITECTURE.md` — ownership boundaries, event/effect maps, limitations
- `docs/IMPLEMENTATION_LOG.md` — this entry appended
- `pyproject.toml` — pin `pipecat-ai[openai,silero,websocket]==1.11.0`
- `uv.lock` — regenerated for exact pin (version remained 1.11.0)

### Before-and-after behavior

| Concern | Before | After |
|---------|--------|-------|
| Slide advance | 3s bot silence timer | `BotStoppedSpeaking` after audible narration + controller `SLIDE_COMPLETED` |
| Final slide | Goodbye / end presentation text | All 8 slides; then `QA_MODE` + Q&A invite |
| Interrupts | Flag + continue-after-silence | `USER_INTERRUPTED` → stop via `InterruptionFrame` → answer → `RESUME_NARRATION` |
| Authority | Observer counters | One `LessonController` per WebSocket session |
| Persona | Business / speak fast | Student science tutor persona + calm TTS |

### Exact Pipecat APIs verified (1.11.0)

- `Pipeline`, `PipelineTask.queue_frames`, `PipelineTask.cancel`, `PipelineParams(allow_interruptions=True)`
- `BaseObserver.on_push_frame` / `FramePushed`
- Frames: `BotStartedSpeakingFrame`, `BotStoppedSpeakingFrame`, `UserStartedSpeakingFrame`, `InterruptionFrame`, `LLMMessagesAppendFrame`
- Transport: `FastAPIWebsocketTransport` events `on_client_connected`, `on_client_disconnected`
- Services unchanged: OpenAI STT/LLM/TTS model names preserved

### Design decisions

- Keep `lesson_controller.py` free of Pipecat; lazy-import frames in runtime unless test factories inject fakes.
- Treat only audible start→stop pairs as narration completion; suppress bot-stop after interruption/pause.
- Mint stable completion IDs `slide-complete-{slide}-u{utterance}` for duplicate protection.
- Backend `pause` / `resume` / `go_to_slide` methods exist but are not frontend-wired yet.
- Offline tests use `asyncio.run` (no pytest-asyncio dependency).

### Commands executed

- Preflight: `uv run pytest -q` (20 passed), env Boolean check, Pipecat inspect
- `uv lock` / `uv sync` after pin
- `uv run pytest -q` (40 passed)
- `uv run python -m compileall ...`
- `uv run python -c "import agent, main, lesson_controller, presentation_runtime; ..."`
- `rg` obsolete strings; non-billable `POST /connect` smoke; `git diff --check`

### Validation results

- `uv run pytest -q` → **40 passed** (20 controller + 20 runtime), 2 Pipecat deprecation warnings on agent import
- `compileall` → pass
- imports → `imports passed`
- `POST /connect` → HTTP **200**, `{"ws_url":"ws://localhost:7860/ws"}`; server stopped; no WebSocket client opened
- Obsolete runtime strings absent from `agent.py` / `presentation_runtime.py` (only asserted/historical in tests/docs)
- No live OpenAI STT/LLM/TTS invoked during this iteration’s validation
- No secret printed or committed

### Failures encountered and resolutions

- Initial runtime tests used `@pytest.mark.asyncio` without plugin → converted to `asyncio.run` wrappers; all passed.

### Known limitations

- No frontend pause/goto wiring
- Logical cursor only (not audio-byte resume)
- Baseline persona ≠ full underage moderation
- No RAG, transcript flywheel, or disconnect metrics report
- Live interruption ordering under real OpenAI audio still unverified (requires approved billable session)

### Result after iteration

- Silence-timer controller retired; deterministic controller owns progression through `PresentationRuntime`.
- Offline test suite green (40).
- Pipecat dependency exactly pinned to 1.11.0.

### Next recommended step

**Iteration 4:** Add frontend (or WS) control messages for pause/resume/goto validated by the backend runtime; optionally begin disconnect metrics logging and stronger underage guardrails—without yet building full RAG/flywheel unless scoped separately.

---

## Iteration 4: Bidirectional lesson control protocol and frontend controls

- Date: 2026-09-25
- Objective: Add versioned JSON lesson control messages over the existing Pipecat Protobuf WebSocket, wire pause/resume/goto/get_state to `PresentationRuntime`, publish authoritative `lesson.state`, and update the vanilla TS frontend with lesson controls. Offline tests only; no live OpenAI calls; no RAG/flywheel/metrics.
- Starting commit: `f21cd26`
- Starting Git status: clean after Iteration 3 checkpoint.

### Before

- Backend owned lesson progression but had no browser control channel.
- `/connect` always returned `ws://localhost:7860/ws`.
- Frontend only had Connect/Disconnect + debug log.

### Baseline before editing

- `uv run pytest -q` → 40 passed
- `yarn tsc --noEmit` / `yarn vite build` → passed

### Exact APIs inspected

- Python 1.11.0: `InputTransportMessageFrame`, `OutputTransportMessageUrgentFrame`, `ProtobufFrameSerializer` (messages enabled), FastAPI WS broadcast of transport messages
- RTVI models: label `rtvi-ai`, types `client-message` / `server-message`
- JS `@pipecat-ai/client-js@1.6.0`: `sendClientMessage(msgType, data)`, `onServerMessage`, `RTVIEvent.ServerMessage`

### Files created

- `curriculum.py`
- `lesson_protocol.py`
- `tests/test_lesson_protocol.py`
- `frontend/lessonProtocol.ts`
- `frontend/lessonProtocol.test.ts`
- `frontend/.env.example`

### Files modified

- `agent.py`, `main.py`, `presentation_runtime.py`
- `frontend/app.ts`, `index.html`, `style.css`, `package.json`, `yarn.lock`, `vite.config.js`, `tsconfig.json`
- `docs/ARCHITECTURE.md`, `docs/PIPECAT_COMPATIBILITY.md`, `docs/IMPLEMENTATION_LOG.md`

### Protocol design

- Version 1 envelopes: `lesson.command`, `lesson.state`, `lesson.command_result`
- Client: `sendClientMessage('lesson.command', envelope)`
- Server: RTVI `server-message` data = lesson envelope
- Dedup: bounded OrderedDict of `request_id` (128)
- Serialization: per-session `asyncio.Lock` on runtime

### Dependency changes

- Frontend: `vitest@3.2.4` (exact) + transitive Vitest deps via Yarn
- No Python dependency changes
- No Pipecat upgrade

### Validation results

- `uv run pytest -q` → **55 passed**
- `yarn test` → **8 passed**
- `yarn tsc --noEmit` → pass (after ESNext/vite client tsconfig)
- `yarn vite build` → pass
- `POST /connect` → 200 `ws://localhost:7860/ws`; with `X-Forwarded-Proto: https` → `wss://example.com/ws`
- No WebSocket `/ws` opened during validation
- No live OpenAI calls
- `.env` not opened/printed/tracked

### Failures and resolutions

- `tsc` rejected `import.meta` under `module: commonjs` → updated frontend `tsconfig` to ESNext + `vite/client` types.
- Invalid goto while answering initially stopped narration before rejection → validate transition before stopping.

### Known limitations

- Logical resume only
- No frontend auth; public `VITE_BOT_API_URL` only
- Live control under real TTS not exercised offline

### Next recommended step

**Iteration 5:** Disconnect metrics report + stronger underage guardrails and/or knowledge ingestion—scoped separately from transcript flywheel unless required together.

---

## Iteration 5: Age-appropriate student safety guardrails

- Date: 2026-09-25
- Objective: Add OpenAI Moderation–backed input/output safety with deterministic ALLOW / REDIRECT / SAFETY_HOLD policy, protocol/UI safety status, and offline tests using fake moderation only. No RAG, transcript persistence, flywheel, production metrics, or live OpenAI calls in validation.
- Starting commit: `ace2f984ee429ffc7b59e0d1acc280e21025e8fc` (`feat: add lesson controls and frontend synchronization`)
- Starting Git status: dirty working tree from Iteration 5 in-progress edits (not committed).

### Before (baseline recorded before Iter5 edits)

- `uv run pytest -q` → **55 passed**
- Frontend: **8** Vitest tests passing; `tsc --noEmit` and `vite build` clean
- Pipecat **1.11.0**; OpenAI SDK already present transitively via `pipecat-ai[openai]`
- No moderation processors; prompt-only tutor guidance

### OpenAI SDK / Moderations API inspection

- Installed SDK: **openai==3.19.2**
- Async client: `openai.AsyncOpenAI`
- Method: `await client.moderations.create(input=..., model=...)`
- Response: `ModerationCreateResponse` with `results[]`; each result has `flagged`, `categories`, `category_scores` (and related fields)
- Category access via attribute / `model_dump()` on `Categories` / `CategoryScores`
- Timeout: application wraps call in `asyncio.wait_for`; maps `TimeoutError` → `ModerationTimeout`; other errors → `ModerationUnavailable`; `CancelledError` re-raised
- Model selection evidence:
  - Official OpenAI Moderations guide / models page: **`omni-moderation-latest`**
  - SDK `ModerationModel` literal includes `omni-moderation-latest`, `omni-moderation-2024-09-26`, legacy text-moderation aliases
- Config (non-secret, `.env.example`): `OPENAI_MODERATION_MODEL`, `MODERATION_TIMEOUT_SECONDS`, `MODERATION_MAX_CHARACTERS` — validated at startup via `load_safety_config()`

### Files created

- `moderation_service.py` — `ModerationResult` / client protocol / `OpenAIModerationClient` / `FakeModerationClient` / config
- `safety_policy.py` — decisions, templates, local distress/danger detector, in-memory `SafetyEvent`
- `safety_processors.py` — `InputSafetyProcessor`, `OutputSafetyProcessor`
- `tests/test_safety.py` — deterministic fake-client coverage
- `docs/SAFETY.md` — threat model, flows, privacy, latency tradeoff, limitations

### Files modified

- `agent.py` — pipeline places input safety after STT and output safety before TTS; loads safety config
- `presentation_runtime.py` — safety status, redirect/hold handlers, SAFETY_* output purposes, strengthened `BASE_TUTOR_PROMPT`
- `lesson_protocol.py` — `safety_status` / `safety_notice`; hold disables pause/resume/navigate flags
- `frontend/lessonProtocol.ts`, `lessonProtocol.test.ts`, `app.ts`, `index.html`, `style.css` — parse/render notice; stale sequence unchanged
- `.env.example` — moderation model/timeout/max chars
- `docs/ARCHITECTURE.md`, `docs/PIPECAT_COMPATIBILITY.md`, `docs/IMPLEMENTATION_LOG.md` (this entry)

### Placement

- **Input:** after final STT `TranscriptionFrame`, before user context aggregator / LLM
- **Output:** after LLM, before TTS; buffers one full `LLMFullResponse*` cycle; trusted `TTSSpeakFrame` templates bypass LLM gating

### Policy decisions

- `ALLOW` — educational / ordinary fear (calm answer)
- `REDIRECT` — graphic/inappropriate (template + logical resume)
- `SAFETY_HOLD` — self-harm, immediate danger, sexual_minors, unknown flag, timeout, unavailable, oversized; no auto-resume; Resume/nav rejected

### Timeout / failure

Fail closed. No truncated approve. Disconnected/ended session ignores late moderation results.

### Latency tradeoff

Full LLM response must complete and be moderated before any of that response reaches TTS (moderation RTT + buffer wait). Documented in `docs/SAFETY.md`.

### Tests added

- Backend: `tests/test_safety.py` (input allow/block/dedupe/partials, redirect resume, hold gates, timeout/exception/oversized, output allow/block/oversized, protocol/state privacy, session isolation, cancel-after-end, control frames not moderated)
- Frontend: safety status parse/reject, hold flags, stale sequence, no raw moderation fields in types

### Validation results (after Iter5)

- `uv run pytest -q` → **82 passed**
- `uv run python -m compileall …` → success
- `uv run python -c "import main, agent, moderation_service, safety_policy; …"` → `backend imports passed`
- `yarn test` → **12 passed**
- `yarn tsc --noEmit` → pass
- `yarn vite build` → pass
- `POST /connect` → `{"ws_url":"ws://127.0.0.1:7860/ws"}` (HTTP 200); **`/ws` not opened**
- `git diff --check` → clean
- `git grep OPENAI_API_KEY` → only expected config/docs/forbidden-key lists / `os.getenv` (no secret values)
- No `print`/`logger`/`logging` of transcripts in `*.py`
- **No live OpenAI Moderation/STT/LLM/TTS calls during automated tests or `/connect` smoke**
- **`.env` was not opened, printed, or copied by the agent**

### Known limitations

- Moderation false positives/negatives; local regex is not a full safety system
- Prompt instructions remain advisory only
- Output buffering adds latency
- Safety events are in-memory only (no persistence yet)
- No teacher override / production metrics / deployment hardening in this iteration

### Next recommended step

Iteration 6 candidates per product plan: RAG / knowledge ingestion, transcript persistence + learning flywheel, and/or production metrics—keeping OpenAI as sole external AI provider.

---

## Iteration 6: Knowledge ingestion and in-memory RAG

- Date: 2026-09-25
- Objective: Secure knowledge upload (txt/md/pdf), OpenAI Embeddings, process-local vector store, post-safety temporary RAG context, frontend upload UI. No transcript persistence, flywheel, production metrics, or live OpenAI in tests.
- Starting commit: `d46574d` (`feat: add student safety and moderation guardrails`)
- Starting Git status: clean working tree on `main` before Iter6 edits.

### Before (baseline)

- `uv run pytest -q` → **82 passed**
- `yarn test` → **12 passed**; `tsc` / `vite build` pass

### OpenAI Embeddings inspection

- SDK: **openai==3.19.2**
- `AsyncOpenAI.embeddings.create(input=str|list, model=...)`
- Response: `CreateEmbeddingResponse.data[]` with `embedding`, `index`; ordered by index
- Model: **`text-embedding-3-small`** (official Embeddings guide + SDK `EmbeddingModel`)
- Timeouts via `asyncio.wait_for`; errors → `EmbeddingTimeout` / `EmbeddingUnavailable` / `EmbeddingDimensionError`

### Dependencies added

- `pypdf==5.4.0`
- `python-multipart==0.0.20`
- `uv.lock` updated

### Files created

- `embedding_service.py`, `knowledge_ingestion.py`, `knowledge_store.py`, `knowledge_api.py`, `retrieval_processor.py`
- `tests/test_knowledge.py`
- `frontend/knowledgeProtocol.ts`, `frontend/knowledgeProtocol.test.ts`
- `docs/KNOWLEDGE_RAG.md`

### Files modified

- `main.py`, `agent.py`, `presentation_runtime.py`, `pyproject.toml`, `uv.lock`, `.env.example`, `README.md`
- `frontend/app.ts`, `index.html`, `style.css`
- `docs/ARCHITECTURE.md`, `docs/SAFETY.md`, `docs/PIPECAT_COMPATIBILITY.md`, `docs/IMPLEMENTATION_LOG.md`

### Design notes

- Pipeline: `stt → input_safety → retrieval → user agg → llm → output_safety → tts`
- Temporary context: `LLMMessagesTransformFrame` strip + `LLMMessagesAppendFrame` inject
- Atomic ingest; SHA-256 content dedupe; moderate-before-embed
- HTTP: `POST /knowledge/documents`, `GET /knowledge/status`

### Validation results

- `uv run pytest -q` → **98 passed**
- compileall + import check → pass
- `yarn test` → **20 passed**
- `yarn tsc --noEmit` / `yarn vite build` → pass
- HTTP fakes: status 200, unsupported 400, oversized 413, successful fake upload 200, `/connect` 200; **no `/ws`**; **no live OpenAI**
- `.env` not opened/printed/tracked

### Known limitations

- In-memory / single-process only; no upload auth; prompt-injection residual risk; embedding+search latency per question

### Next recommended step

**Iteration 7:** transcript persistence and/or learning flywheel and/or production metrics (scoped separately).

---

## Iteration 7: Session metrics, consent, and local SQLite persistence

- Date: 2026-09-25
- Objective: Per-session metrics, disconnect report, consent-controlled redacted transcript persistence in local SQLite, sanitized export CLI for Iteration 8 flywheel input. No flywheel/LLM-judge/deployment.
- Starting commit: `5539214`
- Baseline before edit: pytest **98** passed; frontend **20** passed

### Pipecat metrics inspected (1.11.0)

- `MetricsFrame` + `TTFBMetricsData`, `ProcessingMetricsData`, `LLMUsageMetricsData`/`LLMTokenUsage`, `TTSUsageMetricsData`, `STTUsageMetricsData`/`STTUsage`
- Enabled via `PipelineParams(enable_metrics=True, enable_usage_metrics=True)`

### Files created

- `session_config.py`, `session_metrics.py`, `session_store.py`, `session_observability.py`, `transcript_redaction.py`, `session_export.py`
- `tests/test_session_data.py`
- `frontend/sessionProtocol.ts`, `frontend/sessionProtocol.test.ts`
- `docs/SESSION_DATA.md`

### Files modified

- `agent.py`, `lesson_protocol.py`, `presentation_runtime.py`, `safety_processors.py`, `retrieval_processor.py`
- frontend `app.ts` / `index.html` / `style.css`
- `.env.example`, `.gitignore`, README + architecture/safety/Pipecat docs

### Validation

- `uv run pytest -q` → **109 passed**
- compileall + imports → pass
- `uv run python -m session_export --help` → pass
- `yarn test` → **24 passed**
- `yarn tsc --noEmit` / `yarn vite build` → pass
- HTTP: `POST /connect` 200; `GET /knowledge/status` 200; **no `/ws`**; **no live OpenAI**
- `.env`, `data/`, sqlite, export patterns ignored; `.env` not opened/printed

### Known limitations

- SQLite is local/demo-grade; redaction incomplete by nature; playback completion not exact without Pipecat evidence; flywheel analysis deferred to Iteration 8

### Next recommended step

**Iteration 8:** transcript-analysis learning flywheel using consented exported data (review/promotion required; no auto-deploy of prompt changes).

## Iteration 8 — Controlled learning flywheel + LLM-as-a-judge (2026-09-25)

### Goal

Local friction analysis on consented redacted history, reviewable curriculum recommendations, versioned prompt registry, synthetic eval suite, offline promotion gates. No auto-deploy; no historical transcripts to OpenAI; no live OpenAI in tests.

### Baseline (before edits)

- `uv run pytest -q` → **109 passed**
- `yarn test` → **24 passed**; `tsc` / `vite build` → pass
- HEAD `8684d15`

### Files created

- `prompts/registry.json`, `prompts/tutor/v1.md`, `prompts/judges/*.md`
- `prompt_registry.py`, `friction_analyzer.py`, `curriculum_recommendations.py`, `flywheel.py`
- `prompt_workflow.py`, `eval_llm.py`, `eval_harness.py`
- `evals/cases/synthetic_suite_v1.json`, `evals/rubrics/pedagogical_v1.json`, `evals/fixtures/offline_scenarios.json`
- `tests/test_flywheel.py`
- `docs/LEARNING_FLYWHEEL.md`, `docs/EVALUATIONS.md`

### Files modified

- `presentation_runtime.py`, `agent.py`, `session_store.py`, `session_config.py`, `session_observability.py`
- `.env.example`, `.gitignore`, `README.md`, `docs/ARCHITECTURE.md`, `docs/SESSION_DATA.md`, `docs/SAFETY.md`

### Design notes

- Active tutor prompt text preserved byte-for-byte vs prior `BASE_TUTOR_PROMPT`
- SQLite `user_version` 1→2 transactional migration; future versions rejected
- Promotion outcomes: `REJECT` | `NEEDS_MORE_EVIDENCE` | `ELIGIBLE_FOR_HUMAN_REVIEW` only
- Live OpenAI gated by env + CLI flags; default path never initializes clients for flywheel/eval

### Validation

- `uv run pytest -q` → **152 passed**
- compileall + imports → pass
- `uv run python -m flywheel --help` / `prompt_workflow --help` → pass
- `uv run python -m eval_harness validate` → 21 cases OK
- `uv run python -m eval_harness run-offline-fixtures` → 13 fixtures OK
- `yarn test` → **24 passed**; `tsc` / `vite build` → pass
- No live OpenAI calls; no generated prompt activated; `.env` not opened

### Known limitations

- Lexical Jaccard miss on paraphrases; latency≠causation; judge bias; same-model eval limits; live path intentionally unused in CI

### Next recommended step

**Iteration 9:** operator-facing review UX / durable multi-process session store / authenticated export — without auto-deploy of prompts.

---

## Iteration 9: Segment-level narration runtime

- Date: 2026-09-25
- Objective: Replace whole-slide playback completion with deterministic, generation-scoped narration segments and segment-level resume. Preserve runtime/controller, safety, frame factory, and observability APIs. No OpenAI calls.
- Starting commit: `655df62`
- Starting Git status: `narration_plan.py` was already untracked; no tracked modifications were present.

### Before

- `PresentationRuntime` treated each approved LLM response as one TTS utterance and advanced the slide after one audible bot-stop event.
- Resume requested a new LLM continuation because playback offsets and segment checkpoints were unavailable.
- `narration_plan.py` already provided deterministic segmentation and generation/progress structures.

### Changes made

- Rewrote `presentation_runtime.py` to own narration plans, generation IDs, active segment state, safe narration errors, test-visible metrics, and one-based public progress.
- Approved narration now becomes deterministic `TTSSpeakFrame` segments; only the final completed segment dispatches `SLIDE_COMPLETED`.
- Pause/interruption preserves the plan and replays an interrupted segment; a pause between segments resumes at the next pending segment.
- Navigation and session end invalidate plans. Stale/cancelled completions cannot advance a slide.
- Retained safety redirect/hold behavior, controller dispatch, state callbacks, observability hooks, and public test frame helpers.
- Added `RecordingFrameSink.tts_texts`.

### Commands executed

- Required import check: `uv run python -c "from presentation_runtime import PresentationRuntime, BASE_TUTOR_PROMPT; print(len(BASE_TUTOR_PROMPT))"`
- Compile check: `uv run python -m py_compile presentation_runtime.py narration_plan.py`
- Inline offline segment smoke covering plan creation, two segment completions, pause, replay, metrics, and slide advancement
- `uv run pytest -q tests/test_presentation_runtime.py`

### Dependency changes

- None.

### Validation results

- Required import check passed and printed `842`.
- Compile check passed.
- IDE lint diagnostics: no errors.
- Segment runtime smoke passed.
- Existing presentation runtime suite: **12 passed, 8 failed**. The failures are legacy tests that synthesize bot start/stop without first calling `accept_approved_narration`; under the new contract no narration plan exists, so a bot stop correctly does not complete a slide.

### Problems or uncertainties

- The output safety/pipeline integration and existing tests must route approved slide narration through `should_segment_approved_output()` and `accept_approved_narration()` for end-to-end segment playback. This iteration changed only `presentation_runtime.py` as scoped.
- Exact playback offsets remain unavailable; resume accuracy is segment-level only.

### Result after iteration

- The runtime implements generation-safe segment queueing, completion, interruption, replay, invalidation, progress, errors, and metrics without external calls.
- No commit or push was created.

### Next recommended step

- Wire approved output into `accept_approved_narration`, pass `load_narration_max_characters()` from agent construction, and update runtime tests to create approved plans before playback lifecycle events.

### Iteration 9 completion note

The earlier entry captured an intermediate runtime-only handoff. The remaining
pipeline integration was already present at the start of this completion pass;
this note records the completed offline test/documentation scope and final
validation.

#### Files added

- `tests/test_narration.py`
- `scripts/live_test_preflight.py`
- `docs/RESUME_ACCURACY.md`
- `docs/LIVE_TEST_PLAN.md`

#### Files updated in this completion pass

- `frontend/lessonProtocol.test.ts`, `frontend/lessonProtocol.ts`
- `docs/ARCHITECTURE.md`, `docs/PIPECAT_COMPATIBILITY.md`
- `docs/SESSION_DATA.md`, `docs/SAFETY.md`, `README.md`

#### Offline coverage

- Deterministic segmentation, configuration validation, plan defaults/progress
- Segment progression and final-segment-only slide/Q&A completion
- Pause/resume and barge-in replay at segment granularity
- Navigation generation invalidation and cancelled completion suppression
- Content-free narration metrics
- `/health/live` and `/health/ready` through `TestClient`, with a temporary
  SQLite path and network methods set to fail if called
- Frontend narration parsing, malformed/text-leak rejection, stale sequence
  rejection, one-based segment wording, and type-level absence of text fields

#### API evidence and limitation

- Installed Pipecat `BotStoppedSpeakingFrame` has no playback-offset field.
- `OutputAudioRawFrame` carries produced audio bytes but no client-played acknowledgement.
- Installed client-js `botStoppedSpeaking` and `trackStarted` callbacks have no offset.
- FastAPI WebSocket + Protobuf has no played-offset acknowledgement in this protocol.
- The truthful guarantee is deterministic `ResumeAccuracy.SEGMENT`.

#### Validation results

- `uv run pytest tests/test_narration.py tests/test_presentation_runtime.py -q`
  → **32 passed**, 2 dependency deprecation warnings.
- With the repository-pinned Node 22 toolchain:
  `cd frontend && yarn test && yarn tsc --noEmit`
  → **29 frontend tests passed** and TypeScript passed.
- `cd frontend && yarn vite build` with Node 22 → passed (72 modules transformed).
- `uv run python scripts/live_test_preflight.py`
  → passed Python 3.11.14, Node 22, Yarn 1.22.22, package hints, `.env`
  existence-only check, ports, backend imports, and approved prompt registry.
  It printed `No OpenAI request was made`.
- IDE diagnostics for edited Python/TypeScript files: no errors.

The first frontend attempt used the ambient Node 24 shell where `yarn` was not
on `PATH`; validation was rerun successfully after loading the repository's
Node 22 toolchain. The first preflight also exposed its missing repository
import path and an orphaned prior `/connect` smoke-test uvicorn process on
port 7860; the import path was fixed, that stale process was stopped, and the
preflight then passed with both required ports available.

#### Live status

**LIVE OPENAI VALIDATION NOT RUN.** No OpenAI request was made, `/ws` was not
opened, `.env` contents were not read or printed, and no production
`data/tutor_sessions.sqlite3` test access was used.

### Iteration 9 final offline validation

- Full `uv run pytest -q` → **171 passed**.
- Frontend yarn test/tsc/vite pass.
- Preflight: No OpenAI request was made.
- Health live/ready/connect/knowledge status: 200 without `/ws`.
- **LIVE OPENAI VALIDATION NOT RUN**
