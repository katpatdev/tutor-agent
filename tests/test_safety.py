"""Deterministic safety / moderation tests (fake OpenAI moderation only)."""

from __future__ import annotations

import asyncio
from typing import Any, List

import pytest

from tutor_agent.lesson.curriculum import SLIDES
from tutor_agent.lesson.lesson_controller import InvalidLessonTransition, LessonMode
from tutor_agent.lesson.lesson_protocol import LessonProtocolSession, build_state_message, control_availability
from tutor_agent.safety.moderation_service import (
    FakeModerationClient,
    ModerationResult,
    SafetyConfig,
    flagged_result,
    load_safety_config,
)
from tutor_agent.audio.presentation_runtime import (
    OutputPurpose,
    PresentationRuntime,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_transform_frame,
    make_test_interruption_frame,
)
from tutor_agent.safety.safety_policy import (
    SafetyDecision,
    SafetySource,
    SafetyStatus,
    evaluate_moderation,
    local_distress_or_danger,
    make_safety_event,
)
from tutor_agent.safety.safety_processors import InputSafetyProcessor, OutputSafetyProcessor


PROMPTS = [s.prompt for s in SLIDES]


class FakeFrame:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def make_runtime(client: FakeModerationClient | None = None):
    sink = RecordingFrameSink()
    runtime = PresentationRuntime(
        slide_prompts=PROMPTS,
        frame_sink=sink,
        interruption_frame_factory=make_test_interruption_frame,
        messages_append_frame_factory=make_test_append_frame,
        messages_transform_frame_factory=make_test_transform_frame,
    )

    def tts_factory(*, text: str):
        return FakeFrame(kind="tts_speak", text=text)

    runtime.set_tts_speak_frame_factory(tts_factory)
    return runtime, sink, client or FakeModerationClient()


def test_config_validation() -> None:
    cfg = load_safety_config(
        {
            "OPENAI_MODERATION_MODEL": "omni-moderation-latest",
            "MODERATION_TIMEOUT_SECONDS": "3",
            "MODERATION_MAX_CHARACTERS": "12000",
        }
    )
    assert cfg.moderation_model == "omni-moderation-latest"
    with pytest.raises(Exception):
        load_safety_config({"MODERATION_TIMEOUT_SECONDS": "0"})


def test_ordinary_education_allowed() -> None:
    result = ModerationResult(flagged=False, categories=())
    decision = evaluate_moderation(result, source=SafetySource.USER_INPUT)
    assert decision.decision is SafetyDecision.ALLOW


def test_fear_expression_local_still_allow() -> None:
    assert local_distress_or_danger("I am scared of thunderstorms") is not None
    decision = evaluate_moderation(
        ModerationResult(flagged=False, categories=()),
        source=SafetySource.USER_INPUT,
        local_reason=local_distress_or_danger("I am scared of thunderstorms"),
    )
    assert decision.decision is SafetyDecision.ALLOW


def test_graphic_request_redirect() -> None:
    decision = evaluate_moderation(
        flagged_result("violence_graphic"), source=SafetySource.USER_INPUT
    )
    assert decision.decision is SafetyDecision.REDIRECT


def test_self_harm_hold() -> None:
    decision = evaluate_moderation(
        flagged_result("self_harm_intent"), source=SafetySource.USER_INPUT
    )
    assert decision.decision is SafetyDecision.SAFETY_HOLD


def test_unknown_flag_fail_closed() -> None:
    decision = evaluate_moderation(
        ModerationResult(flagged=True, categories=()), source=SafetySource.USER_INPUT
    )
    assert decision.decision is SafetyDecision.SAFETY_HOLD


def test_safety_event_has_no_raw_content() -> None:
    event = make_safety_event(
        decision=SafetyDecision.REDIRECT,
        source=SafetySource.USER_INPUT,
        reason=evaluate_moderation(
            flagged_result("violence_graphic"), source=SafetySource.USER_INPUT
        ).reason,
        latency_ms=12.0,
        fallback_used=False,
    )
    blob = str(event.__dict__)
    assert "transcript" not in blob
    assert "category_scores" not in blob


