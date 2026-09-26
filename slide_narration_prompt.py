"""Slide narration system instructions (scoped; not LLM-owned navigation).

Prior slide instructions are stripped before each new slide turn so only the
current slide curriculum controls narration. Base tutor/safety prompt and
ordinary conversational turns remain untouched.
"""

from __future__ import annotations

from typing import List

SLIDE_NARRATION_MARKER = "<<<SLIDE_NARRATION_INSTRUCTION>>>"


def format_slide_narration_instruction(
    *,
    slide_index: int,
    slide_count: int,
    curriculum_prompt: str,
) -> str:
    """Build a temporary system message for the current slide only.

    ``slide_index`` is zero-based; display uses one-based numbering.
    """
    if slide_index < 0 or slide_index >= slide_count:
        raise ValueError("slide_index out of range for slide narration instruction")
    body = (curriculum_prompt or "").strip()
    if not body:
        raise ValueError("curriculum_prompt must be non-empty")

    display = slide_index + 1
    return (
        f"{SLIDE_NARRATION_MARKER}\n"
        f"You are narrating slide {display} of {slide_count} right now. "
        "Application code owns the slide index; do not invent slide numbers or "
        "skip ahead. Teach the curriculum points below in connected, "
        "age-appropriate spoken sentences with concrete examples from the "
        "curriculum. Cover the listed ideas meaningfully rather than only "
        "restating the slide title.\n\n"
        "Style: a few natural sentences that develop one continuous explanation "
        "for this slide. Slide 1 may be a short welcome/overview; slide 8 may "
        "recap and invite questions. Do not mechanically use the same sentence "
        "count on every slide. Avoid restarting successive slides with the same "
        "opener (for example, do not begin every slide with “Natural disasters…”). "
        "Do not add filler transitions such as “and now on the next slide.” "
        "Avoid unnecessary fear and unsupported facts.\n\n"
        "The tutor base prompt’s one-or-two-sentence brevity applies to student "
        "questions and Q&A answers, not to this slide narration turn.\n\n"
        f"{body}\n"
        f"{SLIDE_NARRATION_MARKER}"
    )


def strip_slide_narration_instructions(messages: list) -> list:
    """Remove prior temporary slide-narration system messages from LLM context."""
    cleaned: List = []
    for msg in messages:
        if not isinstance(msg, dict):
            cleaned.append(msg)
            continue
        content = msg.get("content")
        if (
            msg.get("role") == "system"
            and isinstance(content, str)
            and SLIDE_NARRATION_MARKER in content
        ):
            continue
        cleaned.append(msg)
    return cleaned


def is_slide_narration_instruction(content: str) -> bool:
    return isinstance(content, str) and SLIDE_NARRATION_MARKER in content
