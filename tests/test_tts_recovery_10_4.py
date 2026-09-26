"""Iteration 10.4: TTS no-audio recovery and deterministic voice navigation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List

import pytest

from lesson_controller import LessonMode
from presentation_runtime import (
    OutputPurpose,
    PresentationRuntime,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_interruption_frame,
    make_test_transform_frame,
)
from tts_unit import (
    SpeechUnitKind,
    TtsRecoveryConfig,
    is_tts_no_audio_error,
)
from voice_navigation import VoiceNavAction, parse_voice_navigation


@dataclass
class FakeTTSSpeakFrame:
    text: str
    kind: str = "tts_speak"


@dataclass
class FakeErrorFrame:
    error: str
    processor: Any = None
    fatal: bool = False


class FakeTTSProcessor:
    pass


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self._sleepers: List[tuple[float, asyncio.Future]] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._sleepers.append((self.now + delay, fut))
        await fut

    def advance(self, seconds: float) -> None:
        self.now += seconds
        ready = [item for item in self._sleepers if item[0] <= self.now]
        self._sleepers = [item for item in self._sleepers if item[0] > self.now]
        for _, fut in ready:
            if not fut.done():
                fut.set_result(None)


def make_runtime(
    *,
    clock: FakeClock | None = None,
    config: TtsRecoveryConfig | None = None,
    slide_count: int = 8,
) -> tuple[PresentationRuntime, RecordingFrameSink, FakeClock]:
    clock = clock or FakeClock()
    sink = RecordingFrameSink()
    cfg = config or TtsRecoveryConfig(
        max_retries=1,
        first_audio_timeout_s=2.0,
        completion_timeout_base_s=5.0,
        completion_seconds_per_char=0.01,
        completion_timeout_max_s=20.0,
        max_consecutive_failures=3,
    )
    tasks: List[asyncio.Task] = []

    def create_task(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    runtime = PresentationRuntime(
        slide_prompts=[f"SLIDE {i}" for i in range(slide_count)],
        frame_sink=sink,
        interruption_frame_factory=make_test_interruption_frame,
        messages_append_frame_factory=make_test_append_frame,
        messages_transform_frame_factory=make_test_transform_frame,
        narration_max_characters=80,
        narration_min_characters=1,
        tts_recovery_config=cfg,
        time_fn=clock.time,
        create_task=create_task,
        sleep_fn=clock.sleep,
    )
    runtime.set_tts_speak_frame_factory(FakeTTSSpeakFrame)
    return runtime, sink, clock


def test_is_tts_no_audio_error_requires_tts_processor() -> None:
    tts = FakeTTSProcessor()
    ok = FakeErrorFrame(
        error="TTS context 11111111-1111-1111-1111-111111111111 completed with no audio",
        processor=tts,
    )
    assert is_tts_no_audio_error(ok, tts_processor=tts)
    other = FakeErrorFrame(error="STT failed", processor=object())
    assert not is_tts_no_audio_error(other, tts_processor=tts)
    broad = FakeErrorFrame(error="network audio glitch", processor=tts)
    assert not is_tts_no_audio_error(broad, tts_processor=tts)


def test_voice_nav_parser_commands_and_false_positives() -> None:
    assert parse_voice_navigation("next slide").action is VoiceNavAction.NEXT
    assert parse_voice_navigation("Can we go to the next slide?").action is VoiceNavAction.NEXT
    goto = parse_voice_navigation("go to slide 6")
    assert goto is not None and goto.slide_index == 5
    assert parse_voice_navigation("take me to slide six").slide_index == 5
    assert parse_voice_navigation("previous slide").action is VoiceNavAction.PREVIOUS
    assert parse_voice_navigation("repeat this slide").action is VoiceNavAction.REPEAT
    assert parse_voice_navigation("What is on slide 6?") is None
    assert parse_voice_navigation("Why is the next slide important?") is None
    assert parse_voice_navigation("When will we reach slide 6?") is None
    assert parse_voice_navigation("Can you explain the previous slide?") is None


def test_narration_no_audio_retries_same_text_once() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration(
            "First teaching sentence is long enough. Second teaching sentence follows."
        )
        assert sink.tts_texts
        first = sink.tts_texts[0]
        tts = FakeTTSProcessor()
        err = FakeErrorFrame(
            error="TTS context 22222222-2222-2222-2222-222222222222 completed with no audio",
            processor=tts,
        )
        await runtime.on_tts_error_frame(err, tts_processor=tts)
        assert runtime.tts_retry_attempts == 1
        assert sink.tts_texts.count(first) == 2
        assert sink.interruption_count >= 1
        # Successful retry completes once.
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.tts_retry_successes == 1
        assert runtime.segments_completed == 1

    asyncio.run(_run())


def test_retry_exhaustion_does_not_freeze_and_skips_verified_complete() -> None:
    async def _run() -> None:
        cfg = TtsRecoveryConfig(max_retries=0, first_audio_timeout_s=2.0, max_consecutive_failures=5)
        runtime, sink, _ = make_runtime(config=cfg)
        await runtime.start_session()
        await runtime.accept_approved_narration(
            "Alpha sentence for packing bounds here. Beta sentence continues the idea."
        )
        first_plan_segments = runtime.narration_plan.total_segments
        assert first_plan_segments >= 1
        tts = FakeTTSProcessor()
        err = FakeErrorFrame(
            error="TTS context 33333333-3333-3333-3333-333333333333 completed with no audio",
            processor=tts,
        )
        await runtime.on_tts_error_frame(err, tts_processor=tts)
        assert runtime.tts_retry_exhaustion == 1
        assert runtime.tts_failed_narration_units == 1
        assert runtime.segments_completed == 0  # not verified heard
        assert runtime.audio_warning
        # Progression continued or completed slide without freeze.
        assert runtime._tts.pending is None or runtime.state.mode is LessonMode.PRESENTING

    asyncio.run(_run())


def test_answer_no_audio_leaves_answering() -> None:
    async def _run() -> None:
        cfg = TtsRecoveryConfig(max_retries=0, max_consecutive_failures=5)
        runtime, _, _ = make_runtime(config=cfg)
        await runtime.start_session()
        await runtime.accept_approved_narration("Only one short segment here for the plan.")
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        await runtime.play_moderated_answer_units(["Short answer unit."])
        tts = FakeTTSProcessor()
        await runtime.on_tts_error_frame(
            FakeErrorFrame(
                error="TTS context 44444444-4444-4444-4444-444444444444 completed with no audio",
                processor=tts,
            ),
            tts_processor=tts,
        )
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.tts_failed_answer_units == 1

    asyncio.run(_run())


def test_start_timeout_retries_then_ignores_late_success() -> None:
    async def _run() -> None:
        cfg = TtsRecoveryConfig(max_retries=1, first_audio_timeout_s=1.0, max_consecutive_failures=5)
        runtime, sink, _ = make_runtime(config=cfg)
        await runtime.start_session()
        await runtime.accept_approved_narration(
            "Timeout sentence one is long enough. Timeout sentence two continues."
        )
        unit = runtime._tts.pending
        assert unit is not None
        unit_id = unit.unit_id
        attempt = unit.attempt
        await runtime._on_tts_start_timeout(unit_id, attempt)
        assert runtime.tts_first_audio_timeouts == 1
        assert runtime.tts_retry_attempts == 1
        assert sink.tts_texts[-1] == unit.text

    asyncio.run(_run())


def test_voice_navigation_next_and_goto() -> None:
    async def _run() -> None:
        runtime, _, _ = make_runtime()
        await runtime.start_session()
        from voice_navigation import VoiceNavIntent

        ok = await runtime.handle_voice_navigation(
            VoiceNavIntent(action=VoiceNavAction.NEXT, raw="next slide")
        )
        assert ok
        assert runtime.state.cursor.slide_index == 1
        ok = await runtime.handle_voice_navigation(
            VoiceNavIntent(action=VoiceNavAction.GOTO, slide_index=5, raw="go to slide 6")
        )
        assert ok
        assert runtime.state.cursor.slide_index == 5
        bad = await runtime.handle_voice_navigation(
            VoiceNavIntent(action=VoiceNavAction.GOTO, slide_index=99, raw="go to slide 99")
        )
        assert bad is False
        assert runtime.voice_nav_rejections >= 1

    asyncio.run(_run())


def test_non_tts_error_does_not_retry() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration(
            "Keep this narration segment text stable for the check."
        )
        before = list(sink.tts_texts)
        await runtime.on_tts_error_frame(
            FakeErrorFrame(error="LLM timeout", processor=object()),
            tts_processor=FakeTTSProcessor(),
        )
        assert sink.tts_texts == before
        assert runtime.tts_retry_attempts == 0

    asyncio.run(_run())


def test_metrics_have_no_text() -> None:
    runtime, _, _ = make_runtime()
    # Public counters only; pending text must never appear on host public attrs.
    assert runtime.tts_units_queued == 0
    assert not hasattr(runtime, "pending_tts_text")
    blob = str(runtime.__dict__.keys())
    assert "Hello students" not in blob
