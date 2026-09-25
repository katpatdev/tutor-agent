# AGENTS.md

Instructions for humans and coding agents working in this repository.

## Project mission

This project is a Pipecat and OpenAI voice tutor. It presents a natural-disaster lesson to students, accepts interruptions, resumes narration reliably, enters Q&A after the final slide, and must remain safe for underage users.

## Non-negotiable constraints

- Use **Pipecat only** for the voice pipeline. Do not introduce LangChain, LlamaIndex, Graphify, OmniRoute, or other agent frameworks.
- Use **OpenAI only** for external AI services (STT, LLM, TTS, embeddings if needed). No other model providers or hosted vector databases.
- Never place `OPENAI_API_KEY` or other secrets in frontend code, committed files, logs, or docs.
- Never commit `.env`, API keys, access tokens, or credentials.
- Python must be exactly **3.11.14** (`uv` + `.python-version`).
- Node must be **22** (see root `.nvmrc`).
- Yarn must be Classic **1.22.22** (`packageManager` in `frontend/package.json`). Do not migrate to Yarn Berry/Yarn 4.
- Preserve backend/frontend protocol compatibility (WebSocket + Protobuf frame serialization unless a deliberate, tested protocol change is approved).

## Evidence and assumption rules

- Read relevant source files before proposing changes.
- Never claim a requirement is implemented without identifying the code and test that prove it.
- Clearly distinguish **observed facts**, **inferences**, and **proposals**.
- Do not invent APIs, Pipecat classes, events, or assignment requirements.
- Verify library APIs against the **installed** package version (see `uv.lock` / `frontend/yarn.lock`) before using them.
- When uncertain, stop and report the uncertainty instead of guessing.

## Architecture rules

- Deterministic application code must own lesson state (slide index, pause, Q&A mode, narration position).
- The LLM must **not** be the source of truth for slide index, pause state, Q&A state, or narration position.
- Frontend commands must be validated by the backend.
- Pause/resume and slide navigation must use deterministic control messages.
- Student safety cannot depend only on a prompt; enforce guardrails in application logic where possible.
- Transcript-derived improvements must never deploy automatically; any flywheel requires explicit review/promotion.

## Change discipline

- Inspect `git status` before editing.
- Do not overwrite unrelated changes.
- Do not edit generated dependency directories (`.venv`, `frontend/node_modules`, build outputs).
- Do not add dependencies without explaining necessity and assignment compliance.
- Prefer the smallest complete implementation, but never remove validation, safety, tests, or observability to reduce code size.
- Do not create a Git commit or push without explicit instruction.

## Verification requirements

- Add deterministic tests for business logic.
- Run relevant Python tests before claiming backend changes are done.
- Run frontend type checking (`yarn tsc --noEmit`) and build verification (`yarn vite build` or `yarn build`) for frontend changes.
- Report commands executed and their results.
- Never hide or silently work around a failed check.

## Iteration logging

Every implementation iteration must **append** an entry to `docs/IMPLEMENTATION_LOG.md`. Do not rewrite prior entries to hide failures or decisions.
