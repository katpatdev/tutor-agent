# Evaluations and LLM-as-a-judge (Iteration 8)

## Synthetic suite

Version-controlled cases live under `evals/cases/` (synthetic student questions only). No historical transcript text.

Categories include factual accuracy, age-appropriate explanation, frightened student, topic recovery, confusion, simpler wording, interruption, Q&A, uncertainty, RAG miss/hit, malicious retrieved docs, fabricated sources, graphic requests, self-harm handling, speech-based state control, prompt extraction, navigation, concision, comprehension checks, and accelerated learners.

State transitions (pause/resume/navigation) remain owned by application code; judges do not authorize control changes.

## Rubric

`evals/rubrics/pedagogical_v1.json` — scale 1–5 with anchors for:

factual correctness, pedagogical clarity, age appropriateness, safety, topic recovery, active-learning quality, concision/voice suitability, uncertainty handling, RAG grounding, source fidelity.

Judge outputs must be structured; malformed output cannot pass.

## Pairwise harness

```bash
uv run python -m eval_harness validate
uv run python -m eval_harness run-offline-fixtures
# Live (gated; not used in automated tests):
uv run python -m eval_harness run-live --candidate data/prompt_candidates/... --live-openai --confirm-cost
```

Baseline vs candidate answers are labeled Answer A / Answer B with deterministic per-case order randomization. Repeated judging is configurable via `EVAL_REPETITIONS`.

## Promotion gates

| Decision | Meaning |
|----------|---------|
| `REJECT` | Invariant failure, critical safety regression, critical violation, dimension regression on safety/age/grounding/topic/source, or invalid judge output |
| `NEEDS_MORE_EVIDENCE` | Coverage below minimum |
| `ELIGIBLE_FOR_HUMAN_REVIEW` | Passed deterministic gates only |
| `AUTO_DEPLOY` | **Never produced or executed** |

Higher average score cannot override a critical safety failure.

## Offline fixtures

`evals/fixtures/offline_scenarios.json` covers baseline/candidate wins, ties, malformed judge output, timeouts, safety/source/topic regressions, missing dimensions, disagreement, and insufficient coverage. Tests use fake LLM/judge clients only.

## Live-call safeguards

Requires all of: `ALLOW_LIVE_FLYWHEEL_OPENAI=true`, `--live-openai`, `--confirm-cost`, configured model. Usage tokens may be recorded; monetary cost is not calculated. Transcript text must never appear in request builders.

## Known limitations

- Judge bias and nondeterminism
- Same-model evaluation limitations
- Synthetic cases may not match real classroom distributions
- Live path is intentionally hard to enable
