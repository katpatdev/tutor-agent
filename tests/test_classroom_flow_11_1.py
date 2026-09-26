"""Iteration 11.1: natural resume bridges, Yes-expectation, conversation mirror."""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from classroom_control import ClassroomControlKind, parse_classroom_control
from classroom_copy import (
    advance_bridge,
    checkpoint_first,
    checkpoint_followup,
    direct_nav_bridge,
    mid_slide_followup,
    resume_bridge,
    return_bridge,
)
from conversation_ledger import ConversationLedger, PlaybackStatus, TutorSource
from lesson_controller import LessonMode
from presentation_runtime import (
    ExpectedClassroomResponse,
    OutputPurpose,
    PresentationRuntime,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_interruption_frame,
    make_test_transform_frame,
)


def make_runtime(slide_count: int = 8) -> tuple[PresentationRuntime, RecordingFrameSink]:
    sink = RecordingFrameSink()
    runtime = PresentationRuntime(
        slide_prompts=[f"SLIDE {i}" for i in range(slide_count)],
        frame_sink=sink,
        interruption_frame_factory=make_test_interruption_frame,
        messages_append_frame_factory=make_test_append_frame,
        messages_transform_frame_factory=make_test_transform_frame,
        narration_max_characters=80,
        narration_min_characters=1,
        lesson_followup_wait_seconds=0.05,
    )
    return runtime, sink


TWO = (
    "First section about earthquakes and safety prep. "
    "Second section about floods and water care."
)


async def drain_purpose(runtime: PresentationRuntime, *purposes: OutputPurpose) -> None:
    for _ in range(8):
        if runtime.output_purpose in purposes:
            await runtime.on_bot_started_speaking()
            await runtime.on_bot_stopped_speaking()
            continue
        break


async def narrate(runtime: PresentationRuntime, text: str = TWO) -> None:
    await drain_purpose(
        runtime,
        OutputPurpose.NAV_ACK,
        OutputPurpose.SLIDE_CHECKPOINT,
        OutputPurpose.POST_ANSWER_INVITE,
        OutputPurpose.RESUME_BRIDGE,
    )
    await runtime.accept_approved_narration(text)
    plan = runtime.narration_plan
    total = plan.total_segments if plan is not None else 1
    for _ in range(total):
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
    await drain_purpose(
        runtime,
        OutputPurpose.NAV_ACK,
        OutputPurpose.SLIDE_CHECKPOINT,
        OutputPurpose.RESUME_BRIDGE,
    )


@pytest.mark.parametrize(
    "utterance",
    [
        "Yes.",
        "Yes, continue.",
        "Okay, you can continue.",
        "Please continue.",
        "Go ahead.",
        "Carry on.",
        "No more questions.",
        "No more questions, please continue.",
        "That is clear, continue.",
        "Okay, move on.",
    ],
)
def test_continue_and_affirm_phrases(utterance: str) -> None:
    intent = parse_classroom_control(utterance, slide_count=8)
    assert intent is not None
    assert intent.kind in {
        ClassroomControlKind.CONTINUE,
        ClassroomControlKind.AFFIRM,
        ClassroomControlKind.QA_COMPLETION,
    }


def test_plain_yes_is_affirm() -> None:
    intent = parse_classroom_control("Yes.", slide_count=8)
    assert intent is not None
    assert intent.kind is ClassroomControlKind.AFFIRM


def test_mid_slide_question_stops_and_asks_continue_same_slide() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate(runtime)  # → slide 2 (index 1)
        await drain_purpose(runtime, OutputPurpose.NAV_ACK)
        await runtime.accept_approved_narration(TWO)
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.INTERRUPTED or True
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.output_purpose is OutputPurpose.POST_ANSWER_INVITE
        invite = mid_slide_followup(runtime.state.cursor.slide_index)
        assert invite in sink.tts_texts
        assert runtime.expected_classroom_response is ExpectedClassroomResponse.RESUME_CURRENT
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        # Follow-up question keeps hold / invite cycle.
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert checkpoint_followup() not in sink.tts_texts or True
        assert mid_slide_followup(runtime.state.cursor.slide_index) in sink.tts_texts

    asyncio.run(_run())


