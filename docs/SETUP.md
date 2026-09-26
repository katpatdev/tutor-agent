# Local setup (WSL / Linux)

Reproducible environment for the tutor-agent project.

## Required versions

| Tool | Version |
|------|---------|
| Python | `3.11.14` (exact) |
| uv | any recent user install (tested with `0.12.x`) |
| Node.js | `22` (see root `.nvmrc`) |
| Yarn | Classic `1.22.22` |
| Framework | Pipecat only |
| External AI | OpenAI only |

## Install uv (user install, no sudo)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

Add `$HOME/.local/bin` to your shell profile if it is not already there.

## Install Python 3.11.14 and sync backend deps

From the repository root:

```bash
uv python install 3.11.14
uv sync --python 3.11.14
uv run python --version   # must print: Python 3.11.14
```

This creates `.venv` and uses `uv.lock` for reproducible installs.

## Install NVM (if missing)

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash
```

Reload your shell, then:

```bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
```

## Install Node 22

```bash
cd /path/to/tutor-agent
nvm install
nvm use
# or explicitly:
nvm install 22
nvm use 22
nvm alias default 22
node --version   # v22.x.x
npm --version
```

## Install Yarn Classic 1.22.22

```bash
npm install --global yarn@1.22.22
yarn --version   # must print: 1.22.22
```

Do not migrate to Yarn Berry / Yarn 4. The lockfile is Yarn v1.

## Install frontend dependencies

```bash
cd frontend
yarn install --frozen-lockfile
```

Prefer `--frozen-lockfile` so `yarn.lock` does not change unexpectedly.

## Create `.env`

```bash
cd /path/to/tutor-agent
cp .env.example .env
```

Edit `.env` and set your real OpenAI API key as the value of `OPENAI_API_KEY` (leave no spaces around `=`).

- Never commit `.env`.
- Never put the key in frontend code.
- **ChatGPT Plus does not replace OpenAI API access.** You need a billed OpenAI API key with access to the models used by this project. Plus/subscription chat access is separate from API billing.

## Start the backend

```bash
cd /path/to/tutor-agent
uv run python main.py
# equivalent packaged entry points:
# uv run python -m tutor_agent.main
# uv run tutor-agent
```

Server listens on `0.0.0.0:7860`.

- `POST /connect` returns `{"ws_url": "ws://localhost:7860/ws"}`.
- WebSocket endpoint: `/ws`.

Application code lives under `src/tutor_agent/` (see `docs/PROJECT_STRUCTURE.md`). The root `main.py` is a compatibility launcher only.

## Start the frontend

In a second terminal:

```bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm use
cd /path/to/tutor-agent/frontend
yarn dev
```

Open the URL printed by Vite (typically `http://localhost:5173`).

## Verification commands (non-paid)

Backend imports (no OpenAI calls):

```bash
cd /path/to/tutor-agent
uv run python -c "import pipecat, fastapi, uvicorn; print('Core backend imports OK')"
uv run python -c "import tutor_agent.agent, tutor_agent.main; print('Application imports OK')"
```

Frontend typecheck and build:

```bash
cd /path/to/tutor-agent/frontend
yarn tsc --noEmit
yarn vite build
```

## Common WSL notes

- Prefer NVM for Node and `uv` for Python; avoid `sudo apt` / `sudo snap` for these tools unless you have no alternative and approve system installs.
- Ensure `uv` is on `PATH` (`~/.local/bin`).
- Ensure NVM is sourced in interactive shells so `node` / `yarn` resolve to the Node 22 toolchain.
- Microphone access from WSL may require extra host/WSL audio configuration; that is outside dependency setup.
- Backend CORS allows all origins in the starter; keep that in mind for local networking.

## Billing warning

Live STT, LLM, and TTS calls consume OpenAI API credits. Do not run end-to-end voice tests until `.env` contains a valid key and you intentionally approve billable usage. ChatGPT Plus alone is not sufficient for those API calls.