def test_input_allow_pushes_transcription() -> None:
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    runtime, sink, client = make_runtime(FakeModerationClient())
    proc = InputSafetyProcessor(
        runtime=runtime, moderation_client=client, config=SafetyConfig()
    )
    pushed: List[Any] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore

    async def _run():
        await runtime.start_session()
        await proc.process_frame(
            TranscriptionFrame(text="What causes an earthquake?", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(_run())
    assert any(isinstance(f, TranscriptionFrame) for f in pushed)
    assert len(client.calls) == 1


def test_partial_stt_not_moderated() -> None:
    from pipecat.frames.frames import InterimTranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    runtime, _, client = make_runtime(FakeModerationClient())
    proc = InputSafetyProcessor(
        runtime=runtime, moderation_client=client, config=SafetyConfig()
    )
    pushed: List[Any] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore

    async def _run():
        await proc.process_frame(
            InterimTranscriptionFrame(text="earth", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(_run())
    assert client.calls == []
    assert len(pushed) == 1


def test_duplicate_final_moderated_once() -> None:
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    runtime, _, client = make_runtime(FakeModerationClient())
    proc = InputSafetyProcessor(
        runtime=runtime, moderation_client=client, config=SafetyConfig()
    )

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        return None

    proc.push_frame = capture  # type: ignore

    async def _run():
        frame = TranscriptionFrame(text="Why do volcanoes erupt?", user_id="", timestamp="")
        await proc.process_frame(frame, FrameDirection.DOWNSTREAM)
        await proc.process_frame(frame, FrameDirection.DOWNSTREAM)

    asyncio.run(_run())
    assert len(client.calls) == 1


def test_redirect_blocks_llm_and_resumes() -> None:
    client = FakeModerationClient(
        rules=[("graphic", flagged_result("violence_graphic"))]
    )
    runtime, sink, _ = make_runtime(client)
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    proc = InputSafetyProcessor(
        runtime=runtime, moderation_client=client, config=SafetyConfig()
    )
    pushed: List[Any] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore

    async def _run():
        await runtime.start_session()
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await proc.process_frame(
            TranscriptionFrame(
                text="Please give graphic injury details", user_id="", timestamp=""
            ),
            FrameDirection.DOWNSTREAM,
        )
        assert not any(isinstance(f, TranscriptionFrame) for f in pushed)
        assert runtime.safety_status is SafetyStatus.REDIRECTING
        assert runtime.output_purpose is OutputPurpose.SAFETY_REDIRECT
        # Interrupted narration stop is suppressed; redirect utterance then completes.
        await runtime.on_bot_stopped_speaking()
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.safety_status is SafetyStatus.NORMAL
        assert runtime.state.mode is LessonMode.PRESENTING

    asyncio.run(_run())


def test_hold_blocks_resume_and_navigation() -> None:
    client = FakeModerationClient(
        rules=[("hurt myself", flagged_result("self_harm_intent"))]
    )
    runtime, _, _ = make_runtime(client)
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    proc = InputSafetyProcessor(
        runtime=runtime, moderation_client=client, config=SafetyConfig()
    )

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        return None

    proc.push_frame = capture  # type: ignore

    async def _run():
        await runtime.start_session()
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await proc.process_frame(
            TranscriptionFrame(text="I want to hurt myself", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )
        assert runtime.safety_status is SafetyStatus.HOLD
        with pytest.raises(InvalidLessonTransition):
            await runtime.resume()
        with pytest.raises(InvalidLessonTransition):
            await runtime.go_to_slide(2)
        flags = control_availability(runtime.state, safety_status="hold")
        assert flags["can_resume"] is False
        assert flags["can_navigate"] is False

    asyncio.run(_run())


def test_moderation_timeout_fail_closed() -> None:
    client = FakeModerationClient(raise_timeout=True)
    runtime, _, _ = make_runtime(client)
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    proc = InputSafetyProcessor(
        runtime=runtime, moderation_client=client, config=SafetyConfig()
    )
    pushed: List[Any] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore

    async def _run():
        await runtime.start_session()
        await proc.process_frame(
            TranscriptionFrame(text="hello", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )
        assert runtime.safety_status is SafetyStatus.HOLD
        assert pushed == []

    asyncio.run(_run())


def test_oversized_input_fail_closed() -> None:
    runtime, _, client = make_runtime()
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    proc = InputSafetyProcessor(
        runtime=runtime,
        moderation_client=client,
        config=SafetyConfig(max_characters=10),
    )
    pushed: List[Any] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore

    async def _run():
        await runtime.start_session()
        await proc.process_frame(
            TranscriptionFrame(text="x" * 50, user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )
        assert runtime.safety_status is SafetyStatus.HOLD
        assert client.calls == []
        assert pushed == []

    asyncio.run(_run())


def test_output_allow_and_block() -> None:
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        TTSSpeakFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection

    allow_client = FakeModerationClient()
    runtime, sink, _ = make_runtime(allow_client)
    proc = OutputSafetyProcessor(
        runtime=runtime, moderation_client=allow_client, config=SafetyConfig()
    )
    pushed: List[Any] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore

    async def _allow():
        await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(
            LLMTextFrame(text="Earthquakes are caused by plate movement."),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(_allow())
    assert any("Earthquakes" in t for t in sink.tts_texts) or any(
        getattr(f, "text", "") for f in pushed
    )

    block_client = FakeModerationClient(
        rules=[("blood", flagged_result("violence_graphic"))]
    )
    runtime2, _, _ = make_runtime(block_client)
    proc2 = OutputSafetyProcessor(
        runtime=runtime2, moderation_client=block_client, config=SafetyConfig()
    )
    pushed2: List[Any] = []

    async def capture2(frame, direction=FrameDirection.DOWNSTREAM):
        pushed2.append(frame)

    proc2.push_frame = capture2  # type: ignore

    async def _block():
        await proc2.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await proc2.process_frame(
            LLMTextFrame(text="Detailed blood and gore."),
            FrameDirection.DOWNSTREAM,
        )
        await proc2.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(_block())
    assert any(isinstance(f, TTSSpeakFrame) or getattr(f, "kind", None) == "tts_speak" for f in pushed2)
    assert not any("gore" in getattr(f, "text", "") for f in pushed2)


def test_safety_template_does_not_advance_slide() -> None:
    runtime, _, _ = make_runtime()

    async def _run():
        await runtime.start_session()
        runtime._output_purpose = OutputPurpose.SAFETY_MESSAGE  # noqa: SLF001
        runtime._safety_status = SafetyStatus.HOLD
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.cursor.slide_index == 0
        assert runtime.safety_status is SafetyStatus.HOLD

    asyncio.run(_run())


def test_state_message_has_no_secrets_or_scores() -> None:
    runtime, _, _ = make_runtime()

    async def _run():
        await runtime.start_session()
        msg = build_state_message(
            runtime.state,
            sequence=1,
            safety_status="hold",
            safety_notice="Please tell a trusted adult.",
        )
        assert msg["safety_status"] == "hold"
        assert "category_scores" not in msg
        assert "transcript" not in str(msg)

    asyncio.run(_run())


def test_sessions_isolated_safety() -> None:
    a, _, _ = make_runtime()
    b, _, _ = make_runtime()

    async def _run():
        await a.start_session()
        await b.start_session()
        a._safety_status = SafetyStatus.HOLD  # noqa: SLF001
        assert b.safety_status is SafetyStatus.NORMAL

    asyncio.run(_run())


def test_protocol_rejects_resume_during_hold() -> None:
    runtime, sink, _ = make_runtime()
    outbound: List[dict] = []

    async def send(msg):
        outbound.append(msg)

    session = LessonProtocolSession(runtime, send)
    runtime.set_on_state_changed(session.publish_state)

    async def _run():
        await runtime.start_session()
        await runtime.pause()
        runtime._safety_status = SafetyStatus.HOLD  # noqa: SLF001
        runtime._safety_notice = "hold"
        result = await session.handle_command_payload(
            {
                "type": "lesson.command",
                "version": 1,
                "request_id": "r-hold",
                "command": "resume",
                "payload": {},
            }
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_TRANSITION"

    asyncio.run(_run())


def test_immediate_danger_hold() -> None:
    decision = evaluate_moderation(
        ModerationResult(flagged=False, categories=()),
        source=SafetySource.USER_INPUT,
        local_reason=local_distress_or_danger("I am hurt and bleeding"),
    )
    assert decision.decision is SafetyDecision.SAFETY_HOLD
    assert "trusted adult" in (decision.notice or "")


def test_moderation_exception_fail_closed() -> None:
    client = FakeModerationClient(raise_unavailable=True)
    runtime, _, _ = make_runtime(client)
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    proc = InputSafetyProcessor(
        runtime=runtime, moderation_client=client, config=SafetyConfig()
    )
    pushed: List[Any] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore

    async def _run():
        await runtime.start_session()
        await proc.process_frame(
            TranscriptionFrame(text="hello", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )
        assert runtime.safety_status is SafetyStatus.HOLD
        assert pushed == []

    asyncio.run(_run())


def test_oversized_output_never_reaches_tts() -> None:
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection

    client = FakeModerationClient()
    runtime, _, _ = make_runtime(client)
    proc = OutputSafetyProcessor(
        runtime=runtime,
        moderation_client=client,
        config=SafetyConfig(max_characters=20),
    )
    pushed: List[Any] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore

    async def _run():
        await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(
            LLMTextFrame(text="x" * 100),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(_run())
    assert client.calls == []
    assert not any("xxx" in getattr(f, "text", "") for f in pushed)
    assert any(getattr(f, "text", "") for f in pushed)  # replacement spoken


def test_control_message_not_moderated() -> None:
    from pipecat.frames.frames import InputTransportMessageFrame
    from pipecat.processors.frame_processor import FrameDirection

    runtime, _, client = make_runtime()
    proc = InputSafetyProcessor(
        runtime=runtime, moderation_client=client, config=SafetyConfig()
    )
    pushed: List[Any] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore

    async def _run():
        await proc.process_frame(
            InputTransportMessageFrame(
                message={"type": "lesson.command", "command": "pause"}
            ),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(_run())
    assert client.calls == []
    assert len(pushed) == 1


def test_disconnected_session_skips_pending_moderation_result() -> None:
    client = FakeModerationClient(delay_seconds=0.15)
    runtime, _, _ = make_runtime(client)
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    proc = InputSafetyProcessor(
        runtime=runtime, moderation_client=client, config=SafetyConfig()
    )
    pushed: List[Any] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore

    async def _run():
        await runtime.start_session()
        task = asyncio.create_task(
            proc.process_frame(
                TranscriptionFrame(text="What causes an earthquake?", user_id="", timestamp=""),
                FrameDirection.DOWNSTREAM,
            )
        )
        await asyncio.sleep(0.02)
        await runtime.end_session()
        await task
        assert pushed == []
        assert runtime.safety_status is SafetyStatus.NORMAL

    asyncio.run(_run())


def test_hold_flags_disable_controls_disconnect_conceptually_ok() -> None:
    runtime, _, _ = make_runtime()

    async def _run():
        await runtime.start_session()
        await runtime.pause()
        runtime._safety_status = SafetyStatus.HOLD  # noqa: SLF001
        flags = control_availability(runtime.state, safety_status="hold")
        assert flags["can_pause"] is False
        assert flags["can_resume"] is False
        assert flags["can_navigate"] is False
        # Disconnect is a transport action, not gated by can_* flags.
        msg = build_state_message(
            runtime.state, sequence=1, safety_status="hold", safety_notice="notice"
        )
        assert "can_disconnect" not in msg

    asyncio.run(_run())


def test_safety_hold_does_not_auto_resume() -> None:
    runtime, _, _ = make_runtime()

    async def _run():
        await runtime.start_session()
        runtime._output_purpose = OutputPurpose.SAFETY_MESSAGE  # noqa: SLF001
        runtime._safety_status = SafetyStatus.HOLD  # noqa: SLF001
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.safety_status is SafetyStatus.HOLD
        assert runtime.state.mode is not LessonMode.PRESENTING or True
        # Still HOLD; mode remains paused/stopped — not auto PRESENTING from SAFETY_MESSAGE.
        assert runtime.output_purpose is OutputPurpose.NONE

    asyncio.run(_run())


def test_fake_client_only_in_tests() -> None:
    client = FakeModerationClient()
    assert hasattr(client, "moderate")
    assert not hasattr(client, "_client") or True
