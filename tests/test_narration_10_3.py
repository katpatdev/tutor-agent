"""Iteration 10.3: slide narration coverage, segment packing, protocol ACK once.

Offline only — no /ws, OpenAI, or live microphone.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List
from unittest.mock import MagicMock

import pytest

from agent import LessonLifecycleObserver
from curriculum import SLIDES
from lesson_controller import LessonMode
from lesson_protocol import LessonProtocolSession, MSG_RESULT
from narration_plan import segment_narration_text
from presentation_runtime import (
    BASE_TUTOR_PROMPT,
    OutputPurpose,
    PresentationRuntime,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_interruption_frame,
    make_test_transform_frame,
)
from prompt_registry import load_active_tutor_prompt, sha256_text
from slide_narration_prompt import (
    SLIDE_NARRATION_MARKER,
    format_slide_narration_instruction,
    is_slide_narration_instruction,
    strip_slide_narration_instructions,
)


@dataclass
class FakeTTSSpeakFrame:
    text: str
    kind: str = "tts_speak"


class OutboundRecorder:
    def __init__(self) -> None:
        self.messages: List[Any] = []

    async def __call__(self, message: Any) -> None:
        self.messages.append(message)

    @property
    def lesson_payloads(self) -> List[dict]:
        return [m["data"] for m in self.messages if m.get("type") == "server-message"]


def make_runtime(
    *,
    slide_count: int = 8,
    max_characters: int = 320,
    min_characters: int = 110,
) -> tuple[PresentationRuntime, RecordingFrameSink]:
    prompts = [s.prompt for s in SLIDES[:slide_count]]
    if slide_count > len(SLIDES):
        prompts = [f"SLIDE {i}" for i in range(slide_count)]
    sink = RecordingFrameSink()
    runtime = PresentationRuntime(
        slide_prompts=prompts,
        frame_sink=sink,
        interruption_frame_factory=make_test_interruption_frame,
        messages_append_frame_factory=make_test_append_frame,
        messages_transform_frame_factory=make_test_transform_frame,
        narration_max_characters=max_characters,
        narration_min_characters=min_characters,
    )
    runtime.set_tts_speak_frame_factory(FakeTTSSpeakFrame)
    return runtime, sink


async def complete_active_segment(runtime: PresentationRuntime) -> None:
    await runtime.on_bot_started_speaking()
    await runtime.on_bot_stopped_speaking()


def test_tutor_v5_distinguishes_slide_narration_from_qa_brevity() -> None:
    loaded = load_active_tutor_prompt(environ={"TUTOR_PROMPT_VERSION": "v5"})
    text = loaded.text
    assert text == BASE_TUTOR_PROMPT
    assert "two to four" in text.lower()
    assert "never claim" in text.lower()
    assert "exact question" in text.lower()
    assert "feel free to ask" in text.lower()  # forbidden filler named in prompt
    assert "slide-narration" in text.lower() or "slide narration" in text.lower()
    assert "Q&A" in text or "q&a" in text.lower()
    # Curriculum coverage belongs on slide instructions, not Q&A length.
    instruction = format_slide_narration_instruction(
        slide_index=1,
        slide_count=8,
        curriculum_prompt=SLIDES[1].prompt,
    )
    assert "earthquakes" in instruction.lower()
    assert "one-or-two-sentence brevity applies to student" in instruction.lower()
    assert "Cover the listed ideas" in instruction or "cover the listed ideas" in instruction.lower()


def test_slide_instruction_strip_and_revisit_scopes_prior_slides() -> None:
    messages = [
        {"role": "system", "content": "base tutor prompt"},
        {
            "role": "system",
            "content": format_slide_narration_instruction(
                slide_index=0, slide_count=8, curriculum_prompt=SLIDES[0].prompt
            ),
        },
        {"role": "assistant", "content": "Welcome to our lesson."},
        {
            "role": "system",
            "content": format_slide_narration_instruction(
                slide_index=1, slide_count=8, curriculum_prompt=SLIDES[1].prompt
            ),
        },
    ]
    cleaned = strip_slide_narration_instructions(messages)
    assert cleaned[0]["content"] == "base tutor prompt"
    assert cleaned[1]["role"] == "assistant"
    assert all(not is_slide_narration_instruction(m.get("content", "")) for m in cleaned)
    assert SLIDE_NARRATION_MARKER not in str(cleaned)


def test_runtime_queues_transform_then_current_slide_only() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime(slide_count=3)
        await runtime.start_session()
        assert any(getattr(f, "kind", None) == "messages_transform" for f in sink.frames)
        assert is_slide_narration_instruction(sink.system_messages[-1])
        assert SLIDES[0].prompt in sink.system_messages[-1]

        await runtime.accept_approved_narration(
            "Welcome everyone to science class today. "
            "We will learn what natural disasters are and how people stay safer."
        )
        while runtime.narration_plan and not runtime.narration_plan.is_complete:
            await complete_active_segment(runtime)

        assert runtime.state.cursor.slide_index == 1
        assert is_slide_narration_instruction(sink.system_messages[-1])
        assert SLIDES[1].prompt in sink.system_messages[-1]
        # Prior raw curriculum alone is not appended; only wrapped instructions.
        assert sink.system_messages[0] != SLIDES[0].prompt

        await runtime.go_to_slide(0)
        assert is_slide_narration_instruction(sink.system_messages[-1])
        assert SLIDES[0].prompt in sink.system_messages[-1]
        transform_count = sum(
            1 for f in sink.frames if getattr(f, "kind", None) == "messages_transform"
        )
        assert transform_count >= 3  # start, advance, revisit

    asyncio.run(_run())


def test_short_sentences_pack_into_bounded_segments_without_reorder() -> None:
    text = (
        "Hello everyone! "
        "Natural events can become disasters when they harm people or homes. "
        "Earthquakes, floods, and hurricanes are clear examples students should know."
    )
    segments = segment_narration_text(text, max_characters=320, min_characters=110)
    joined = " ".join(s.text for s in segments)
    assert joined == " ".join(text.split())
    assert len(segments) >= 1
    # Tiny greeting must not stand alone as its own resume unit.
    assert segments[0].text.startswith("Hello everyone!")
    assert "Natural events" in segments[0].text
    assert all(len(s.text) <= 320 for s in segments)
    assert [s.index for s in segments] == list(range(len(segments)))


def test_pause_resume_during_grouped_segment_replays_same_text() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime(min_characters=80, max_characters=250)
        await runtime.start_session()
        await runtime.accept_approved_narration(
            "Hello everyone! "
            "Today we study how communities prepare for floods and storms together."
        )
        plan = runtime.narration_plan
        assert plan is not None and plan.total_segments >= 1
        first_text = plan.segments[0].text
        assert "Hello everyone!" in first_text
        await runtime.on_bot_started_speaking()
        await runtime.pause()
        assert runtime.state.mode is LessonMode.PAUSED
        sink.frames.clear()
        await runtime.resume()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION
        assert sink.tts_texts == [first_text]
        assert runtime.segment_replays >= 1

    asyncio.run(_run())


def test_interruption_then_answer_completion_replays_correct_segment() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime(min_characters=80, max_characters=250)
        await runtime.start_session()
        await runtime.accept_approved_narration(
            "Plate motion can shake the ground suddenly. "
            "Weather extremes can also cause floods over hours or days."
        )
        active = runtime.narration_plan.active_text()
        assert active
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        await runtime.on_bot_stopped_speaking()  # cancelled narration
        await runtime.on_bot_started_speaking()  # interruption answer
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.output_purpose is OutputPurpose.POST_ANSWER_INVITE
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        from classroom_control import parse_classroom_control
        assert await runtime.handle_classroom_control(parse_classroom_control("Continue."))
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION
        assert sink.tts_texts[-1] == active

    asyncio.run(_run())


def test_navigation_invalidates_pending_generation() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime(slide_count=3, min_characters=40, max_characters=120)
        await runtime.start_session()
        await runtime.accept_approved_narration(
            "Old slide content stays here as one longer teaching sentence for packing."
        )
        old = runtime.narration_plan
        await runtime.on_bot_started_speaking()
        await runtime.go_to_slide(2)
        assert old is not None and old.invalidated
        assert runtime.narration_plan is None
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.cursor.slide_index == 2
        await runtime.accept_approved_narration(
            "New slide content teaches volcanic activity and climate-related changes carefully."
        )
        assert runtime.narration_plan is not None
        assert runtime.narration_plan.generation_id != old.generation_id

    asyncio.run(_run())


def test_final_slide_one_qa_transition() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime(slide_count=1, min_characters=40, max_characters=80)
        await runtime.start_session()
        await runtime.accept_approved_narration(
            "First recap sentence covers preparedness and cooperation. "
            "Second invitation sentence asks students for their questions now."
        )
        assert runtime.narration_plan is not None
        while not runtime.narration_plan.is_complete:
            await complete_active_segment(runtime)
        assert runtime.state.mode is LessonMode.QA_MODE
        assert sum("open Q&A" in m for m in sink.system_messages) == 1

    asyncio.run(_run())


def test_observer_duplicate_hops_emit_one_command_ack() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime(slide_count=2)
        await runtime.start_session()
        outbound = OutboundRecorder()
        protocol = LessonProtocolSession(runtime, outbound)
        runtime.set_on_state_changed(protocol.publish_state)
        obs = MagicMock()
        obs.collector = MagicMock()
        observer = LessonLifecycleObserver(runtime, protocol, obs)

        from pipecat.frames.frames import InputTransportMessageFrame
        from pipecat.observers.base_observer import FramePushed
        from pipecat.processors.frame_processor import FrameDirection

        command = {
            "label": "rtvi-ai",
            "type": "client-message",
            "id": "p1",
            "data": {
                "t": "lesson.command",
                "d": {
                    "type": "lesson.command",
                    "version": 1,
                    "request_id": "pause-once",
                    "command": "pause",
                    "payload": {},
                },
            },
        }
        pause_frame = InputTransportMessageFrame(message=command)
        src = MagicMock(name="src")
        dst = MagicMock(name="dst")
        for _ in range(15):
            await observer.on_push_frame(
                FramePushed(
                    source=src,
                    destination=dst,
                    frame=pause_frame,
                    direction=FrameDirection.DOWNSTREAM,
                    timestamp=1,
                )
            )
        acks = [
            p
            for p in outbound.lesson_payloads
            if p.get("type") == MSG_RESULT and p.get("request_id") == "pause-once"
        ]
        assert len(acks) == 1
        assert acks[0].get("ok") is True

    asyncio.run(_run())


def test_prompt_registry_v2_hash_matches_file() -> None:
    loaded = load_active_tutor_prompt(environ={"TUTOR_PROMPT_VERSION": "v2"})
    assert loaded.record.version == "v2"
    assert loaded.content_hash == sha256_text(loaded.text)