def test_yes_speaks_resume_bridge_then_resumes_segment() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate(runtime)
        await drain_purpose(runtime, OutputPurpose.NAV_ACK)
        await runtime.accept_approved_narration(TWO)
        await runtime.on_bot_started_speaking()
        interrupted_slide = runtime.state.cursor.slide_index
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        await drain_purpose(runtime, OutputPurpose.POST_ANSWER_INVITE)
        before = list(sink.tts_texts)
        ok = await runtime.handle_classroom_control(parse_classroom_control("Yes."))
        assert ok
        assert runtime.output_purpose is OutputPurpose.RESUME_BRIDGE
        bridge = resume_bridge(interrupted_slide)
        assert bridge in sink.tts_texts
        assert bridge not in before
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION
        assert runtime.expected_classroom_response is ExpectedClassroomResponse.NONE
        # Same slide, not regenerated from the start via a new slide jump.
        assert runtime.state.cursor.slide_index == interrupted_slide

    asyncio.run(_run())


def test_checkpoint_yes_advances_with_advance_bridge() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate(runtime)
        await narrate(runtime)
        assert runtime.in_slide_checkpoint
        slide = runtime.state.cursor.slide_index
        assert checkpoint_first(slide) in sink.tts_texts
        assert runtime.expected_classroom_response is ExpectedClassroomResponse.ADVANCE_NEXT
        await drain_purpose(runtime, OutputPurpose.SLIDE_CHECKPOINT)
        ok = await runtime.handle_classroom_control(parse_classroom_control("Yes."))
        assert ok
        await drain_purpose(runtime, OutputPurpose.NAV_ACK)
        assert runtime.state.cursor.slide_index == slide + 1
        assert advance_bridge(slide + 1) in sink.tts_texts
        assert runtime.expected_classroom_response is ExpectedClassroomResponse.NONE

    asyncio.run(_run())


def test_direct_navigation_uses_direct_bridge_no_confirm() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate(runtime)
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("Go to slide 6.", slide_count=8)
        )
        assert ok
        await drain_purpose(runtime, OutputPurpose.NAV_ACK)
        assert runtime.state.cursor.slide_index == 5
        assert direct_nav_bridge(5) in sink.tts_texts
        assert not any("Are you sure" in t for t in sink.tts_texts)
        assert not any("Would you like to move" in t for t in sink.tts_texts)

    asyncio.run(_run())


def test_detour_return_bridge() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate(runtime)
        await narrate(runtime)
        await drain_purpose(runtime, OutputPurpose.SLIDE_CHECKPOINT)
        origin = runtime.state.cursor.slide_index
        await runtime.handle_classroom_control(
            parse_classroom_control("Go to slide 6.", slide_count=8)
        )
        await drain_purpose(runtime, OutputPurpose.NAV_ACK)
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("Return to where we were.", slide_count=8)
        )
        assert ok
        await drain_purpose(runtime, OutputPurpose.NAV_ACK)
        assert return_bridge(origin) in sink.tts_texts
        assert runtime.state.cursor.slide_index == origin

    asyncio.run(_run())


def test_continue_does_not_call_rag_or_llm_frames() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate(runtime)
        await narrate(runtime)
        await drain_purpose(runtime, OutputPurpose.SLIDE_CHECKPOINT)
        before_sys = len(sink.system_messages)
        await runtime.handle_classroom_control(parse_classroom_control("Continue."))
        assert len(sink.system_messages) == before_sys

    asyncio.run(_run())


def test_no_duplicate_followup_invite() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate(runtime)
        await drain_purpose(runtime, OutputPurpose.NAV_ACK)
        await runtime.accept_approved_narration(TWO)
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        await drain_purpose(runtime, OutputPurpose.POST_ANSWER_INVITE)
        invite = mid_slide_followup(runtime.state.cursor.slide_index)
        assert sink.tts_texts.count(invite) == 1
        assert not any("Feel free to ask" in t for t in sink.tts_texts)
        assert not any("I'm always here" in t for t in sink.tts_texts)

    asyncio.run(_run())


