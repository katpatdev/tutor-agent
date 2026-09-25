"""Offline tests for PresentationRuntime (no OpenAI, network, or live Pipecat I/O)."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from lesson_controller import (
    InvalidLessonTransition,
    LessonController,
    LessonMode,
    NarrationCursor,
)
from presentation_runtime import (
    OutputPurpose,
    PresentationRuntime,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_interruption_frame,
)

SLIDES = [f"SLIDE {i} CONTENT" for i in range(8)]


def make_runtime(
    *, controller: LessonController | None = None
) -> tuple[PresentationRuntime, RecordingFrameSink]:
    sink = RecordingFrameSink()
    runtime = PresentationRuntime(
        slide_prompts=SLIDES,
        frame_sink=sink,
        controller=controller,
        interruption_frame_factory=make_test_interruption_frame,
        messages_append_frame_factory=make_test_append_frame,
    )
    return runtime, sink


async def narrate_current_slide(
    runtime: PresentationRuntime, text: str = "First sentence. Second sentence."
) -> None:
    """Approve segmented narration then drive bot lifecycle for every segment."""
    await runtime.accept_approved_narration(text)
    plan = runtime.narration_plan
    total = plan.total_segments if plan is not None else 1
    for _ in range(total):
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()


def test_01_one_controller_per_session() -> None:
    runtime_a, _ = make_runtime()
    runtime_b, _ = make_runtime()
    assert runtime_a.controller is not runtime_b.controller
    assert runtime_a.state is not runtime_b.state


def test_02_session_start_presents_slide_0_once() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.state.cursor.slide_index == 0
        assert runtime.output_purpose is OutputPurpose.SLIDE_NARRATION
        assert sink.system_messages == ["SLIDE 0 CONTENT"]

    asyncio.run(_run())


def test_03_duplicate_start_does_not_present_twice() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.start_session()
        assert sink.system_messages == ["SLIDE 0 CONTENT"]
        assert runtime.state.cursor.slide_index == 0

    asyncio.run(_run())


def test_04_normal_completion_advances_exactly_one_slide() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate_current_slide(runtime)
        assert runtime.state.cursor.slide_index == 1
        assert runtime.state.mode is LessonMode.PRESENTING
        assert sink.system_messages[-1] == "SLIDE 1 CONTENT"

    asyncio.run(_run())


def test_05_duplicate_completion_does_not_skip_slide() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await narrate_current_slide(runtime)
        assert runtime.state.cursor.slide_index == 1
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.cursor.slide_index == 1

    asyncio.run(_run())


def test_06_user_interruption_does_not_count_as_slide_completion() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        assert runtime.state.cursor.slide_index == 0
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        assert runtime.state.cursor.slide_index == 0
        assert sink.interruption_count >= 1

    asyncio.run(_run())


def test_07_interruption_saves_logical_cursor() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await narrate_current_slide(runtime)
        await narrate_current_slide(runtime)
        assert runtime.state.cursor.slide_index == 2
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.state.interruption_cursor == NarrationCursor(2, 0, 0)
        assert runtime.state.cursor == NarrationCursor(2, 0, 0)

    asyncio.run(_run())


def test_08_answer_completion_returns_to_interrupted_slide() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await narrate_current_slide(runtime)
        assert runtime.state.cursor.slide_index == 1
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.output_purpose is OutputPurpose.INTERRUPTION_ANSWER
        await runtime.on_bot_stopped_speaking()
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.state.cursor.slide_index == 1
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION

    asyncio.run(_run())


def test_09_pause_preserves_state_and_cursor() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate_current_slide(runtime)
        cursor = NarrationCursor(1, 3, 4)
        await runtime.pause(cursor=cursor)
        assert runtime.state.mode is LessonMode.PAUSED
        assert runtime.state.cursor == cursor
        assert runtime.state.mode_before_pause is LessonMode.PRESENTING
        assert sink.interruption_count >= 1

    asyncio.run(_run())


def test_10_resume_restores_previous_mode() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.pause()
        await runtime.resume()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION

    asyncio.run(_run())


def test_11_valid_slide_navigation_presents_requested_slide() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.go_to_slide(5)
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.state.cursor == NarrationCursor(5, 0, 0)
        assert "SLIDE 5 CONTENT" in sink.system_messages

    asyncio.run(_run())


def test_12_invalid_slide_navigation_leaves_state_unchanged() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        before = runtime.state
        with pytest.raises(InvalidLessonTransition):
            await runtime.go_to_slide(-1)
        assert runtime.state is before
        with pytest.raises(InvalidLessonTransition):
            await runtime.go_to_slide(8)
        assert runtime.state is before

    asyncio.run(_run())


def test_13_slides_0_through_7_are_reachable() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        seen = {0}
        for _ in range(7):
            await narrate_current_slide(runtime)
            seen.add(runtime.state.cursor.slide_index)
        assert seen == set(range(8))
        assert all(f"SLIDE {i} CONTENT" in sink.system_messages for i in range(8))

    asyncio.run(_run())


def test_14_completing_slide_7_enters_qa_mode() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        for _ in range(7):
            await narrate_current_slide(runtime)
        assert runtime.state.cursor.slide_index == 7
        await narrate_current_slide(runtime)
        assert runtime.state.mode is LessonMode.QA_MODE

    asyncio.run(_run())


def test_15_completing_slide_7_does_not_finish_session() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        for _ in range(8):
            await narrate_current_slide(runtime)
        assert runtime.state.mode is LessonMode.QA_MODE
        assert runtime.state.mode is not LessonMode.FINISHED

    asyncio.run(_run())


def test_16_qa_transition_queued_once() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        for _ in range(8):
            await narrate_current_slide(runtime)
        qa_msgs = [m for m in sink.system_messages if "Q&A" in m or "open Q&A" in m]
        assert len(qa_msgs) == 1
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        qa_msgs_after = [
            m for m in sink.system_messages if "Q&A" in m or "open Q&A" in m
        ]
        assert len(qa_msgs_after) == 1

    asyncio.run(_run())


def test_17_disconnect_ends_controller_session_once() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.end_session()
        assert runtime.state.mode is LessonMode.FINISHED
        await runtime.end_session()
        assert runtime.state.mode is LessonMode.FINISHED

    asyncio.run(_run())


def test_18_no_live_openai_client_in_runtime_module() -> None:
    source = Path(__file__).resolve().parents[1] / "presentation_runtime.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert "openai" not in imported
    assert "httpx" not in imported


def test_19_retired_silence_timer_cannot_advance_slides() -> None:
    root = Path(__file__).resolve().parents[1]
    agent_source = (root / "agent.py").read_text(encoding="utf-8")
    runtime_source = (root / "presentation_runtime.py").read_text(encoding="utf-8")
    for text in (agent_source, runtime_source):
        assert "PresentationObserver0" not in text
        assert "SILENCE_THRESHOLD" not in text
        assert "call_later" not in text
        assert "Say goodbye and end the presentation" not in text
    import agent as agent_module

    assert not hasattr(agent_module, "PresentationObserver0")


def test_20_output_purpose_distinguishes_narration_from_answers() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        assert runtime.output_purpose is OutputPurpose.SLIDE_NARRATION
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.output_purpose is OutputPurpose.INTERRUPTION_ANSWER
        await runtime.on_bot_stopped_speaking()
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION

    asyncio.run(_run())
