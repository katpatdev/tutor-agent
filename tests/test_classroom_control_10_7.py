"""Iteration 10.7: checkpoints, natural controls, transitions, prefetch."""

from __future__ import annotations

import asyncio
from typing import List, Optional

import pytest

from classroom_control import (
    ClassroomControlKind,
    parse_classroom_control,
    parse_navigation_intent,
)
from lesson_controller import LessonMode
from narration_prefetch import NarrationPrefetchCache, PrefetchState
from presentation_runtime import (
    CHECKPOINT_REMINDER_TEXT,
    POST_ANSWER_INVITE_CHECKPOINT,
    POST_ANSWER_INVITE_MID_SLIDE,
    OutputPurpose,
    PresentationRuntime,
    RecordingFrameSink,
    SLIDE1_TO_SLIDE2_TRANSITION,
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
    for _ in range(6):
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
    )


# --- Parser ---

@pytest.mark.parametrize(
    "utterance",
    [
        "Can we please skip to slide 3 as I already have knowledge about this?",
        "Please move to slide 6 because I know this part",
        "Please move to slide 6 because I already understand this part.",
    ],
)
def test_explanatory_clause_navigates(utterance: str) -> None:
    intent = parse_classroom_control(utterance, slide_count=8)
    assert intent is not None
    assert intent.kind is ClassroomControlKind.NAVIGATION


@pytest.mark.parametrize(
    "utterance",
    [
        "I just have a question from slide seven",
        "I have a doubt in slide two",
        "My question is about slide 5.",
    ],
)
def test_question_reference_not_navigation(utterance: str) -> None:
    intent = parse_classroom_control(utterance, slide_count=8)
    assert intent is not None
    assert intent.kind is ClassroomControlKind.QUESTION_REFERENCE
    assert intent.navigation is None


def test_negation_is_stay_not_clarify() -> None:
    intent = parse_classroom_control("Do not go to slide 7.", slide_count=8)
    assert intent is not None
    assert intent.kind is ClassroomControlKind.STAY


CONTINUE_PHRASES = [
    "continue",
    "please continue",
    "continue please",
    "you can continue",
    "you may continue",
    "yes, continue",
    "okay, continue",
    "let’s continue",
    "go ahead",
    "please go ahead",
    "carry on",
    "move on",
    "we can move on",
    "let’s move on",
    "proceed",
    "you can proceed",
    "resume",
    "continue from where you stopped",
    "continue from where you left off",
]


@pytest.mark.parametrize("utterance", CONTINUE_PHRASES)
def test_continue_phrases(utterance: str) -> None:
    intent = parse_classroom_control(utterance, slide_count=8)
    assert intent is not None
    assert intent.kind is ClassroomControlKind.CONTINUE


def test_content_next_slide_does_not_navigate() -> None:
    assert parse_navigation_intent("The next slide is about preparation.") is None
    assert parse_classroom_control("The next slide is about preparation.") is None


# --- Runtime / checkpoints ---


def test_slide1_auto_transitions_to_slide2() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate(runtime)
        assert runtime.state.cursor.slide_index == 1
        assert SLIDE1_TO_SLIDE2_TRANSITION in sink.tts_texts
        assert not runtime.in_slide_checkpoint

    asyncio.run(_run())


def test_slides_2_to_7_stop_at_checkpoint() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await narrate(runtime)  # → 1
        await narrate(runtime)  # complete slide 2 (index 1)
        assert runtime.state.cursor.slide_index == 1
        assert runtime.in_slide_checkpoint

    asyncio.run(_run())


def test_checkpoint_silence_does_not_advance() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate(runtime)
        await narrate(runtime)
        assert runtime.in_slide_checkpoint
        slide = runtime.state.cursor.slide_index
        await asyncio.sleep(0.12)
        assert runtime.state.cursor.slide_index == slide
        assert runtime.in_slide_checkpoint
        # Reminder at most once.
        await drain_purpose(runtime, OutputPurpose.POST_ANSWER_REMINDER)
        reminders = [t for t in sink.tts_texts if t == CHECKPOINT_REMINDER_TEXT]
        assert len(reminders) <= 1

    asyncio.run(_run())


def test_continue_at_checkpoint_advances_once() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await narrate(runtime)
        await narrate(runtime)
        assert runtime.in_slide_checkpoint
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("You can continue.")
        )
        assert ok
        await drain_purpose(runtime, OutputPurpose.NAV_ACK)
        assert runtime.state.cursor.slide_index == 2
        assert not runtime.in_slide_checkpoint

    asyncio.run(_run())


def test_continue_after_mid_slide_question_resumes() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await narrate(runtime)  # on slide 1 awaiting/start
        await drain_purpose(runtime, OutputPurpose.NAV_ACK)
        await runtime.accept_approved_narration(TWO)
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.output_purpose is OutputPurpose.POST_ANSWER_INVITE
        assert POST_ANSWER_INVITE_MID_SLIDE in sink.tts_texts
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("You can continue.")
        )
        assert ok
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION

    asyncio.run(_run())