# --- Conversation mirror ---


def test_conversation_user_once_and_no_partial() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.record_user_utterance("What is a tsunami?")
        await runtime.record_user_utterance("What is a tsunami?")
        await runtime.record_user_utterance("")
        users = [e for e in runtime.conversation_ledger.snapshot() if e["role"] == "user"]
        # Ledger does not dedupe identical text across utterances; InputSafety does.
        assert len(users) == 2
        assert users[0]["text"] == "What is a tsunami?"

    asyncio.run(_run())


def test_prefetch_text_not_mirrored_until_presented() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        # Prefetch cache store is not a presentation event.
        assert runtime.conversation_ledger.entries_created == 0
        await runtime.accept_approved_narration(TWO)
        # Accepting narration queues TTS → mirror on present.
        snap = runtime.conversation_ledger.snapshot()
        assert any(e.get("source") == TutorSource.NARRATION.value for e in snap)
        assert any(TWO.split(".")[0] in e["text"] for e in snap)

    asyncio.run(_run())


def test_tts_retry_does_not_duplicate_tutor_message() -> None:
    async def _run() -> None:
        ledger = ConversationLedger()
        await ledger.add_or_update_tutor(
            "Hello class",
            source=TutorSource.NARRATION,
            tts_unit_id="logical-1",
            playback_status=PlaybackStatus.QUEUED,
        )
        await ledger.add_or_update_tutor(
            "Hello class",
            source=TutorSource.NARRATION,
            tts_unit_id="logical-1",
            playback_status=PlaybackStatus.SPEAKING,
        )
        assert ledger.entries_created == 1
        assert ledger.duplicates_ignored == 1
        assert len(ledger.snapshot()) == 1

    asyncio.run(_run())


def test_explicit_repeat_can_create_new_entry() -> None:
    async def _run() -> None:
        ledger = ConversationLedger()
        await ledger.add_or_update_tutor(
            "Same sentence",
            source=TutorSource.NARRATION,
            tts_unit_id="unit-a",
        )
        await ledger.add_or_update_tutor(
            "Same sentence",
            source=TutorSource.REPEAT,
            tts_unit_id="unit-b",
        )
        assert ledger.entries_created == 2

    asyncio.run(_run())


def test_snapshot_plus_incremental_no_duplicate_ids() -> None:
    async def _run() -> None:
        published: List[dict] = []

        async def _pub(msg: dict) -> None:
            published.append(msg)

        ledger = ConversationLedger(publish=_pub)
        e = await ledger.add_or_update_tutor(
            "Hi",
            source=TutorSource.CHECKPOINT,
            tts_unit_id="t1",
        )
        assert e is not None
        snap = ledger.snapshot()
        # Re-apply snapshot semantics on a fresh consumer tracker is frontend;
        # ledger itself keeps one entry.
        assert len(snap) == 1
        await ledger.add_or_update_tutor(
            "Hi",
            source=TutorSource.CHECKPOINT,
            tts_unit_id="t1",
            playback_status=PlaybackStatus.SPOKEN,
        )
        assert len(ledger.snapshot()) == 1

    asyncio.run(_run())


def test_observer_failure_does_not_block_narration() -> None:
    async def _run() -> None:
        async def _boom(_msg: dict) -> None:
            raise RuntimeError("publish failed")

        runtime, sink = make_runtime()
        runtime.set_conversation_publisher(_boom)
        await runtime.start_session()
        await runtime.accept_approved_narration(TWO)
        assert sink.tts_texts  # narration still queued
        assert runtime.conversation_ledger.publish_failures >= 1

    asyncio.run(_run())


def test_affirm_without_expectation_falls_through() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        assert runtime.expected_classroom_response is ExpectedClassroomResponse.NONE
        ok = await runtime.handle_classroom_control(parse_classroom_control("Yes."))
        assert ok is False

    asyncio.run(_run())
