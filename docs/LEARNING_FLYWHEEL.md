# Learning flywheel (Iteration 8)

## Purpose

Identify lesson friction from **historical local sessions**, produce **reviewable** improvement recommendations, and support a **human-gated** prompt candidate workflow. Nothing auto-deploys.

## Privacy boundary

| Allowed locally | Never sent to OpenAI |
|-----------------|----------------------|
| Content-free metrics, safety/RAG events | Historical transcript text (raw or redacted) |
| Consented **already-redacted** transcripts for local lexical analysis | SQLite rows as bulk uploads |
| Content-free friction reports | Local redacted example appendices |

Student consent covers **local redacted storage**, not external transcript analysis. Live OpenAI flywheel/eval calls are disabled by default (`ALLOW_LIVE_FLYWHEEL_OPENAI=false`) and require `--live-openai` + `--confirm-cost`.

## Prompt registry

- Files under `prompts/` with `registry.json`
- Active tutor prompt: `TUTOR_PROMPT_VERSION` (default `v2`)
- Runtime loads only **registered + approved** prompts; hash mismatch / missing file / path traversal fail startup
- `data/prompt_candidates/` is ignored by runtime
- Session metadata records `tutor_prompt_version` and `tutor_prompt_hash`

## Local friction analysis

```bash
uv run python -m tutor_agent.evaluation.flywheel analyze
uv run python -m tutor_agent.evaluation.flywheel analyze --output data/flywheel/friction-report.json
uv run python -m tutor_agent.evaluation.flywheel analyze --include-local-redacted-examples --output data/flywheel/friction-report.json
# equivalent: uv run tutor-flywheel analyze ...
```

Signals (deterministic): repeated similar questions (Jaccard on normalized tokens), confusion cues, interruptions, backward navigation heuristics, pauses, early dropout, failed Q&A entry, RAG miss rate, safety redirects, high latency (engineering), long monologues, missing comprehension checks, simpler-wording requests.

**Limitation:** lexical Jaccard similarity can miss semantically equivalent questions; no embeddings on historical transcripts.

Primary reports are content-free (no `redacted_text`). Optional local appendices are gitignored, permission-restricted, never printed, never included in OpenAI requests.

## Recommendations

`curriculum_recommendations.py` maps signals → reviewable actions. Safety-related items require human review. Never recommend weakening moderation. High latency is classified as engineering, not curriculum. RAG misses recommend improving documents, not auto prompt edits.

## Candidate workflow

```bash
uv run python -m tutor_agent.evaluation.prompt_workflow generate-candidate --instructions "..."
uv run python -m tutor_agent.evaluation.prompt_workflow check-invariants prompts/tutor/v1.md
# equivalent: uv run tutor-prompt-workflow ...
```

Candidates write under `data/prompt_candidates/` only. They are **not** added to `registry.json` automatically.

## Human promotion (manual)

1. Review candidate diff vs parent.
2. Confirm safety invariants.
3. Review eval report / failures.
4. Copy approved text into `prompts/tutor/`.
5. Update `prompts/registry.json` (hash, approved).
6. Set `TUTOR_PROMPT_VERSION`.
7. Re-run full tests.
8. Separate human commit.

Rollback: set `TUTOR_PROMPT_VERSION` back to the prior approved version and restart; keep prior files in the registry.

## Why deployment is never automatic

An LLM judge is fallible, biased, and nondeterministic. Same-model evaluation can favor stylistically similar text. Deterministic gates can only recommend `ELIGIBLE_FOR_HUMAN_REVIEW` — never `AUTO_DEPLOY`.