def test_question_reference_sets_context_without_nav() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        slide = runtime.state.cursor.slide_index
        ok = await runtime.handle_classroom_control(
            parse_classroom_control(
                "I just have a question from slide seven.", slide_count=8
            )
        )
        assert ok
        assert runtime.state.cursor.slide_index == slide
        assert runtime._question_reference_slide == 7  # noqa: SLF001
        snap = runtime.build_lesson_context_snapshot()
        assert "refers to slide 7" in snap
        assert any("slide 7" in t for t in sink.tts_texts)

    asyncio.run(_run())


def test_direct_nav_no_confirmation_question() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        ok = await runtime.handle_classroom_control(
            parse_classroom_control(
                "Can we please skip to slide 3 as I already have knowledge about this?",
                slide_count=8,
            )
        )
        assert ok
        assert runtime.state.cursor.slide_index == 2
        assert not any("?" in t and "want me" in t.lower() for t in sink.tts_texts)
        assert any("slide 3" in t.lower() for t in sink.tts_texts)

    asyncio.run(_run())


def test_transition_spoken_once_on_nav() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        before = len(sink.tts_texts)
        await runtime.handle_classroom_control(
            parse_classroom_control("I want to go to slide 5.", slide_count=8)
        )
        new = sink.tts_texts[before:]
        assert len(new) == 1
        assert "slide 5" in new[0].lower()

    asyncio.run(_run())


# --- Prefetch ---


def test_prefetch_ready_before_present_and_sequential() -> None:
    async def _run() -> None:
        generated: List[int] = []

        async def gen(idx: int, _instruction: str) -> str:
            generated.append(idx)
            await asyncio.sleep(0.01)
            return f"Narration for slide {idx + 1}. Extra detail here for length."

        async def mod(text: str) -> str:
            return text

        runtime, _ = make_runtime()
        cache = NarrationPrefetchCache(
            slide_prompts=runtime._slide_prompts,  # noqa: SLF001
            slide_count=8,
            tutor_system_prompt="tutor",
            generate_fn=gen,
            moderate_fn=mod,
            max_characters=80,
            min_characters=1,
        )
        runtime.set_prefetch_cache(cache)
        await runtime.prepare_and_start_session()
        assert cache.entry(0).state is PrefetchState.READY
        assert runtime.state.mode is LessonMode.PRESENTING
        # Background starts for remaining slides.
        await asyncio.sleep(0.2)
        assert generated[0] == 0
        assert generated == sorted(generated)
        await cache.cancel()

    asyncio.run(_run())


def test_prefetch_cache_hit_skips_llm_instruction() -> None:
    async def _run() -> None:
        calls = {"n": 0}

        async def gen(idx: int, _instruction: str) -> str:
            calls["n"] += 1
            return f"Cached narration text for slide {idx + 1} with enough characters."

        async def mod(text: str) -> str:
            return text

        runtime, sink = make_runtime()
        cache = NarrationPrefetchCache(
            slide_prompts=runtime._slide_prompts,  # noqa: SLF001
            slide_count=8,
            tutor_system_prompt="tutor",
            generate_fn=gen,
            moderate_fn=mod,
            max_characters=80,
            min_characters=1,
        )
        runtime.set_prefetch_cache(cache)
        await cache.ensure_slide(0)
        n_before = calls["n"]
        await runtime.prepare_and_start_session()
        # Slide 1 used cache — no additional generate for present path.
        assert calls["n"] == n_before
        # No live slide-narration instruction for slide 0 when cache hit.
        assert not any("SLIDE 0" in m for m in sink.system_messages)
        await cache.cancel()

    asyncio.run(_run())


def test_prefetch_cancel_on_end_session() -> None:
    async def _run() -> None:
        started = asyncio.Event()

        async def gen(idx: int, _instruction: str) -> str:
            started.set()
            await asyncio.sleep(2.0)
            return f"Slow narration {idx}"

        async def mod(text: str) -> str:
            return text

        runtime, _ = make_runtime()
        cache = NarrationPrefetchCache(
            slide_prompts=runtime._slide_prompts,  # noqa: SLF001
            slide_count=8,
            tutor_system_prompt="tutor",
            generate_fn=gen,
            moderate_fn=mod,
        )
        runtime.set_prefetch_cache(cache)
        # Seed slide 0 quickly via store, then start background.
        await cache.store_live_approved(0, "Welcome slide narration with enough text here.")
        cache.start_background(after_slide=0)
        await started.wait()
        await runtime.end_session()
        assert cache._cancelled  # noqa: SLF001

    asyncio.run(_run())


def test_prefetch_not_marked_visited() -> None:
    async def _run() -> None:
        async def gen(idx: int, _instruction: str) -> str:
            return f"Prefetch only narration for slide {idx + 1} content."

        async def mod(text: str) -> str:
            return text

        runtime, _ = make_runtime()
        cache = NarrationPrefetchCache(
            slide_prompts=runtime._slide_prompts,  # noqa: SLF001
            slide_count=8,
            tutor_system_prompt="tutor",
            generate_fn=gen,
            moderate_fn=mod,
        )
        await cache.ensure_slide(3)
        assert cache.entry(3).state is PrefetchState.READY
        assert 3 not in runtime._visited_slides  # noqa: SLF001

    asyncio.run(_run())
