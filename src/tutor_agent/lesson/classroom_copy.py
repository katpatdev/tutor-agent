"""Centralized student-facing classroom copy (application-owned)."""

from __future__ import annotations

from tutor_agent.lesson.curriculum import slide_title


def topic_for_slide(slide_0based: int) -> str:
    try:
        return slide_title(slide_0based).strip()
    except Exception:  # noqa: BLE001
        return f"slide {slide_0based + 1}"


def mid_slide_followup(slide_0based: int) -> str:
    n = slide_0based + 1
    return (
        f"Should I continue with slide {n}, or do you have another question?"
    )


def checkpoint_first(slide_0based: int) -> str:
    return (
        f"We've just covered {topic_for_slide(slide_0based)}. "
        "Shall we move on, or do you have any questions?"
    )


def checkpoint_followup() -> str:
    return "Do you have another question, or shall we move on?"


def resume_bridge(slide_0based: int) -> str:
    n = slide_0based + 1
    return f"Okay, continuing with slide {n} from where we left off."


def advance_bridge(slide_0based: int) -> str:
    n = slide_0based + 1
    return f"Okay, let's move on to slide {n}: {topic_for_slide(slide_0based)}."


def direct_nav_bridge(slide_0based: int) -> str:
    n = slide_0based + 1
    return f"Okay, moving to slide {n}: {topic_for_slide(slide_0based)}."


def return_bridge(slide_0based: int) -> str:
    n = slide_0based + 1
    return f"Okay, returning to slide {n}, where we left off."


CHECKPOINT_REMINDER = (
    "When you're ready, say continue to move on, or ask a question."
)
POST_ANSWER_REMINDER = (
    "Say continue when you're ready, or ask another question."
)
QUESTION_PREAMBLE = "Of course. What would you like to ask?"
STAY_ACK = "Okay, we'll stay on this slide."
SLIDE1_TO_SLIDE2 = "Let's begin with what natural disasters are."
