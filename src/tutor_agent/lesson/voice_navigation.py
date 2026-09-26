"""Deterministic spoken lesson navigation intents.

Enums live here for stable imports. Parsing is implemented in
``classroom_control`` (broader natural-language variants).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class VoiceNavAction(str, Enum):
    NEXT = "next"
    PREVIOUS = "previous"
    GOTO = "goto"
    REPEAT = "repeat"


@dataclass(frozen=True)
class VoiceNavIntent:
    action: VoiceNavAction
    slide_index: Optional[int] = None  # zero-based for GOTO
    raw: str = ""


def parse_voice_navigation(text: str) -> Optional[VoiceNavIntent]:
    """Return a navigation intent for clear commands only; else None."""
    from tutor_agent.lesson.classroom_control import parse_navigation_intent

    return parse_navigation_intent(text)
