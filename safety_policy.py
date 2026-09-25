"""Deterministic student safety policy and trusted templates."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional

from moderation_service import (
    ModerationResult,
    ModerationTimeout,
    ModerationUnavailable,
)


class SafetyDecision(Enum):
    ALLOW = auto()
    REDIRECT = auto()
    SAFETY_HOLD = auto()


class SafetySource(Enum):
    USER_INPUT = auto()
    ASSISTANT_OUTPUT = auto()


class SafetyStatus(Enum):
    NORMAL = "normal"
    REDIRECTING = "redirecting"
    HOLD = "hold"


class SafetyReason(Enum):
    ALLOWED = "allowed"
    GRAPHIC_CONTENT = "graphic_content"
    INAPPROPRIATE_CONTENT = "inappropriate_content"
    DISTRESS_SUPPORT = "distress_support"
    IMMEDIATE_DANGER = "immediate_danger"
    SELF_HARM = "self_harm"
    SEXUAL_MINORS = "sexual_minors"
    UNKNOWN_FLAG = "unknown_flag"
    MODERATION_TIMEOUT = "moderation_timeout"
    MODERATION_UNAVAILABLE = "moderation_unavailable"
    OVERSIZED_CONTENT = "oversized_content"
    LOCAL_DISTRESS = "local_distress"
    LOCAL_IMMEDIATE_DANGER = "local_immediate_danger"


TEMPLATES = {
    "graphic_redirect": (
        "I can explain the science and how people stay safe, but I will not describe "
        "graphic injuries. Let's focus on what happens during the disaster and how to prepare."
    ),
    "inappropriate_redirect": (
        "That topic is not part of our classroom lesson. Let's return to natural disasters "
        "and how communities prepare to stay safe."
    ),
    "distress_support": (
        "It's okay to feel worried about big storms or earthquakes. Many students feel that way. "
        "We can talk about what scientists know and simple ways people prepare. "
        "If you keep feeling scared, please tell a trusted adult, teacher, or guardian."
    ),
    "immediate_danger_hold": (
        "I'm glad you told me. Please tell a trusted adult, teacher, parent, or guardian right now. "
        "If someone is in immediate danger, move to a safe place and contact local emergency "
        "services with an adult."
    ),
    "self_harm_hold": (
        "I'm glad you told me. Please talk to a trusted adult, teacher, parent, or guardian right away. "
        "If you are in immediate danger, move to a safe place and contact local emergency services with an adult."
    ),
    "moderation_unavailable": (
        "I need a moment to keep our classroom conversation safe. Please wait, or ask a trusted adult "
        "for help if you need support right now."
    ),
    "unsafe_output_replacement": (
        "Let me explain that in a calm, classroom-safe way. Natural disasters can be serious, "
        "and people stay safer by preparing, listening to warnings, and following adult instructions."
    ),
    "oversized_content": (
        "That message was too long for me to check safely. Please ask a shorter question about "
        "the science or how people prepare."
    ),
}


HOLD_REASONS = {
    SafetyReason.IMMEDIATE_DANGER,
    SafetyReason.SELF_HARM,
    SafetyReason.SEXUAL_MINORS,
    SafetyReason.UNKNOWN_FLAG,
    SafetyReason.MODERATION_TIMEOUT,
    SafetyReason.MODERATION_UNAVAILABLE,
    SafetyReason.OVERSIZED_CONTENT,
    SafetyReason.LOCAL_IMMEDIATE_DANGER,
}

# High-risk OpenAI category names (from openai.types.moderation.Categories).
SELF_HARM_CATEGORIES = {
    "self_harm",
    "self_harm_intent",
    "self_harm_instructions",
}
SEXUAL_MINORS_CATEGORIES = {"sexual_minors"}
GRAPHIC_CATEGORIES = {"violence_graphic"}
VIOLENCE_CATEGORIES = {"violence", "illicit_violent"}
OTHER_REDIRECT_CATEGORIES = {
    "harassment",
    "harassment_threatening",
    "hate",
    "hate_threatening",
    "illicit",
    "sexual",
}

_IMMEDIATE_DANGER_RE = re.compile(
    r"\b("
    r"i('m| am) (hurt|bleeding|trapped)|"
    r"someone is (hurt|dying|attacking)|"
    r"call (the )?police|"
    r"there is a (fire|gun|shooter) (here|in)|"
    r"help me now"
    r")\b",
    re.IGNORECASE,
)

_DISTRESS_RE = re.compile(
    r"\b("
    r"i('m| am) (scared|afraid|worried|anxious)|"
    r"this (scares|frightens) me|"
    r"i feel (unsafe|scared)"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PolicyDecision:
    decision: SafetyDecision
    reason: SafetyReason
    template_key: Optional[str]
    notice: Optional[str]


@dataclass(frozen=True)
class SafetyEvent:
    timestamp: float
    event_id: str
    decision: str
    source: str
    reason_code: str
    moderation_latency_ms: Optional[float]
    fallback_used: bool


def local_distress_or_danger(text: str) -> Optional[SafetyReason]:
    """Small deterministic detector — not a substitute for OpenAI Moderation."""
    if _IMMEDIATE_DANGER_RE.search(text):
        return SafetyReason.LOCAL_IMMEDIATE_DANGER
    if _DISTRESS_RE.search(text):
        return SafetyReason.LOCAL_DISTRESS
    return None


def evaluate_moderation(
    result: ModerationResult,
    *,
    source: SafetySource,
    local_reason: Optional[SafetyReason] = None,
) -> PolicyDecision:
    if local_reason is SafetyReason.LOCAL_IMMEDIATE_DANGER:
        return PolicyDecision(
            SafetyDecision.SAFETY_HOLD,
            SafetyReason.LOCAL_IMMEDIATE_DANGER,
            "immediate_danger_hold",
            TEMPLATES["immediate_danger_hold"],
        )

    flagged = set(result.flagged_category_names)

    if flagged & SEXUAL_MINORS_CATEGORIES:
        return PolicyDecision(
            SafetyDecision.SAFETY_HOLD,
            SafetyReason.SEXUAL_MINORS,
            "inappropriate_redirect",
            TEMPLATES["immediate_danger_hold"],
        )
    if flagged & SELF_HARM_CATEGORIES:
        return PolicyDecision(
            SafetyDecision.SAFETY_HOLD,
            SafetyReason.SELF_HARM,
            "self_harm_hold",
            TEMPLATES["self_harm_hold"],
        )
    if flagged & GRAPHIC_CATEGORIES:
        return PolicyDecision(
            SafetyDecision.REDIRECT,
            SafetyReason.GRAPHIC_CONTENT,
            "graphic_redirect",
            TEMPLATES["graphic_redirect"],
        )
    if flagged & (VIOLENCE_CATEGORIES | OTHER_REDIRECT_CATEGORIES):
        # Violence without graphic may still be educational; if API flagged, redirect rather than hold.
        return PolicyDecision(
            SafetyDecision.REDIRECT,
            SafetyReason.INAPPROPRIATE_CONTENT,
            "inappropriate_redirect",
            TEMPLATES["inappropriate_redirect"],
        )
    if result.flagged and not flagged:
        return PolicyDecision(
            SafetyDecision.SAFETY_HOLD,
            SafetyReason.UNKNOWN_FLAG,
            "moderation_unavailable",
            TEMPLATES["moderation_unavailable"],
        )
    if flagged:
        # Unknown category names → fail closed.
        known = (
            SELF_HARM_CATEGORIES
            | SEXUAL_MINORS_CATEGORIES
            | GRAPHIC_CATEGORIES
            | VIOLENCE_CATEGORIES
            | OTHER_REDIRECT_CATEGORIES
        )
        if flagged - known:
            return PolicyDecision(
                SafetyDecision.SAFETY_HOLD,
                SafetyReason.UNKNOWN_FLAG,
                "moderation_unavailable",
                TEMPLATES["moderation_unavailable"],
            )

    if local_reason is SafetyReason.LOCAL_DISTRESS:
        return PolicyDecision(
            SafetyDecision.ALLOW,
            SafetyReason.DISTRESS_SUPPORT,
            None,
            None,
        )

    return PolicyDecision(SafetyDecision.ALLOW, SafetyReason.ALLOWED, None, None)


def decision_from_failure(exc: Exception) -> PolicyDecision:
    if isinstance(exc, ModerationTimeout):
        return PolicyDecision(
            SafetyDecision.SAFETY_HOLD,
            SafetyReason.MODERATION_TIMEOUT,
            "moderation_unavailable",
            TEMPLATES["moderation_unavailable"],
        )
    if isinstance(exc, ModerationUnavailable):
        return PolicyDecision(
            SafetyDecision.SAFETY_HOLD,
            SafetyReason.MODERATION_UNAVAILABLE,
            "moderation_unavailable",
            TEMPLATES["moderation_unavailable"],
        )
    return PolicyDecision(
        SafetyDecision.SAFETY_HOLD,
        SafetyReason.MODERATION_UNAVAILABLE,
        "moderation_unavailable",
        TEMPLATES["moderation_unavailable"],
    )


def oversized_decision() -> PolicyDecision:
    return PolicyDecision(
        SafetyDecision.SAFETY_HOLD,
        SafetyReason.OVERSIZED_CONTENT,
        "oversized_content",
        TEMPLATES["oversized_content"],
    )


def _decision_label(decision: SafetyDecision) -> str:
    if decision is SafetyDecision.ALLOW:
        return "allow"
    if decision is SafetyDecision.REDIRECT:
        return "redirect"
    return "hold"


def make_safety_event(
    *,
    decision: SafetyDecision,
    source: SafetySource,
    reason: SafetyReason,
    latency_ms: Optional[float],
    fallback_used: bool,
) -> SafetyEvent:
    return SafetyEvent(
        timestamp=time.time(),
        event_id=str(uuid.uuid4()),
        decision=_decision_label(decision),
        source="user_input" if source is SafetySource.USER_INPUT else "assistant_output",
        reason_code=reason.value,
        moderation_latency_ms=latency_ms,
        fallback_used=fallback_used,
    )


def template_text(key: str) -> str:
    return TEMPLATES[key]
