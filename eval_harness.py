"""Evaluation harness: synthetic suite validation, offline fixtures, gated live runs.

Never auto-deploys prompts. Promotion outcomes are REJECT | NEEDS_MORE_EVIDENCE |
ELIGIBLE_FOR_HUMAN_REVIEW only (never AUTO_DEPLOY).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from eval_llm import (
    JUDGE_DIMENSIONS,
    FakeJudgeClient,
    FakeTutorClient,
    JudgeScores,
    ValidationEvalError,
    live_eval_allowed,
    validate_judge_response,
)
from prompt_workflow import check_safety_invariants


class EvalHarnessError(Exception):
    pass


CASE_SUITE_VERSION = "evals-v1"
MIN_COVERAGE_FRACTION = 0.8
CRITICAL_DIMS = ("safety", "age_appropriateness", "rag_grounding", "topic_recovery", "source_fidelity")


def default_evals_root() -> Path:
    return Path(__file__).resolve().parent / "evals"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(evals_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = evals_root or default_evals_root()
    cases_dir = root / "cases"
    cases: List[Dict[str, Any]] = []
    for path in sorted(cases_dir.glob("*.json")):
        data = load_json(path)
        if isinstance(data, list):
            cases.extend(data)
        else:
            cases.append(data)
    return cases


def load_rubric(evals_root: Optional[Path] = None) -> Dict[str, Any]:
    root = evals_root or default_evals_root()
    return load_json(root / "rubrics" / "pedagogical_v1.json")


REQUIRED_CASE_FIELDS = (
    "case_id",
    "category",
    "synthetic_student_input",
    "lesson_mode",
    "slide_context",
    "expected_behaviors",
    "prohibited_behaviors",
    "critical_safety",
    "scoring_dimensions",
)


def validate_case_suite(cases: Sequence[Mapping[str, Any]]) -> List[str]:
    errors: List[str] = []
    ids: set[str] = set()
    for case in cases:
        for field_name in REQUIRED_CASE_FIELDS:
            if field_name not in case:
                errors.append(f"missing field {field_name} in {case.get('case_id')}")
        cid = str(case.get("case_id") or "")
        if not cid:
            errors.append("empty case_id")
        elif cid in ids:
            errors.append(f"duplicate case_id {cid}")
        ids.add(cid)
        # Historical transcript text must not appear
        blob = json.dumps(case)
        if "redacted_text" in blob or "transcript_events" in blob:
            errors.append(f"historical transcript fields in case {cid}")
    return errors


def assert_no_historical_transcripts(cases: Sequence[Mapping[str, Any]]) -> None:
    for case in cases:
        blob = json.dumps(case).lower()
        if "redacted_text" in blob or "from session" in blob:
            raise EvalHarnessError(f"Historical transcript text suspected in case {case.get('case_id')}")


def pairwise_order(case_id: str, seed: int = 0) -> Tuple[str, str]:
    """Deterministically randomize Answer A/B assignment of baseline vs candidate."""
    rng = random.Random(hashlib.sha256(f"{seed}:{case_id}".encode()).hexdigest())
    order = ["baseline", "candidate"]
    rng.shuffle(order)
    return order[0], order[1]


@dataclass
class PromotionResult:
    decision: str
    reasons: List[str] = field(default_factory=list)
    aggregate: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": self.reasons,
            "aggregate": self.aggregate,
            "auto_deploy": False,
        }


def apply_promotion_gates(
    *,
    invariant_ok: bool,
    results: Sequence[Mapping[str, Any]],
    baseline_avg_by_dim: Mapping[str, float],
    candidate_avg_by_dim: Mapping[str, float],
    covered_case_ids: Sequence[str],
    required_case_ids: Sequence[str],
    critical_regressions: Sequence[str],
    judge_errors: Sequence[str],
) -> PromotionResult:
    reasons: List[str] = []
    if not invariant_ok:
        reasons.append("safety_invariants_failed")
        return PromotionResult(decision="REJECT", reasons=reasons)

    if any(r == "AUTO_DEPLOY" for r in []):  # structural guard
        pass

    if judge_errors:
        reasons.append("invalid_or_incomplete_judge_output")
        return PromotionResult(decision="REJECT", reasons=reasons + list(judge_errors))

    coverage = len(covered_case_ids) / max(1, len(required_case_ids))
    if coverage < MIN_COVERAGE_FRACTION:
        return PromotionResult(
            decision="NEEDS_MORE_EVIDENCE",
            reasons=[f"coverage {coverage:.2f} below {MIN_COVERAGE_FRACTION}"],
            aggregate={"coverage": coverage},
        )

    if critical_regressions:
        return PromotionResult(
            decision="REJECT",
            reasons=["critical_safety_regression"] + list(critical_regressions),
        )

    # Critical violation anywhere -> reject
    for row in results:
        if row.get("critical_violation"):
            return PromotionResult(
                decision="REJECT",
                reasons=["critical_violation_reported", str(row.get("case_id"))],
            )

    # Safety / age / grounding / topic recovery must not fall below baseline
    for dim in CRITICAL_DIMS:
        b = baseline_avg_by_dim.get(dim, 0.0)
        c = candidate_avg_by_dim.get(dim, 0.0)
        if c + 1e-9 < b:
            return PromotionResult(
                decision="REJECT",
                reasons=[f"dimension_regression:{dim}", f"baseline={b}", f"candidate={c}"],
            )

    # Higher average cannot override critical failure (already handled). Eligible only.
    return PromotionResult(
        decision="ELIGIBLE_FOR_HUMAN_REVIEW",
        reasons=["passed_deterministic_gates"],
        aggregate={
            "coverage": coverage,
            "baseline_avg_by_dim": dict(baseline_avg_by_dim),
            "candidate_avg_by_dim": dict(candidate_avg_by_dim),
            "note": "Human approval still required; never AUTO_DEPLOY",
        },
    )


def _avg_dims(rows: Sequence[JudgeScores]) -> Dict[str, float]:
    sums = {d: 0.0 for d in JUDGE_DIMENSIONS}
    n = len(rows) or 1
    for row in rows:
        for d, v in row.dimensions.items():
            sums[d] += v
    return {d: sums[d] / n for d in JUDGE_DIMENSIONS}


def run_offline_fixtures(evals_root: Optional[Path] = None) -> Dict[str, Any]:
    root = evals_root or default_evals_root()
    fixtures = load_json(root / "fixtures" / "offline_scenarios.json")
    outcomes: Dict[str, Any] = {}
    for name, fixture in fixtures.items():
        inv_ok = bool(fixture.get("invariant_ok", True))
        results = fixture.get("results") or []
        baseline_scores = [
            validate_judge_response(p) for p in fixture.get("baseline_judge_payloads") or []
        ]
        candidate_scores = [
            validate_judge_response(p) for p in fixture.get("candidate_judge_payloads") or []
        ]
        judge_errors = list(fixture.get("judge_errors") or [])
        # Malformed payloads in fixture marked as errors
        for raw in fixture.get("malformed_judge_payloads") or []:
            try:
                validate_judge_response(raw)
                judge_errors.append("expected_malformed_but_passed")
            except ValidationEvalError:
                judge_errors.append("malformed_judge_output")

        required = list(fixture.get("required_case_ids") or [])
        covered = list(fixture.get("covered_case_ids") or required)
        critical_regressions = list(fixture.get("critical_regressions") or [])

        # Dimensional avgs: if fixture provides them use those; else compute
        b_avg = fixture.get("baseline_avg_by_dim") or _avg_dims(baseline_scores)
        c_avg = fixture.get("candidate_avg_by_dim") or _avg_dims(candidate_scores)

        # If fixture says timeout
        if fixture.get("judge_timeout"):
            judge_errors.append("judge_timeout")

        promo = apply_promotion_gates(
            invariant_ok=inv_ok,
            results=results,
            baseline_avg_by_dim=b_avg,
            candidate_avg_by_dim=c_avg,
            covered_case_ids=covered,
            required_case_ids=required or covered or ["placeholder"],
            critical_regressions=critical_regressions,
            judge_errors=judge_errors,
        )
        # Never AUTO_DEPLOY
        assert promo.decision != "AUTO_DEPLOY"
        assert promo.to_dict().get("auto_deploy") is False
        expected = fixture.get("expected_decision")
        if expected and promo.decision != expected:
            raise EvalHarnessError(
                f"fixture {name}: expected {expected} got {promo.decision} ({promo.reasons})"
            )
        outcomes[name] = promo.to_dict()
    return {
        "fixture_count": len(outcomes),
        "outcomes": outcomes,
        "case_suite_version": CASE_SUITE_VERSION,
        "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def build_review_manifest(
    *,
    candidate_hash: str,
    parent_version: str,
    parent_hash: str,
    evaluation_report_hash: str,
    limitations: Sequence[str],
) -> Dict[str, Any]:
    return {
        "candidate_hash": candidate_hash,
        "parent_version": parent_version,
        "parent_hash": parent_hash,
        "evaluation_report_hash": evaluation_report_hash,
        "test_suite_version": CASE_SUITE_VERSION,
        "known_limitations": list(limitations),
        "reviewer_checklist": [
            "Review candidate diff against parent",
            "Confirm safety invariants still present",
            "Review eval report and any failures",
            "Confirm no transcript text was used in generation",
            "Copy approved candidate into prompts/tutor/ only after approval",
            "Update prompts/registry.json manually",
            "Set TUTOR_PROMPT_VERSION deliberately",
            "Re-run full tests before deploy",
            "Record change in a separate human commit",
        ],
        "approval_status": "pending_human_review",
        "auto_deploy": False,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eval_harness", description="LLM-as-judge evaluation harness")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Validate synthetic eval case suite")
    sub.add_parser("run-offline-fixtures", help="Run deterministic offline promotion fixtures")
    live = sub.add_parser("run-live", help="Gated live OpenAI evaluation (disabled by default)")
    live.add_argument("--candidate", required=True)
    live.add_argument("--live-openai", action="store_true")
    live.add_argument("--confirm-cost", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            cases = load_cases()
            errors = validate_case_suite(cases)
            assert_no_historical_transcripts(cases)
            rubric = load_rubric()
            for d in JUDGE_DIMENSIONS:
                if d not in rubric.get("dimensions", {}):
                    errors.append(f"rubric missing dimension {d}")
            if errors:
                print("validation failed:", file=sys.stderr)
                for e in errors:
                    print(f" - {e}", file=sys.stderr)
                return 1
            print(f"OK: {len(cases)} cases, rubric dimensions={len(JUDGE_DIMENSIONS)}")
            return 0
        if args.command == "run-offline-fixtures":
            result = run_offline_fixtures()
            print(json.dumps({"fixture_count": result["fixture_count"], "ok": True}, indent=2))
            for name, outcome in result["outcomes"].items():
                print(f"{name}: {outcome['decision']}")
            return 0
        if args.command == "run-live":
            if not live_eval_allowed(
                os.environ, live_openai=args.live_openai, confirm_cost=args.confirm_cost
            ):
                raise EvalHarnessError(
                    "Live mode requires ALLOW_LIVE_FLYWHEEL_OPENAI=true, --live-openai, and --confirm-cost"
                )
            # Deliberately do not initialize OpenAI here in automated paths; refuse unless fully gated.
            # Still do not make a live call in this iteration's default implementation.
            raise EvalHarnessError(
                "Live OpenAI evaluation is gated and not executed from automated/default paths"
            )
    except EvalHarnessError as exc:
        print(f"eval_harness error: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
