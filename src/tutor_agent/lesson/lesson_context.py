"""Bounded authoritative lesson context for content-question LLM turns.

Derived only from application state. Never overrides slide index ownership.
No extra OpenAI calls.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

LESSON_CONTEXT_BEGIN = "<<<LESSON_CONTEXT>>>"
LESSON_CONTEXT_END = "<<<END_LESSON_CONTEXT>>>"


def is_lesson_context_message(content: str) -> bool:
    text = content or ""
    return LESSON_CONTEXT_BEGIN in text and LESSON_CONTEXT_END in text


def strip_lesson_context_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Remove prior temporary lesson-context system messages."""
    cleaned: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "system" and is_lesson_context_message(
            str(msg.get("content") or "")
        ):
            continue
        cleaned.append(dict(msg))
    return cleaned


def build_lesson_context_snapshot(
    *,
    current_slide_1based: int,
    slide_count: int,
    mode: str,
    interrupted_slide_1based: Optional[int] = None,
    active_segment_1based: Optional[int] = None,
    total_segments: Optional[int] = None,
    visited_slides_1based: Sequence[int] = (),
    verified_completed_segments: Sequence[int] = (),
    user_acknowledged_segments: Sequence[int] = (),
    pending_resume: bool = False,
    return_origin_slide_1based: Optional[int] = None,
    post_answer_hold: bool = False,
    slide_checkpoint: bool = False,
    question_reference_slide_1based: Optional[int] = None,
    latest_student_question: str = "",
) -> str:
    """Compact, content-free-ish snapshot (no full narration text)."""
    visited = ", ".join(str(s) for s in visited_slides_1based) or "none yet"
    verified = ", ".join(str(s) for s in verified_completed_segments) or "none"
    acked = ", ".join(str(s) for s in user_acknowledged_segments) or "none"
    lines = [
        LESSON_CONTEXT_BEGIN,
        "Authoritative lesson state (application-owned; do not invent slide numbers):",
        f"- Current slide: {current_slide_1based} of {slide_count}",
        f"- Lesson mode: {mode}",
        f"- Visited slides: {visited}",
    ]
    if interrupted_slide_1based is not None:
        lines.append(f"- Interrupted from slide: {interrupted_slide_1based}")
    if active_segment_1based is not None and total_segments is not None:
        lines.append(
            f"- Active narration segment: {active_segment_1based} of {total_segments}"
        )
    lines.append(f"- Segments verified audibly completed (indexes): {verified}")
    lines.append(
        f"- Segments acknowledged by student (not verified heard): {acked}"
    )
    lines.append(f"- Pending narration resume: {'yes' if pending_resume else 'no'}")
    if return_origin_slide_1based is not None:
        lines.append(
            f"- Saved return point (one-level detour): slide {return_origin_slide_1based}"
        )
    if post_answer_hold:
        lines.append(
            "- Waiting after an answer: do not claim narration resumed or slides changed"
        )
    if slide_checkpoint:
        lines.append(
            "- At slide checkpoint: waiting for questions or continue; do not invent "
            "a slide change"
        )
    if question_reference_slide_1based is not None:
        lines.append(
            f"- Student question refers to slide {question_reference_slide_1based} "
            "(do not change the current authoritative slide)"
        )
    if latest_student_question.strip():
        # Bound question length to keep context small.
        q = latest_student_question.strip()
        if len(q) > 240:
            q = q[:237] + "..."
        lines.append(f"- Latest student question: {q}")
    lines.extend(
        [
            "Rules: Never claim you moved slides or resumed narration unless this "
            "snapshot already shows that state. Answer the student's question only in "
            "two to four concise sentences. Do not append filler such as "
            "'feel free to ask' or 'I'm here to help'—application code invites "
            "follow-ups. Application code owns slide changes.",
            LESSON_CONTEXT_END,
        ]
    )
    return "\n".join(lines)


def lesson_context_message_dict(snapshot: str) -> dict:
    return {"role": "system", "content": snapshot}
