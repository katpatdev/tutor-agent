# Project structure

Professional `src/` layout for the Pipecat / OpenAI voice tutor.

## Top-level directories

| Path | Purpose | Source-controlled? |
|------|---------|--------------------|
| `src/tutor_agent/` | Installable Python application package | Yes |
| `tests/` | Offline pytest suite (imports the installed package) | Yes |
| `frontend/` | Vite + TypeScript classroom UI | Yes (`node_modules/` / `dist/` ignored) |
| `prompts/` | Approved prompt registry and tutor/judge prompt files | Yes |
| `evals/` | Offline evaluation fixtures and schemas | Yes |
| `scripts/` | Operational helpers (preflight, config summary, submission scan) | Yes |
| `docs/` | Architecture, setup, safety, live-test plans, implementation log | Yes |
| `data/` | Local runtime outputs (SQLite, flywheel, candidates, live-test reports) | **No** (gitignored) |
| `main.py` | Thin compatibility launcher only | Yes |
| `.venv/` | Local Python environment from `uv sync` | No |

## Backend packages (`src/tutor_agent/`)

| Package | Responsibility |
|---------|----------------|
| `tutor_agent` (root) | FastAPI entry (`main.py`), Pipecat pipeline wiring (`agent.py`), repo path helpers (`paths.py`) |
| `lesson/` | Curriculum, lesson state machine, protocol, classroom controls, conversation ledger, voice navigation |
| `narration/` | Segment plans, prefetch cache, speech chunking |
| `audio/` | Presentation runtime, TTS delivery/recovery, speech units, voice runtime config |
| `safety/` | Moderation client, policy, Pipecat processors, transcript redaction |
| `knowledge/` | Upload API, ingestion, in-memory store, embeddings, retrieval processor |
| `observability/` | Session config/metrics/store/export and observability facade |
| `evaluation/` | Eval harness, flywheel, friction analysis, prompt registry/workflow |

`__init__.py` files are empty markers. Prefer absolute imports such as
`from tutor_agent.lesson.lesson_controller import LessonController`.

## Application entry points

| Command | What it starts |
|---------|----------------|
| `uv run python main.py` | Compatibility launcher → `tutor_agent.main.run` |
| `uv run python -m tutor_agent.main` | Packaged module entry |
| `uv run tutor-agent` | Console script (same as module entry) |

Operational CLIs (also available as console scripts):

| Command | Module equivalent |
|---------|-------------------|
| `uv run tutor-session-export` | `python -m tutor_agent.observability.session_export` |
| `uv run tutor-eval` | `python -m tutor_agent.evaluation.eval_harness` |
| `uv run tutor-flywheel` | `python -m tutor_agent.evaluation.flywheel` |
| `uv run tutor-prompt-workflow` | `python -m tutor_agent.evaluation.prompt_workflow` |

## Prompts and evaluation fixtures

- Prompts: repository `prompts/` (resolved via `tutor_agent.paths.prompts_dir()`).
- Eval fixtures: repository `evals/` (resolved via `tutor_agent.paths.evals_dir()`).
- Relocating Python modules does **not** relocate these directories; they stay at the repo root.

## Local runtime data (generated / ignored)

Written under `data/` when configured (defaults are CWD-relative from the repo root):

- `data/tutor_sessions.sqlite3` — optional session metrics / consented transcripts
- `data/flywheel/` — friction reports
- `data/prompt_candidates/` — review-only prompt candidates
- `data/eval_reports/` — evaluation outputs
- `data/live_tests/` — manual live-test reports

Also ignored: `__pycache__/`, `.pytest_cache/`, `.venv/`, `frontend/node_modules/`, `frontend/dist/`, `.env`, exported JSONL transcripts.

## Packaging notes

- `pyproject.toml` uses a `src/` layout (`tool.setuptools.packages.find.where = ["src"]`).
- `uv sync` installs the editable `tutor-agent` package so imports work without `PYTHONPATH` or `sys.path` edits.
- Tests import `tutor_agent.*` only; they do not rely on flat root modules.
