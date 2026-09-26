"""Deterministic classroom-control intents (no LLM classification).

Parses final transcriptions after input safety. Clear control utterances must
not reach RAG or the conversational LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from tutor_agent.lesson.voice_navigation import VoiceNavAction, VoiceNavIntent


class ClassroomControlKind(str, Enum):
    NAVIGATION = "navigation"
    ACKNOWLEDGE = "acknowledge"
    CONTINUE = "continue"
    AFFIRM = "affirm"  # plain yes/ok — resolved by expected classroom prompt
    REPEAT = "repeat"
    QA_COMPLETION = "qa_completion"
    RETURN_ORIGIN = "return_origin"
    CLARIFY = "clarify"  # unresolved control-like; do not invent a slide change
    STAY = "stay"  # negated navigation: acknowledge stay, do not move
    QUESTION_REFERENCE = "question_reference"  # mention slide for upcoming Q
    QUESTION_PREAMBLE = "question_preamble"  # awaiting the actual question


@dataclass(frozen=True)
class ClassroomControlIntent:
    kind: ClassroomControlKind
    navigation: Optional[VoiceNavIntent] = None
    raw: str = ""
    clarify_prompt: str = ""
    # Optional absolute 1-based slide mentioned (clarify / question reference).
    suspected_slide_number: Optional[int] = None
    reference_slide_1based: Optional[int] = None


_CARDINAL_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

_ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
}

_NUMBER_WORDS = {**_CARDINAL_WORDS, **_ORDINAL_WORDS}

# Content / non-command shapes — checked before reducing to slide commands.
_QUESTION_BLOCKLIST = (
    re.compile(r"^(what|why|when|how|where|who)\b"),
    re.compile(r"\b(what is|what's|whats)\b.*\bslide\b"),
    re.compile(r"\bwhat did\b.*\bslide\b"),
    re.compile(r"\bwhat was (discussed|said|covered)\b.*\bslide\b"),
    re.compile(r"\bwhy\b.*\b(next )?slide\b"),
    re.compile(r"\bwhen\b.*\b(reach|get to|arrive)\b.*\bslide\b"),
    re.compile(r"\bexplain\b.*\b(previous|next|this|last)?\s*slide\b"),
    re.compile(r"\b(mean|meant)\b.*\bslide\b"),
    re.compile(r"\bimportant\b.*\bslide\b"),
    re.compile(r"\bhow many slides\b"),
    re.compile(r"\bare we (on|repeating)\b.*\bslide\b"),
    re.compile(r"\bwhat did you say\b.*\bslide\b"),
    re.compile(r"\bexplain (that|this|it) differently\b"),
    re.compile(r"\bthe next slide (explains|is about|covers)\b"),
    re.compile(r"\bgo back and explain\b"),
    re.compile(r"\bcan you go back and explain\b"),
    re.compile(r"\b(mentioned|discussed)\b.*\bon\s+slide\b"),
    re.compile(r"\blike you mentioned on slide\b"),
)

_NEGATION = re.compile(
    r"\b(?:do not|don't|dont|never|stop|avoid)\b.*\b(?:go|move|skip|jump|proceed|navigate)\b"
    r"|\b(?:do not|don't|dont)\s+(?:go|move|skip)\b"
    r"|\bi\s+don'?t\s+want\s+to\s+(?:move|go|skip|jump)\b"
)

_LEADING_CHATTER = re.compile(
    r"^(?:(?:yeah|yes|yep|yup|ok|okay|alright|all right|um+|uh+|so|well|right|"
    r"great|good|nice|perfect|thanks|thank you|sure|"
    r"this is good|that is good|that's good|thats good|"
    r"this is great|that is great|"
    r"okay this is good|ok this is good)\s+)+"
)
_POLITE_PREFIX = re.compile(
    r"^(?:(?:please|can we|could we|can you|could you|would you|"
    r"lets|let's|i would like to|i'd like to|i want to|"
    r"im ready to|i'm ready to)\s+)+"
)
_TRAILING_FILLER = re.compile(
    r"(?:\s+(?:then|please|thanks|thank you|if possible|if you (?:can|could)|"
    r"when you (?:can|could)|now|over here))+$"
)
# Explanatory tails after a clear command.
_TRAILING_REASON = re.compile(
    r"\s+(?:as|because|since|so that|given that|considering)"
    r"(?:\s+i\s+(?:already\s+)?(?:have\s+knowledge|know|understand|covered|learned)"
    r"(?:\s+(?:about|of)?\s+(?:this|that|it|the\s+part)?)?)?"
    r"(?:\s+.+)?$"
)

# Extract actionable nav clause — require a movement cue, not bare "slide N".
_EXTRACT_FROM_CHATTER = re.compile(
    r"^(?:.*?\b)(?=(?:can we|could we|can you|could you|would you|lets|let's|"
    r"please|go|move|skip|jump|proceed|take me|return|next slide|previous slide))"
)

_CLEAR_NAV_VERB = re.compile(
    r"\b(?:go|move|skip|jump|proceed|take me|return|navigate)\b"
)

# "I have a question from/about/in slide N" — store reference, do not navigate.
_QUESTION_REFERENCE = re.compile(
    r"\b(?:i\s+(?:just\s+)?(?:have|got)\s+(?:a\s+)?(?:question|doubt)|"
    r"my\s+question\s+is|"
    r"(?:have|got)\s+(?:a\s+)?(?:question|doubt))\b"
    r".*\b(?:from|about|on|regarding|in)\s+(?:the\s+)?slide\s+(\w+)\b"
    r"|\b(?:from|about|on|regarding|in)\s+(?:the\s+)?slide\s+(\w+)\b"
    r".*\b(?:question|doubt)\b"
)

_QUESTION_PREAMBLE = (
    re.compile(r"^oh\s+wait(?:\s+you\s+know\s+what)?$"),
    re.compile(r"^you\s+know\s+what$"),
    re.compile(r"^i\s+have\s+another\s+question$"),
    re.compile(r"^i\s+(?:just\s+)?have\s+a\s+(?:question|doubt)$"),
    re.compile(r"^i\s+have\s+a\s+doubt$"),
    re.compile(r"^can\s+i\s+ask\s+(?:something|a\s+question)$"),
    re.compile(r"^hey\s+i\s+just\s+have\s+a\s+question$"),
    re.compile(r"^wait(?:\s+a\s+(?:second|minute))?$"),
)


def normalize_control_text(text: str) -> str:
    cleaned = (text or "").strip().lower()
    cleaned = cleaned.replace("’", "'")
    cleaned = re.sub(r"[^\w\s']", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def soft_strip_control_wrappers(normalized: str) -> str:
    """Remove harmless politeness / chatter / explanatory tails."""
    text = normalized
    text = _LEADING_CHATTER.sub("", text).strip()
    text = _POLITE_PREFIX.sub("", text).strip()
    text = _TRAILING_FILLER.sub("", text).strip()
    text = _TRAILING_REASON.sub("", text).strip()
    text = _LEADING_CHATTER.sub("", text).strip()
    text = _TRAILING_FILLER.sub("", text).strip()
    return text


def _extract_command_candidate(normalized: str) -> str:
    """Prefer the navigation-bearing clause inside a longer conversational utterance."""
    stripped = soft_strip_control_wrappers(normalized)
    if stripped and stripped != normalized:
        return stripped
    m = _EXTRACT_FROM_CHATTER.match(normalized)
    if m and m.end() > 0:
        tail = normalized[m.end() :].strip()
        if tail:
            return soft_strip_control_wrappers(tail) or tail
    return stripped or normalized


def _parse_slide_number(token: str) -> Optional[int]:
    token = (token or "").strip().lower()
    if not token:
        return None
    if token.isdigit():
        value = int(token)
        return value if value >= 1 else None
    return _NUMBER_WORDS.get(token)


def _is_blocked_question(normalized: str) -> bool:
    for pattern in _QUESTION_BLOCKLIST:
        if pattern.search(normalized):
            return True
    return False


def _is_negated(normalized: str) -> bool:
    return bool(_NEGATION.search(normalized))


def _content_slide_reference_nouns(normalized: str) -> bool:
    """True when utterance is about a question/content regarding a slide."""
    if re.search(
        r"\b(question|doubt|explain|explained|discussed|mentioned|about)\b",
        normalized,
    ) and re.search(r"\bslide\b", normalized):
        # Allow if a clear movement verb is also present ("go to slide 3 to ask" rare).
        if _CLEAR_NAV_VERB.search(normalized) and not re.search(
            r"\b(question|doubt)\b", normalized
        ):
            return False
        if re.search(r"\b(question|doubt)\b", normalized):
            return True
        if re.search(r"\b(explain|discussed|mentioned)\b.*\bslide\b", normalized):
            return True
        if re.search(r"\babout\s+(?:the\s+)?slide\b", normalized):
            return True
    return False


# --- Navigation cores ---

_NEXT_PATTERNS = (
    re.compile(r"^next\s+slide$"),
    re.compile(
        r"^(?:go|move|skip|continue|jump|proceed)(?:\s+(?:ahead|forward|on))?"
        r"(?:\s+to)?\s+(?:the\s+)?next\s+slide$"
    ),
    re.compile(r"^move\s+on(?:\s+to\s+(?:the\s+)?next\s+slide)?$"),
    re.compile(r"^move\s+forward(?:\s+to\s+(?:the\s+)?next\s+slide)?$"),
    re.compile(r"^continue\s+to\s+(?:the\s+)?next\s+slide$"),
    re.compile(r"^proceed\s+to\s+(?:the\s+)?next\s+slide$"),
    re.compile(r"^skip\s+to\s+(?:the\s+)?next\s+slide$"),
    re.compile(r"^ready\s+to\s+move\s+forward$"),
    re.compile(r"^i'?m\s+ready\s+to\s+move\s+forward$"),
)

_PREV_PATTERNS = (
    re.compile(r"^(?:previous|prev)\s+slide$"),
    re.compile(
        r"^(?:go|move|jump|take me|return)\s+back(?:\s+to)?\s+(?:the\s+)?"
        r"(?:previous|prev|last)\s+slide$"
    ),
    re.compile(r"^please\s+return\s+to\s+(?:the\s+)?(?:previous|prev|last)\s+slide$"),
    re.compile(r"^return\s+to\s+(?:the\s+)?(?:previous|prev|last)\s+slide$"),
    re.compile(r"^jump\s+back\s+to\s+(?:the\s+)?(?:previous|prev|last)\s+slide$"),
    re.compile(r"^(?:go|take me)\s+back\s+(?:one\s+)?(?:slide|one)$"),
    re.compile(r"^back\s+(?:one\s+)?slide$"),
    re.compile(r"^take\s+me\s+back\s+one\s+slide$"),
    re.compile(r"^go\s+back\s+one\s+slide$"),
)

_GOTO_PATTERNS = (
    re.compile(
        r"^(?:go|move|skip|jump|proceed|return|take me)"
        r"(?:\s+back)?(?:\s+ahead)?(?:\s+to)?\s+"
        r"(?:the\s+)?slide\s+(?:number\s+)?(\w+)$"
    ),
    re.compile(
        r"^(?:go|move|jump|return)\s+back\s+to\s+(?:the\s+)?slide\s+(?:number\s+)?(\w+)$"
    ),
    re.compile(r"^return\s+to\s+(?:the\s+)?slide\s+(?:number\s+)?(\w+)$"),
    re.compile(r"^skip\s+ahead\s+to\s+(?:the\s+)?slide\s+(?:number\s+)?(\w+)$"),
    re.compile(
        r"^(?:go|move|skip|jump|proceed|take me)(?:\s+ahead)?(?:\s+to)?\s+"
        r"(?:the\s+)?(\w+)\s+slide$"
    ),
    re.compile(r"^take\s+me\s+to\s+(?:the\s+)?slide\s+(\w+)$"),
    re.compile(r"^go\s+through\s+slide\s+(\w+)$"),
    re.compile(r"^i\s+want\s+to\s+go\s+(?:through\s+)?(?:to\s+)?slide\s+(\w+)$"),
    # Short direct command only (entire candidate): "slide 8"
    re.compile(r"^slide\s+(?:number\s+)?(\w+)$"),
)

_RESTART_SLIDE_PATTERNS = (
    re.compile(r"^(?:repeat|restart)\s+(?:this\s+|the\s+|current\s+)?(?:slide|one)$"),
    re.compile(r"^start\s+(?:this\s+|the\s+)?slide\s+again$"),
    re.compile(r"^start\s+this\s+(?:one|slide)\s+again$"),
)

_RETURN_ORIGIN_PATTERNS = (
    re.compile(r"^return\s+to\s+where\s+we\s+were$"),
    re.compile(r"^go\s+back\s+to\s+(?:the\s+)?(?:slide\s+)?we\s+were\s+on$"),
    re.compile(r"^go\s+back\s+to\s+where\s+we\s+were$"),
    re.compile(r"^lets?\s+continue\s+from\s+where\s+we\s+left\s+off$"),
    re.compile(r"^continue\s+from\s+where\s+we\s+left\s+off$"),
    re.compile(r"^then\s+we\s+can\s+come\s+back(?:\s+over)?\s+here$"),
    re.compile(r"^we\s+can\s+come\s+back(?:\s+over)?\s+here$"),
    re.compile(r"^come\s+back(?:\s+over)?\s+here$"),
    re.compile(r"^return\s+to\s+(?:the\s+)?previous\s+location$"),
)

_SUSPECTED_NAV = re.compile(
    r"\b(?:go|move|skip|jump|proceed|take me|return)\b.*\bslide\b"
    r"|\b(?:next|previous|prev)\s+slide\b"
)

_ACK_PATTERNS = (
    re.compile(r"^i\s+got\s+(?:it|that|that\s+point)$"),
    re.compile(r"^got\s+(?:it|that)$"),
    re.compile(r"^understood$"),
    re.compile(r"^that\s+makes\s+sense$"),
    re.compile(r"^i\s+understand(?:\s+that(?:\s+point)?)?$"),
    re.compile(r"^okay\s+i\s+understand(?:\s+continue)?$"),
    re.compile(r"^ok\s+i\s+understand(?:\s+continue)?$"),
    re.compile(r"^i\s+understand\s+that\s+point$"),
)

_AFFIRM_PATTERNS = (
    re.compile(r"^yes$"),
    re.compile(r"^yeah$"),
    re.compile(r"^yep$"),
    re.compile(r"^yup$"),
    re.compile(r"^ok$"),
    re.compile(r"^okay$"),
)

_CONTINUE_PATTERNS = (
    re.compile(r"^continue$"),
    re.compile(r"^please\s+continue$"),
    re.compile(r"^continue\s+please$"),
    re.compile(r"^you\s+can\s+continue$"),
    re.compile(r"^you\s+may\s+continue$"),
    re.compile(r"^yes\s*,?\s*continue$"),
    re.compile(r"^yes\s*,?\s*please\s+continue$"),
    re.compile(r"^okay\s*,?\s*(?:you\s+can\s+)?continue$"),
    re.compile(r"^ok\s*,?\s*(?:you\s+can\s+)?continue$"),
    re.compile(r"^okay\s*,?\s*move\s+on$"),
    re.compile(r"^ok\s*,?\s*move\s+on$"),
    re.compile(r"^lets?\s+continue$"),
    re.compile(r"^go\s+ahead$"),
    re.compile(r"^please\s+go\s+ahead$"),
    re.compile(r"^carry\s+on$"),
    re.compile(r"^please\s+carry\s+on$"),
    re.compile(r"^keep\s+going$"),
    re.compile(r"^move\s+on$"),
    re.compile(r"^we\s+can\s+move\s+on$"),
    re.compile(r"^lets?\s+move\s+on$"),
    re.compile(r"^proceed$"),
    re.compile(r"^you\s+can\s+proceed$"),
    re.compile(r"^please\s+proceed$"),
    re.compile(r"^yes\s*,?\s*please\s+proceed$"),
    re.compile(r"^resume$"),
    re.compile(r"^continue\s+from\s+where\s+you\s+stopped$"),
    re.compile(r"^continue\s+from\s+where\s+you\s+left\s+off$"),
    re.compile(r"^no\s+questions?\s*,?\s*(?:please\s+)?continue$"),
    re.compile(r"^no\s+further\s+questions?\s*,?\s*(?:please\s+)?continue$"),
    re.compile(r"^no\s+more\s+questions?\s*,?\s*(?:please\s+)?(?:continue|go\s+ahead)?$"),
    re.compile(r"^that\s+is\s+clear\s*,?\s*(?:please\s+)?continue$"),
    re.compile(r"^thats\s+clear\s*,?\s*(?:please\s+)?continue$"),
    re.compile(r"^that's\s+clear\s*,?\s*(?:please\s+)?continue$"),
    re.compile(r"^i'?m\s+ready\s+to\s+continue$"),
    re.compile(r"^ready\s+to\s+continue$"),
)

_CONTINUE_BLOCK = (
    re.compile(r"\bi\s+don'?t\s+want\s+to\s+continue\b"),
    re.compile(r"\bdon'?t\s+continue(?:\s+yet)?\b"),
    re.compile(r"\bdo\s+not\s+continue\b"),
    re.compile(r"\bbefore\s+we\s+continue\b"),
    re.compile(r"\banother\s+question\s+before\b"),
    re.compile(r"\bquestion\s+before\s+(?:we\s+)?(?:continu|moving\s+on|move\s+on)\b"),
    re.compile(r"\bexplain\b.*\bbefore\s+(?:we\s+)?continu"),
    re.compile(r"\brepeat\b.*\bbefore\s+(?:we\s+)?continu"),
    re.compile(r"\bbefore\s+(?:we\s+)?(?:move\s+on|moving\s+on)\b"),
)

_REPEAT_PATTERNS = (
    re.compile(r"^repeat\s+that$"),
    re.compile(r"^repeat\s+this\s+line$"),
    re.compile(r"^say\s+that\s+again$"),
    re.compile(r"^repeat\s+the\s+last\s+point$"),
    re.compile(r"^can\s+you\s+repeat\s+(?:this\s+line|that|the\s+last\s+point)$"),
    re.compile(r"^please\s+replay\s+that\s+section$"),
    re.compile(r"^replay\s+that\s+section$"),
)

_QA_AMBIGUOUS_NO = (
    re.compile(r"^no$"),
    re.compile(r"^nope$"),
    re.compile(r"^not\s+yet$"),
    re.compile(r"^not\s+that\s+one$"),
    re.compile(r"^i\s+don'?t\s+want\b"),
    re.compile(r"^no\s*,?\s*i\s+would\s+not\b"),
    re.compile(r"^no\s*,?\s*explain\b"),
    re.compile(r"^no\s*,?\s*not\s+that\b"),
)

_QA_COMPLETE_PATTERNS = (
    re.compile(r"^that'?s\s+all$"),
    re.compile(r"^that'?s\s+all\s+from\s+my\s+end$"),
    re.compile(r"^no\s+more\s+questions$"),
    re.compile(r"^i\s+have\s+no\s+further\s+questions$"),
    re.compile(r"^i'?m\s+done$"),
    re.compile(r"^we\s+can\s+finish$"),
    re.compile(r"^you\s+can\s+end\s+the\s+session$"),
    re.compile(r"^thank\s+you\s*,?\s*that\s+will\s+be\s+all$"),
    re.compile(r"^no\s*,?\s*i\s+guess\s+that'?s\s+all(?:\s+from\s+my\s+end)?$"),
    re.compile(r"^i\s+guess\s+that'?s\s+all(?:\s+from\s+my\s+end)?$"),
)


def _match_any(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(p.fullmatch(text) for p in patterns)


def _try_goto(candidate: str) -> Optional[int]:
    for pattern in _GOTO_PATTERNS:
        match = pattern.fullmatch(candidate)
        if not match:
            continue
        number = _parse_slide_number(match.group(1))
        if number is None:
            continue
        return number
    return None


def _extract_mentioned_slide(normalized: str) -> Optional[int]:
    m = re.search(
        r"\bslide\s+(?:number\s+)?(\w+)\b|\b(?:the\s+)?(\w+)\s+slide\b",
        normalized,
    )
    if not m:
        return None
    token = m.group(1) or m.group(2)
    return _parse_slide_number(token)


def _is_short_direct_slide_command(candidate: str) -> bool:
    return bool(
        re.fullmatch(r"slide\s+(?:number\s+)?\w+", candidate)
        or re.fullmatch(r"(?:next|previous|prev)\s+slide", candidate)
    )


def parse_navigation_intent(
    text: str,
    *,
    slide_count: Optional[int] = None,
) -> Optional[VoiceNavIntent]:
    """Return a navigation intent for clear commands only; else None."""
    raw = (text or "").strip()
    normalized = normalize_control_text(raw)
    if not normalized or _is_blocked_question(normalized) or _is_negated(normalized):
        return None
    if _content_slide_reference_nouns(normalized):
        return None

    core = _extract_command_candidate(normalized)
    candidates = []
    for item in (core, soft_strip_control_wrappers(normalized), normalized):
        if item and item not in candidates:
            candidates.append(item)

    for candidate in candidates:
        if _is_blocked_question(candidate) or _is_negated(candidate):
            continue
        if _content_slide_reference_nouns(candidate):
            continue
        if _match_any(_NEXT_PATTERNS, candidate):
            return VoiceNavIntent(action=VoiceNavAction.NEXT, raw=raw)
        if _match_any(_PREV_PATTERNS, candidate):
            return VoiceNavIntent(action=VoiceNavAction.PREVIOUS, raw=raw)
        if _match_any(_RESTART_SLIDE_PATTERNS, candidate):
            return VoiceNavIntent(action=VoiceNavAction.REPEAT, raw=raw)
        number = _try_goto(candidate)
        if number is not None:
            # Bare "slide N" only when the whole candidate is that short command
            # or the original utterance has a clear movement verb.
            if re.fullmatch(r"slide\s+(?:number\s+)?\w+", candidate):
                if not (
                    _is_short_direct_slide_command(candidate)
                    and (
                        _CLEAR_NAV_VERB.search(normalized)
                        or soft_strip_control_wrappers(normalized) == candidate
                        or normalized == candidate
                        or _is_short_direct_slide_command(
                            soft_strip_control_wrappers(normalized)
                        )
                    )
                ):
                    # Allow "slide 8 please" after soft-strip.
                    stripped = soft_strip_control_wrappers(normalized)
                    if stripped != candidate and not _is_short_direct_slide_command(
                        stripped
                    ):
                        continue
            if slide_count is not None and (number < 1 or number > slide_count):
                return None
            return VoiceNavIntent(
                action=VoiceNavAction.GOTO,
                slide_index=number - 1,
                raw=raw,
            )
    return None


def parse_classroom_control(
    text: str,
    *,
    slide_count: Optional[int] = None,
) -> Optional[ClassroomControlIntent]:
    """Return a classroom-control intent, or None for the conversational path."""
    raw = (text or "").strip()
    normalized = normalize_control_text(raw)
    if not normalized:
        return None

    if _is_blocked_question(normalized):
        return None

    if any(p.search(normalized) for p in _CONTINUE_BLOCK):
        if _is_negated(normalized):
            return ClassroomControlIntent(
                kind=ClassroomControlKind.STAY,
                raw=raw,
                clarify_prompt="Okay, we'll stay on this slide.",
            )
        return None

    if _is_negated(normalized):
        return ClassroomControlIntent(
            kind=ClassroomControlKind.STAY,
            raw=raw,
            clarify_prompt="Okay, we'll stay on this slide.",
        )

    # Question reference about another slide — before nav extraction.
    ref_match = _QUESTION_REFERENCE.search(normalized)
    if ref_match and not _CLEAR_NAV_VERB.search(normalized):
        token = ref_match.group(1) or ref_match.group(2)
        number = _parse_slide_number(token or "")
        if number is not None:
            if slide_count is not None and (number < 1 or number > slide_count):
                return ClassroomControlIntent(
                    kind=ClassroomControlKind.CLARIFY,
                    raw=raw,
                    suspected_slide_number=number,
                    clarify_prompt=(
                        f"I only have slides 1 through {slide_count}. "
                        "Which slide is your question about?"
                    ),
                )
            return ClassroomControlIntent(
                kind=ClassroomControlKind.QUESTION_REFERENCE,
                raw=raw,
                reference_slide_1based=number,
            )

    if _content_slide_reference_nouns(normalized) and not _CLEAR_NAV_VERB.search(
        normalized
    ):
        return None

    core = _extract_command_candidate(normalized)
    candidates = []
    for item in (core, soft_strip_control_wrappers(normalized), normalized):
        if item and item not in candidates:
            candidates.append(item)

    for candidate in candidates:
        if any(p.fullmatch(candidate) for p in _QA_AMBIGUOUS_NO):
            return None
        if _match_any(_CONTINUE_PATTERNS, candidate):
            return ClassroomControlIntent(
                kind=ClassroomControlKind.CONTINUE,
                raw=raw,
            )
        if _match_any(_QA_COMPLETE_PATTERNS, candidate):
            return ClassroomControlIntent(
                kind=ClassroomControlKind.QA_COMPLETION,
                raw=raw,
            )
        if _match_any(_QUESTION_PREAMBLE, candidate):
            return ClassroomControlIntent(
                kind=ClassroomControlKind.QUESTION_PREAMBLE,
                raw=raw,
            )

    for candidate in candidates:
        if _match_any(_ACK_PATTERNS, candidate):
            return ClassroomControlIntent(
                kind=ClassroomControlKind.ACKNOWLEDGE,
                raw=raw,
            )
        if _match_any(_AFFIRM_PATTERNS, candidate):
            return ClassroomControlIntent(
                kind=ClassroomControlKind.AFFIRM,
                raw=raw,
            )
        if _match_any(_REPEAT_PATTERNS, candidate):
            return ClassroomControlIntent(
                kind=ClassroomControlKind.REPEAT,
                raw=raw,
            )
        if _match_any(_RETURN_ORIGIN_PATTERNS, candidate):
            return ClassroomControlIntent(
                kind=ClassroomControlKind.RETURN_ORIGIN,
                raw=raw,
            )

    nav = parse_navigation_intent(raw, slide_count=slide_count)
    if nav is not None:
        return ClassroomControlIntent(
            kind=ClassroomControlKind.NAVIGATION,
            navigation=nav,
            raw=raw,
        )

    for candidate in candidates:
        number = _try_goto(candidate)
        if number is not None and slide_count is not None and number > slide_count:
            return ClassroomControlIntent(
                kind=ClassroomControlKind.CLARIFY,
                raw=raw,
                suspected_slide_number=number,
                clarify_prompt=(
                    f"I only have slides 1 through {slide_count}. "
                    f"Which slide should I go to?"
                ),
            )

    # Command-like but unresolved — only with a clear nav verb (no false promises).
    if _SUSPECTED_NAV.search(normalized) and _CLEAR_NAV_VERB.search(normalized):
        mentioned = _extract_mentioned_slide(normalized)
        if mentioned is not None and (
            slide_count is None or 1 <= mentioned <= slide_count
        ):
            # Prefer executing when a clear verb + in-range number are present.
            return ClassroomControlIntent(
                kind=ClassroomControlKind.NAVIGATION,
                navigation=VoiceNavIntent(
                    action=VoiceNavAction.GOTO,
                    slide_index=mentioned - 1,
                    raw=raw,
                ),
                raw=raw,
            )
        prompt = (
            "Did you want me to change slides? "
            "You can say next slide, previous slide, or go to slide 3."
        )
        return ClassroomControlIntent(
            kind=ClassroomControlKind.CLARIFY,
            raw=raw,
            suspected_slide_number=mentioned,
            clarify_prompt=prompt,
        )

    return None


def parse_voice_navigation(text: str) -> Optional[VoiceNavIntent]:
    return parse_navigation_intent(text)
