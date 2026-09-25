"""Local friction analysis over consented redacted sessions (no OpenAI).

Lexical similarity uses token-set Jaccard similarity. This can miss
semantically equivalent questions phrased with different vocabulary.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple


class FrictionSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "and", "or",
        "in", "on", "for", "it", "that", "this", "with", "as", "at", "by", "from",
        "do", "does", "did", "can", "could", "would", "should", "you", "i", "me",
        "my", "we", "our", "please", "just", "so", "about", "what", "how", "why",
        "when", "where",
    }
)

CONFUSION_PATTERNS = (
    re.compile(r"\bi\s+don'?t\s+understand\b", re.I),
    re.compile(r"\bcan\s+you\s+explain\s+again\b", re.I),
    re.compile(r"\bexplain\s+again\b", re.I),
    re.compile(r"\bi'?m\s+confused\b", re.I),
    re.compile(r"\bstill\s+confused\b", re.I),
    re.compile(r"\bsimpler\b", re.I),
    re.compile(r"\bin\s+simpler\s+words\b", re.I),
    re.compile(r"\btoo\s+hard\b", re.I),
)

SIMPLER_WORDING_PATTERNS = (
    re.compile(r"\bsimpler\b", re.I),
    re.compile(r"\beasier\s+words\b", re.I),
    re.compile(r"\bin\s+simple\s+(?:terms|words)\b", re.I),
)

DEFAULT_JACCARD_THRESHOLD = 0.55
HIGH_LATENCY_MS = 2500.0
LONG_MONOLOGUE_CHARS = 900
INTERRUPTIONS_PER_SLIDE_HIGH = 3
PAUSES_HIGH = 4
NAV_BACK_HIGH = 2
RAG_MISS_RATE_HIGH = 0.5
SAFETY_REDIRECT_HIGH = 2


class InterventionCategory(str, Enum):
    SIMPLIFY_EXPLANATION = "simplify_explanation"
    ADD_CONCRETE_EXAMPLE = "add_concrete_example"
    SLOW_PACING = "slow_pacing"
    SPLIT_EXPLANATION = "split_explanation_into_smaller_parts"
    ADD_COMPREHENSION_CHECK = "add_comprehension_check"
    STRENGTHEN_TOPIC_RECOVERY = "strengthen_topic_recovery"
    ADD_CURRICULUM_KNOWLEDGE = "add_curriculum_knowledge"
    IMPROVE_RETRIEVAL_CONTENT = "improve_retrieval_content"
    INVESTIGATE_LATENCY = "investigate_latency"
    HUMAN_SAFETY_REVIEW = "human_safety_review"
    ADD_RECAP = "add_recap_before_advancing"
    SHORTEN_NARRATION = "shorten_narration_segments"


@dataclass(frozen=True)
class FrictionSignal:
    reason_code: str
    session_id: str
    slide_index: Optional[int]
    severity: FrictionSeverity
    supporting_metrics: Dict[str, float]
    confidence: float
    intervention_category: InterventionCategory
    aggregate_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["intervention_category"] = self.intervention_category.value
        return d


@dataclass
class SlideFriction:
    slide_index: int
    signal_counts: Dict[str, int] = field(default_factory=dict)
    signals: List[FrictionSignal] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slide_index": self.slide_index,
            "signal_counts": dict(sorted(self.signal_counts.items())),
            "signal_reason_codes": sorted({s.reason_code for s in self.signals}),
        }


@dataclass
class SessionFriction:
    session_id: str
    eligible_for_transcript_analysis: bool
    exclusion_reason: Optional[str]
    final_slide_index: Optional[int]
    lesson_mode: Optional[str]
    signals: List[FrictionSignal] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "eligible_for_transcript_analysis": self.eligible_for_transcript_analysis,
            "exclusion_reason": self.exclusion_reason,
            "final_slide_index": self.final_slide_index,
            "lesson_mode": self.lesson_mode,
            "signal_count": len(self.signals),
            "reason_codes": sorted({s.reason_code for s in self.signals}),
        }


@dataclass
class FrictionReport:
    analyzed_session_count: int
    excluded_session_count: int
    exclusion_reasons: Dict[str, int]
    completion_rate: float
    average_slide_reached: float
    interruption_rate: float
    pause_rate: float
    navigation_back_rate: float
    qa_entry_rate: float
    safety_redirect_hold_rate: float
    rag_hit_rate: float
    rag_miss_rate: float
    latency_summaries: Dict[str, float]
    friction_by_slide: Dict[int, Dict[str, Any]]
    repeated_question_cluster_counts: Dict[str, int]
    prioritized_recommendations: List[Dict[str, Any]]
    prompt_version_distribution: Dict[str, int]
    data_quality_limitations: List[str]
    configuration: Dict[str, Any]
    session_summaries: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analyzed_session_count": self.analyzed_session_count,
            "excluded_session_count": self.excluded_session_count,
            "exclusion_reasons": dict(sorted(self.exclusion_reasons.items())),
            "completion_rate": self.completion_rate,
            "average_slide_reached": self.average_slide_reached,
            "interruption_rate": self.interruption_rate,
            "pause_rate": self.pause_rate,
            "navigation_back_rate": self.navigation_back_rate,
            "qa_entry_rate": self.qa_entry_rate,
            "safety_redirect_hold_rate": self.safety_redirect_hold_rate,
            "rag_hit_rate": self.rag_hit_rate,
            "rag_miss_rate": self.rag_miss_rate,
            "latency_summaries": dict(sorted(self.latency_summaries.items())),
            "friction_by_slide": {
                str(k): v for k, v in sorted(self.friction_by_slide.items())
            },
            "repeated_question_cluster_counts": dict(
                sorted(self.repeated_question_cluster_counts.items())
            ),
            "prioritized_recommendations": self.prioritized_recommendations,
            "prompt_version_distribution": dict(
                sorted(self.prompt_version_distribution.items())
            ),
            "data_quality_limitations": self.data_quality_limitations,
            "configuration": self.configuration,
            "session_summaries": self.session_summaries,
            "disclaimer": (
                "Correlation does not prove causation. Friction signals are "
                "heuristic indicators for human review only."
            ),
        }


def normalize_question(text: str) -> Set[str]:
    lowered = text.lower()
    cleaned = re.sub(r"[^\w\s]", " ", lowered)
    tokens = [t for t in cleaned.split() if t and t not in STOP_WORDS]
    return set(tokens)


def jaccard_similarity(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _metric_map(session: Mapping[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for m in session.get("metrics") or []:
        name = m.get("metric_name")
        val = m.get("numeric_value")
        if name is not None and val is not None:
            out[str(name)] = float(val)
    return out


def _has_confusion_cue(text: str) -> bool:
    return any(p.search(text) for p in CONFUSION_PATTERNS)


def _requests_simpler(text: str) -> bool:
    return any(p.search(text) for p in SIMPLER_WORDING_PATTERNS)


def _find_repeated_clusters(
    utterances: Sequence[Tuple[int, str]],
    *,
    threshold: float,
    session_id: str,
) -> List[FrictionSignal]:
    by_slide: Dict[int, List[Set[str]]] = {}
    for slide, text in utterances:
        toks = normalize_question(text)
        if len(toks) < 2:
            continue
        by_slide.setdefault(slide, []).append(toks)

    signals: List[FrictionSignal] = []
    for slide, token_sets in by_slide.items():
        clusters = 0
        used = [False] * len(token_sets)
        for i in range(len(token_sets)):
            if used[i]:
                continue
            size = 1
            used[i] = True
            for j in range(i + 1, len(token_sets)):
                if used[j]:
                    continue
                if jaccard_similarity(token_sets[i], token_sets[j]) >= threshold:
                    used[j] = True
                    size += 1
            if size >= 2:
                clusters += 1
                signals.append(
                    FrictionSignal(
                        reason_code="repeated_similar_questions",
                        session_id=session_id,
                        slide_index=slide,
                        severity=FrictionSeverity.MEDIUM if size == 2 else FrictionSeverity.HIGH,
                        supporting_metrics={"cluster_size": float(size)},
                        confidence=min(0.95, 0.55 + 0.15 * size),
                        intervention_category=InterventionCategory.SIMPLIFY_EXPLANATION,
                        aggregate_id=f"rq:{session_id}:slide:{slide}:c{clusters}",
                    )
                )
    return signals


def _metric_only_signals(
    sid: str,
    session: Mapping[str, Any],
    metrics: Mapping[str, float],
    final_slide_index: int,
) -> List[FrictionSignal]:
    signals: List[FrictionSignal] = []
    interruptions = metrics.get("interruptions", 0.0)
    pauses = metrics.get("pauses", 0.0)
    navigations = metrics.get("navigations", 0.0)
    slides_completed = metrics.get("slides_completed", 0.0)
    safety_redirects = metrics.get("safety_redirects", 0.0)
    safety_holds = metrics.get("safety_holds", 0.0)
    rag_hits = metrics.get("rag_hits", 0.0)
    rag_misses = metrics.get("rag_misses", 0.0)
    slide = session.get("final_slide_index")
    mode = str(session.get("final_lesson_mode") or "")

    if interruptions >= INTERRUPTIONS_PER_SLIDE_HIGH:
        signals.append(
            FrictionSignal(
                reason_code="multiple_interruptions",
                session_id=sid,
                slide_index=slide if isinstance(slide, int) else None,
                severity=FrictionSeverity.MEDIUM,
                supporting_metrics={"interruptions": interruptions},
                confidence=0.7,
                intervention_category=InterventionCategory.SHORTEN_NARRATION,
            )
        )

    if pauses >= PAUSES_HIGH:
        signals.append(
            FrictionSignal(
                reason_code="frequent_pauses",
                session_id=sid,
                slide_index=slide if isinstance(slide, int) else None,
                severity=FrictionSeverity.LOW,
                supporting_metrics={"pauses": pauses},
                confidence=0.55,
                intervention_category=InterventionCategory.SLOW_PACING,
            )
        )

    if navigations >= NAV_BACK_HIGH and slides_completed > 0 and navigations > slides_completed:
        signals.append(
            FrictionSignal(
                reason_code="repeated_backward_navigation",
                session_id=sid,
                slide_index=slide if isinstance(slide, int) else None,
                severity=FrictionSeverity.MEDIUM,
                supporting_metrics={
                    "navigations": navigations,
                    "slides_completed": slides_completed,
                },
                confidence=0.6,
                intervention_category=InterventionCategory.ADD_RECAP,
            )
        )

    if isinstance(slide, int) and slide < final_slide_index:
        mode_u = mode.upper()
        if mode and "QA" not in mode_u and mode_u not in {"DONE"}:
            signals.append(
                FrictionSignal(
                    reason_code="early_dropout",
                    session_id=sid,
                    slide_index=slide,
                    severity=FrictionSeverity.HIGH,
                    supporting_metrics={"final_slide_index": float(slide)},
                    confidence=0.75,
                    intervention_category=InterventionCategory.SLOW_PACING,
                )
            )

    if "QA" not in mode.upper() and isinstance(slide, int) and slide >= final_slide_index:
        signals.append(
            FrictionSignal(
                reason_code="failed_qa_entry",
                session_id=sid,
                slide_index=slide,
                severity=FrictionSeverity.MEDIUM,
                supporting_metrics={},
                confidence=0.7,
                intervention_category=InterventionCategory.STRENGTHEN_TOPIC_RECOVERY,
            )
        )

    rag_total = rag_hits + rag_misses
    if rag_total >= 2:
        miss_rate = rag_misses / rag_total
        if miss_rate >= RAG_MISS_RATE_HIGH:
            signals.append(
                FrictionSignal(
                    reason_code="high_rag_miss_rate",
                    session_id=sid,
                    slide_index=slide if isinstance(slide, int) else None,
                    severity=FrictionSeverity.MEDIUM,
                    supporting_metrics={
                        "rag_hits": rag_hits,
                        "rag_misses": rag_misses,
                        "miss_rate": miss_rate,
                    },
                    confidence=0.8,
                    intervention_category=InterventionCategory.IMPROVE_RETRIEVAL_CONTENT,
                )
            )

    if safety_redirects + safety_holds >= SAFETY_REDIRECT_HIGH:
        signals.append(
            FrictionSignal(
                reason_code="repeated_safety_redirects",
                session_id=sid,
                slide_index=slide if isinstance(slide, int) else None,
                severity=FrictionSeverity.HIGH,
                supporting_metrics={
                    "safety_redirects": safety_redirects,
                    "safety_holds": safety_holds,
                },
                confidence=0.85,
                intervention_category=InterventionCategory.HUMAN_SAFETY_REVIEW,
            )
        )

    latencies: List[float] = []
    for ev in session.get("safety_events") or []:
        if ev.get("latency_ms") is not None:
            latencies.append(float(ev["latency_ms"]))
    for ev in session.get("rag_events") or []:
        for key in ("embedding_latency_ms", "retrieval_latency_ms"):
            if ev.get(key) is not None:
                latencies.append(float(ev[key]))
    if latencies:
        avg_lat = sum(latencies) / len(latencies)
        if avg_lat >= HIGH_LATENCY_MS:
            correlated = interruptions > 0 or (
                isinstance(slide, int) and slide < final_slide_index
            )
            signals.append(
                FrictionSignal(
                    reason_code="high_latency_engineering",
                    session_id=sid,
                    slide_index=slide if isinstance(slide, int) else None,
                    severity=FrictionSeverity.MEDIUM if correlated else FrictionSeverity.LOW,
                    supporting_metrics={
                        "avg_latency_ms": avg_lat,
                        "correlated_with_interruption_or_dropout": 1.0 if correlated else 0.0,
                    },
                    confidence=0.7,
                    intervention_category=InterventionCategory.INVESTIGATE_LATENCY,
                )
            )

    return signals


def analyze_session(
    session: Mapping[str, Any],
    *,
    jaccard_threshold: float = DEFAULT_JACCARD_THRESHOLD,
    final_slide_index: int = 7,
) -> Tuple[SessionFriction, List[Dict[str, Any]]]:
    sid = str(session.get("session_id") or "unknown")
    consent = bool(session.get("transcript_consent"))
    storage_active = bool(session.get("transcript_storage_active"))
    metrics = _metric_map(session)
    slide = session.get("final_slide_index")
    mode = session.get("final_lesson_mode")
    signals: List[FrictionSignal] = []
    local_examples: List[Dict[str, Any]] = []

    if not consent:
        return (
            SessionFriction(
                session_id=sid,
                eligible_for_transcript_analysis=False,
                exclusion_reason="no_transcript_consent",
                final_slide_index=slide if isinstance(slide, int) else None,
                lesson_mode=str(mode) if mode else None,
                signals=_metric_only_signals(sid, session, metrics, final_slide_index),
            ),
            [],
        )

    if not storage_active and not (session.get("transcript_events") or []):
        base = SessionFriction(
            session_id=sid,
            eligible_for_transcript_analysis=False,
            exclusion_reason="consented_but_no_transcript_events",
            final_slide_index=slide if isinstance(slide, int) else None,
            lesson_mode=str(mode) if mode else None,
            signals=_metric_only_signals(sid, session, metrics, final_slide_index),
        )
        return base, []

    transcripts = list(session.get("transcript_events") or [])
    user_utts: List[Tuple[int, str]] = []
    for ev in transcripts:
        if ev.get("role") != "user":
            continue
        text = str(ev.get("redacted_text") or "")
        sidx = int(ev.get("slide_index") or 0)
        user_utts.append((sidx, text))
        if _has_confusion_cue(text):
            signals.append(
                FrictionSignal(
                    reason_code="confusion_cue",
                    session_id=sid,
                    slide_index=sidx,
                    severity=FrictionSeverity.MEDIUM,
                    supporting_metrics={
                        "text_character_count": float(ev.get("text_character_count") or 0)
                    },
                    confidence=0.8,
                    intervention_category=InterventionCategory.SIMPLIFY_EXPLANATION,
                )
            )
            local_examples.append(
                {
                    "kind": "confusion_cue",
                    "session_id": sid,
                    "slide_index": sidx,
                    "redacted_text": text,
                    "sensitive_local_review_only": True,
                }
            )
        if _requests_simpler(text):
            signals.append(
                FrictionSignal(
                    reason_code="request_simpler_wording",
                    session_id=sid,
                    slide_index=sidx,
                    severity=FrictionSeverity.MEDIUM,
                    supporting_metrics={},
                    confidence=0.75,
                    intervention_category=InterventionCategory.SIMPLIFY_EXPLANATION,
                )
            )

    signals.extend(
        _find_repeated_clusters(user_utts, threshold=jaccard_threshold, session_id=sid)
    )

    for i, ev in enumerate(transcripts):
        if ev.get("role") != "assistant":
            continue
        chars = int(ev.get("text_character_count") or 0)
        if chars < LONG_MONOLOGUE_CHARS:
            continue
        sidx = int(ev.get("slide_index") or 0)
        signals.append(
            FrictionSignal(
                reason_code="long_tutor_monologue",
                session_id=sid,
                slide_index=sidx,
                severity=FrictionSeverity.LOW,
                supporting_metrics={"text_character_count": float(chars)},
                confidence=0.6,
                intervention_category=InterventionCategory.SHORTEN_NARRATION,
            )
        )
        for nxt in transcripts[i + 1 : i + 3]:
            if nxt.get("role") == "user" and int(nxt.get("slide_index") or 0) == sidx:
                signals.append(
                    FrictionSignal(
                        reason_code="question_after_explanation",
                        session_id=sid,
                        slide_index=sidx,
                        severity=FrictionSeverity.MEDIUM,
                        supporting_metrics={"prior_monologue_chars": float(chars)},
                        confidence=0.65,
                        intervention_category=InterventionCategory.ADD_COMPREHENSION_CHECK,
                    )
                )
                break

    confused_slides = {s.slide_index for s in signals if s.reason_code == "confusion_cue"}
    assistant_by_slide: Dict[int, List[str]] = {}
    for ev in transcripts:
        if ev.get("role") == "assistant":
            sidx = int(ev.get("slide_index") or 0)
            assistant_by_slide.setdefault(sidx, []).append(str(ev.get("redacted_text") or ""))
    for sidx in confused_slides:
        texts = assistant_by_slide.get(sidx or 0, [])
        if texts and not any("?" in t for t in texts):
            signals.append(
                FrictionSignal(
                    reason_code="missing_comprehension_check",
                    session_id=sid,
                    slide_index=sidx,
                    severity=FrictionSeverity.LOW,
                    supporting_metrics={},
                    confidence=0.5,
                    intervention_category=InterventionCategory.ADD_COMPREHENSION_CHECK,
                )
            )

    signals.extend(_metric_only_signals(sid, session, metrics, final_slide_index))

    return (
        SessionFriction(
            session_id=sid,
            eligible_for_transcript_analysis=True,
            exclusion_reason=None,
            final_slide_index=slide if isinstance(slide, int) else None,
            lesson_mode=str(mode) if mode else None,
            signals=signals,
        ),
        local_examples,
    )


def build_friction_report(
    sessions: Sequence[Mapping[str, Any]],
    *,
    jaccard_threshold: float = DEFAULT_JACCARD_THRESHOLD,
    final_slide_index: int = 7,
    recommendations: Optional[List[Dict[str, Any]]] = None,
    configuration: Optional[Dict[str, Any]] = None,
) -> Tuple[FrictionReport, List[Dict[str, Any]]]:
    analyzed: List[SessionFriction] = []
    all_examples: List[Dict[str, Any]] = []
    exclusion_reasons: Dict[str, int] = {}

    completed = 0
    slides_sum = 0
    interruptions_total = 0.0
    pauses_total = 0.0
    nav_back_sessions = 0
    qa_entries = 0
    safety_sessions = 0
    rag_hits = 0.0
    rag_misses = 0.0
    latency_all: List[float] = []
    prompt_versions: Dict[str, int] = {}
    by_slide: Dict[int, SlideFriction] = {}
    cluster_counts: Dict[str, int] = {}

    for session in sessions:
        sf, examples = analyze_session(
            session,
            jaccard_threshold=jaccard_threshold,
            final_slide_index=final_slide_index,
        )
        analyzed.append(sf)
        all_examples.extend(examples)
        if sf.exclusion_reason:
            exclusion_reasons[sf.exclusion_reason] = (
                exclusion_reasons.get(sf.exclusion_reason, 0) + 1
            )

        metrics = _metric_map(session)
        interruptions_total += metrics.get("interruptions", 0.0)
        pauses_total += metrics.get("pauses", 0.0)
        rag_hits += metrics.get("rag_hits", 0.0)
        rag_misses += metrics.get("rag_misses", 0.0)
        if metrics.get("safety_redirects", 0) + metrics.get("safety_holds", 0) > 0:
            safety_sessions += 1

        slide = sf.final_slide_index if sf.final_slide_index is not None else 0
        slides_sum += slide
        mode = (sf.lesson_mode or "").upper()
        if "QA" in mode or slide >= final_slide_index:
            completed += 1
        if "QA" in mode:
            qa_entries += 1

        if any(s.reason_code == "repeated_backward_navigation" for s in sf.signals):
            nav_back_sessions += 1

        pv = str(session.get("tutor_prompt_version") or "unknown")
        prompt_versions[pv] = prompt_versions.get(pv, 0) + 1

        for ev in session.get("safety_events") or []:
            if ev.get("latency_ms") is not None:
                latency_all.append(float(ev["latency_ms"]))
        for ev in session.get("rag_events") or []:
            for key in ("embedding_latency_ms", "retrieval_latency_ms"):
                if ev.get(key) is not None:
                    latency_all.append(float(ev[key]))

        for sig in sf.signals:
            if sig.slide_index is None:
                continue
            sf_slide = by_slide.setdefault(sig.slide_index, SlideFriction(slide_index=sig.slide_index))
            sf_slide.signals.append(sig)
            sf_slide.signal_counts[sig.reason_code] = (
                sf_slide.signal_counts.get(sig.reason_code, 0) + 1
            )
            if sig.reason_code == "repeated_similar_questions":
                key = f"slide_{sig.slide_index}"
                cluster_counts[key] = cluster_counts.get(key, 0) + 1

    n = len(analyzed)
    excluded = sum(1 for s in analyzed if s.exclusion_reason)
    rag_total = rag_hits + rag_misses
    latency_summaries = {
        "sample_count": float(len(latency_all)),
        "avg_ms": (sum(latency_all) / len(latency_all)) if latency_all else 0.0,
        "max_ms": max(latency_all) if latency_all else 0.0,
    }

    report = FrictionReport(
        analyzed_session_count=n,
        excluded_session_count=excluded,
        exclusion_reasons=exclusion_reasons,
        completion_rate=(completed / n) if n else 0.0,
        average_slide_reached=(slides_sum / n) if n else 0.0,
        interruption_rate=(interruptions_total / n) if n else 0.0,
        pause_rate=(pauses_total / n) if n else 0.0,
        navigation_back_rate=(nav_back_sessions / n) if n else 0.0,
        qa_entry_rate=(qa_entries / n) if n else 0.0,
        safety_redirect_hold_rate=(safety_sessions / n) if n else 0.0,
        rag_hit_rate=(rag_hits / rag_total) if rag_total else 0.0,
        rag_miss_rate=(rag_misses / rag_total) if rag_total else 0.0,
        latency_summaries=latency_summaries,
        friction_by_slide={k: v.to_dict() for k, v in by_slide.items()},
        repeated_question_cluster_counts=cluster_counts,
        prioritized_recommendations=recommendations or [],
        prompt_version_distribution=prompt_versions,
        data_quality_limitations=[
            "Lexical Jaccard similarity can miss semantically equivalent questions.",
            "Only consented sessions contribute redacted transcript signals.",
            "Correlation between latency and dropout does not prove causation.",
            "Backward navigation is inferred from navigation vs slides_completed metrics.",
            "Micro-comprehension checks are inferred heuristically from assistant '?' marks.",
        ],
        configuration=configuration
        or {
            "jaccard_threshold": jaccard_threshold,
            "final_slide_index": final_slide_index,
            "generated_at_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        session_summaries=[s.to_dict() for s in analyzed],
    )
    return report, all_examples


def report_contains_transcript_text(report: Mapping[str, Any]) -> bool:
    def walk(obj: Any) -> bool:
        if isinstance(obj, dict):
            if "redacted_text" in obj:
                return True
            return any(walk(v) for v in obj.values())
        if isinstance(obj, list):
            return any(walk(v) for v in obj)
        return False

    return walk(report)
