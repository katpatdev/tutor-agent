"""Deterministic curriculum/friction → recommendation mapping (no LLM)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from friction_analyzer import FrictionSignal, InterventionCategory


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    title: str
    actions: List[str]
    evidence_reason_codes: List[str]
    evidence_count: int
    limitations: List[str]
    requires_human_review: bool
    never_weaken_safety: bool
    category: str
    priority: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "title": self.title,
            "actions": self.actions,
            "evidence_reason_codes": self.evidence_reason_codes,
            "evidence_count": self.evidence_count,
            "limitations": self.limitations,
            "requires_human_review": self.requires_human_review,
            "never_weaken_safety": self.never_weaken_safety,
            "category": self.category,
            "priority": self.priority,
            "auto_prompt_edit": False,
        }


_RULES: List[Dict[str, Any]] = [
    {
        "id": "confusion_simplify",
        "reason_codes": {"confusion_cue", "request_simpler_wording", "repeated_similar_questions"},
        "title": "Simplify explanations where students show confusion",
        "actions": [
            "simplify terminology",
            "add one concrete example",
            "add a micro-comprehension check",
        ],
        "category": InterventionCategory.SIMPLIFY_EXPLANATION.value,
        "priority": 10,
        "human_review": False,
        "limitations": [
            "Confusion cues are lexical heuristics and may miss calm but lost students.",
        ],
    },
    {
        "id": "nav_back_recap",
        "reason_codes": {"repeated_backward_navigation"},
        "title": "Add a recap before advancing",
        "actions": ["add a brief recap before advancing to the next slide"],
        "category": InterventionCategory.ADD_RECAP.value,
        "priority": 20,
        "human_review": False,
        "limitations": [
            "Backward navigation is inferred from aggregate metrics, not exact nav direction.",
        ],
    },
    {
        "id": "monologue_interrupt",
        "reason_codes": {"long_tutor_monologue", "multiple_interruptions", "question_after_explanation"},
        "title": "Shorten narration and add check-ins",
        "actions": [
            "shorten narration segments",
            "add a student check-in",
            "split explanation into smaller parts",
        ],
        "category": InterventionCategory.SHORTEN_NARRATION.value,
        "priority": 15,
        "human_review": False,
        "limitations": [
            "Monologue length uses character counts of redacted assistant text, not speaking time.",
        ],
    },
    {
        "id": "rag_misses",
        "reason_codes": {"high_rag_miss_rate"},
        "title": "Improve retrieval source documents",
        "actions": [
            "improve or upload source documents",
            "do not change the tutor prompt automatically for RAG misses",
        ],
        "category": InterventionCategory.IMPROVE_RETRIEVAL_CONTENT.value,
        "priority": 25,
        "human_review": False,
        "limitations": [
            "Miss rate alone does not identify which knowledge gap exists.",
        ],
    },
    {
        "id": "latency_engineering",
        "reason_codes": {"high_latency_engineering"},
        "title": "Investigate latency as an engineering issue",
        "actions": [
            "classify as an engineering issue, not a curriculum issue",
            "profile moderation, embedding, and TTS latency",
        ],
        "category": InterventionCategory.INVESTIGATE_LATENCY.value,
        "priority": 5,
        "human_review": False,
        "limitations": [
            "Latency correlated with interruption or dropout does not prove causation.",
        ],
    },
    {
        "id": "safety_human_review",
        "reason_codes": {"repeated_safety_redirects"},
        "title": "Human safety review required",
        "actions": [
            "review safety redirect patterns with a human",
            "never weaken moderation or output safety",
        ],
        "category": InterventionCategory.HUMAN_SAFETY_REVIEW.value,
        "priority": 1,
        "human_review": True,
        "limitations": [
            "Redirect volume may reflect student probing rather than prompt defects.",
        ],
    },
    {
        "id": "missing_checks",
        "reason_codes": {"missing_comprehension_check"},
        "title": "Add micro-comprehension checks",
        "actions": ["add a short comprehension check after key explanations"],
        "category": InterventionCategory.ADD_COMPREHENSION_CHECK.value,
        "priority": 30,
        "human_review": False,
        "limitations": [
            "Detection relies on '?' in assistant text and may under/over count.",
        ],
    },
    {
        "id": "early_dropout",
        "reason_codes": {"early_dropout", "failed_qa_entry"},
        "title": "Review lesson completion and Q&A entry",
        "actions": [
            "review pacing near early-exit slides",
            "verify Q&A transition reliability in application code (not prompt-only)",
        ],
        "category": InterventionCategory.SLOW_PACING.value,
        "priority": 12,
        "human_review": False,
        "limitations": [
            "Dropout may be environmental (device, time) rather than curriculum friction.",
        ],
    },
]


def recommendations_from_signals(
    signals: Sequence[FrictionSignal],
) -> List[Recommendation]:
    counts: Counter[str] = Counter(s.reason_code for s in signals)
    out: List[Recommendation] = []
    for rule in _RULES:
        matched = {code: counts[code] for code in rule["reason_codes"] if counts[code]}
        if not matched:
            continue
        total = sum(matched.values())
        out.append(
            Recommendation(
                recommendation_id=rule["id"],
                title=rule["title"],
                actions=list(rule["actions"]),
                evidence_reason_codes=sorted(matched.keys()),
                evidence_count=total,
                limitations=list(rule["limitations"]),
                requires_human_review=bool(rule["human_review"]),
                never_weaken_safety=True,
                category=rule["category"],
                priority=int(rule["priority"]),
            )
        )
    out.sort(key=lambda r: (r.priority, -r.evidence_count, r.recommendation_id))
    return out


def recommendations_to_dicts(recs: Sequence[Recommendation]) -> List[Dict[str, Any]]:
    return [r.to_dict() for r in recs]
