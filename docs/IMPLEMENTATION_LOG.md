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
