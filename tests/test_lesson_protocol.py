"""Offline tests for lesson_protocol (no OpenAI / network / live Pipecat I/O)."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from curriculum import SLIDES, TOTAL_SLIDES, validate_curriculum
from lesson_controller import LessonMode
from lesson_protocol import (
    FORBIDDEN_OUTBOUND_KEYS,
    PROTOCOL_VERSION,
    REQUEST_ID_HISTORY_LIMIT,
    LessonProtocolSession,
    ProtocolError,
    RequestIdDeduper,
    assert_no_secrets,
    build_state_message,
    build_ws_url,
    extract_lesson_command_payload,
    parse_command_envelope,
    wrap_rtvi_server_message,
)
from presentation_runtime import (
    PresentationRuntime,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_transform_frame,
    make_test_interruption_frame,
)


SLIDE_PROMPTS = [s.prompt for s in SLIDES]


class OutboundRecorder:
    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    async def __call__(self, message: Dict[str, Any]) -> None:
        self.messages.append(dict(message))

    @property
    def lesson_payloads(self) -> List[Dict[str, Any]]:
        return [m["data"] for m in self.messages if m.get("type") == "server-message"]


def make_session() -> tuple[LessonProtocolSession, PresentationRuntime, OutboundRecorder, RecordingFrameSink]:
    sink = RecordingFrameSink()
    outbound = OutboundRecorder()
    runtime = PresentationRuntime(
        slide_prompts=SLIDE_PROMPTS,
        frame_sink=sink,
        interruption_frame_factory=make_test_interruption_frame,
        messages_append_frame_factory=make_test_append_frame,
        messages_transform_frame_factory=make_test_transform_frame,
    )
    session = LessonProtocolSession(runtime, outbound)
    runtime.set_on_state_changed(session.publish_state)
    return session, runtime, outbound, sink


def rtvi_command(envelope: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "label": "rtvi-ai",
        "type": "client-message",
        "id": "x",
        "data": {"t": "lesson.command", "d": envelope},
    }


def cmd(command: str, request_id: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "type": "lesson.command",
        "version": 1,
        "request_id": request_id,
        "command": command,
        "payload": payload or {},
    }


def test_curriculum_metadata_valid() -> None:
    validate_curriculum()
    assert TOTAL_SLIDES == 8
    assert [s.index for s in SLIDES] == list(range(8))
    assert all(s.title for s in SLIDES)


def test_build_ws_url_http_and_https() -> None:
    assert build_ws_url(scheme="http", host="localhost:7860") == "ws://localhost:7860/ws"
    assert build_ws_url(scheme="https", host="example.com") == "wss://example.com/ws"
    assert build_ws_url(scheme="HTTPS", host="a.b", path="/ws") == "wss://a.b/ws"


def test_parse_rejects_bad_envelopes() -> None:
    with pytest.raises(ProtocolError) as exc:
        parse_command_envelope("nope")
    assert exc.value.code == "MALFORMED_MESSAGE"

    with pytest.raises(ProtocolError) as exc2:
        parse_command_envelope(cmd("pause", "r1") | {"version": 99})
    assert exc2.value.code == "UNSUPPORTED_VERSION"

    with pytest.raises(ProtocolError) as exc3:
        parse_command_envelope(
            {"type": "lesson.command", "version": 1, "command": "pause", "payload": {}}
        )
    assert exc3.value.code == "INVALID_REQUEST_ID"

    with pytest.raises(ProtocolError) as exc4:
        parse_command_envelope(cmd("fly", "r1"))
    assert exc4.value.code == "UNKNOWN_COMMAND"


def test_goto_payload_validation() -> None:
    with pytest.raises(ProtocolError) as missing:
        parse_command_envelope(cmd("goto_slide", "r1", {}))
    assert missing.value.code == "MISSING_SLIDE_INDEX"

    with pytest.raises(ProtocolError) as boolean_idx:
        parse_command_envelope(cmd("goto_slide", "r1", {"slide_index": True}))
    assert boolean_idx.value.code == "INVALID_SLIDE_INDEX"

    with pytest.raises(ProtocolError) as float_idx:
        parse_command_envelope(cmd("goto_slide", "r1", {"slide_index": 1.5}))
    assert float_idx.value.code == "INVALID_SLIDE_INDEX"

    with pytest.raises(ProtocolError) as low:
        parse_command_envelope(cmd("goto_slide", "r1", {"slide_index": -1}))
    assert low.value.code == "SLIDE_OUT_OF_RANGE"

    with pytest.raises(ProtocolError) as high:
        parse_command_envelope(cmd("goto_slide", "r1", {"slide_index": 8}))
    assert high.value.code == "SLIDE_OUT_OF_RANGE"


def test_request_id_deduper_bounded() -> None:
    deduper = RequestIdDeduper(limit=3)
    for i in range(5):
        deduper.put(f"r{i}", {"ok": True, "n": i})
    assert len(deduper) == 3
    assert "r0" not in deduper
    assert "r1" not in deduper
    assert deduper.get("r4") == {"ok": True, "n": 4}


def test_extract_lesson_command_payload() -> None:
    envelope = cmd("pause", "abc")
    assert extract_lesson_command_payload(rtvi_command(envelope)) == envelope
    assert extract_lesson_command_payload({"label": "rtvi-ai", "type": "metrics"}) is None


def test_state_snapshot_structure_and_secrets() -> None:
    session, runtime, _, _ = make_session()

    async def _run() -> None:
        await runtime.start_session()
        message = build_state_message(runtime.state, sequence=1)
        assert message["type"] == "lesson.state"
        assert message["version"] == PROTOCOL_VERSION
        assert message["mode"] == "PRESENTING"
        assert message["slide"]["index"] == 0
        assert message["slide"]["number"] == 1
        assert message["slide"]["total"] == 8
        assert message["slide"]["title"]
        assert message["can_pause"] is True
        assert message["can_resume"] is False
        assert message["can_navigate"] is True
        assert_no_secrets(message)
        wrapped = wrap_rtvi_server_message(message)
        assert_no_secrets(wrapped)
        for key in FORBIDDEN_OUTBOUND_KEYS:
            assert key not in str(wrapped)

    asyncio.run(_run())


def test_all_four_commands_and_sequence_monotonic() -> None:
    session, runtime, outbound, sink = make_session()

    async def _run() -> None:
        await runtime.start_session()
        assert session.sequence >= 1
        seq_after_start = session.sequence

        await session.handle_command_payload(cmd("get_state", "g1"))
        assert session.sequence == seq_after_start + 1

        await session.handle_command_payload(cmd("pause", "p1"))
        assert runtime.state.mode is LessonMode.PAUSED
        assert sink.interruption_count >= 1

        await session.handle_command_payload(cmd("resume", "r1"))
        assert runtime.state.mode is LessonMode.PRESENTING

        await session.handle_command_payload(cmd("goto_slide", "go1", {"slide_index": 3}))
        assert runtime.state.cursor.slide_index == 3

        sequences = [
            m["sequence"]
            for m in outbound.lesson_payloads
            if m.get("type") == "lesson.state"
        ]
        assert sequences == sorted(sequences)
        assert sequences[-1] == session.sequence

    asyncio.run(_run())


def test_duplicate_request_id_is_idempotent() -> None:
    session, runtime, outbound, _ = make_session()

    async def _run() -> None:
        await runtime.start_session()
        await session.handle_command_payload(cmd("pause", "dup"))
        slide_before = runtime.state.cursor.slide_index
        count_before = len(outbound.messages)
        await session.handle_command_payload(cmd("pause", "dup"))
        assert runtime.state.mode is LessonMode.PAUSED
        assert runtime.state.cursor.slide_index == slide_before
        # Second call re-sends cached acknowledgement only (no extra state change).
        results = [
            m["data"]
            for m in outbound.messages[count_before:]
            if m["data"].get("type") == "lesson.command_result"
        ]
        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["request_id"] == "dup"

    asyncio.run(_run())


def test_invalid_transition_does_not_mutate() -> None:
    session, runtime, outbound, _ = make_session()

    async def _run() -> None:
        await runtime.start_session()
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        answering = runtime.state
        result2 = await session.handle_command_payload(
            cmd("goto_slide", "bad-goto", {"slide_index": 2})
        )
        assert result2["ok"] is False
        assert result2["error"]["code"] == "INVALID_TRANSITION"
        assert runtime.state is answering
        assert "stack" not in str(result2).lower()
        assert "traceback" not in str(result2).lower()

    asyncio.run(_run())


def test_pause_and_goto_suppress_completion() -> None:
    session, runtime, _, sink = make_session()

    async def _run() -> None:
        await runtime.start_session()
        await runtime.on_bot_started_speaking()
        await session.handle_command_payload(cmd("pause", "p-suppress"))
        assert runtime.state.mode is LessonMode.PAUSED
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.PAUSED
        assert runtime.state.cursor.slide_index == 0

        await session.handle_command_payload(cmd("resume", "r-suppress"))
        await runtime.on_bot_started_speaking()
        await session.handle_command_payload(
            cmd("goto_slide", "g-suppress", {"slide_index": 4})
        )
        assert sink.interruption_count >= 2
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.cursor.slide_index == 4
        assert runtime.state.mode is LessonMode.PRESENTING

    asyncio.run(_run())


def test_per_session_isolation() -> None:
    session_a, runtime_a, _, _ = make_session()
    session_b, runtime_b, _, _ = make_session()

    async def _run() -> None:
        await runtime_a.start_session()
        await runtime_b.start_session()
        await session_a.handle_command_payload(cmd("goto_slide", "a", {"slide_index": 5}))
        assert runtime_a.state.cursor.slide_index == 5
        assert runtime_b.state.cursor.slide_index == 0
        assert session_a.sequence != 0
        assert runtime_a.controller is not runtime_b.controller

    asyncio.run(_run())


def test_serialized_concurrent_events() -> None:
    session, runtime, _, _ = make_session()

    async def _run() -> None:
        await runtime.start_session()
        await runtime.on_bot_started_speaking()

        async def pause_cmd() -> None:
            await session.handle_command_payload(cmd("pause", "race-pause"))

        async def bot_stop() -> None:
            await runtime.on_bot_stopped_speaking()

        await asyncio.gather(pause_cmd(), bot_stop())
        # Either paused without advancing, or still consistent single-slide state.
        assert runtime.state.cursor.slide_index == 0
        assert runtime.state.mode in {LessonMode.PAUSED, LessonMode.PRESENTING}

    asyncio.run(_run())


def test_disconnect_during_command_marks_closed() -> None:
    session, runtime, outbound, _ = make_session()

    async def _run() -> None:
        await runtime.start_session()
        session.mark_closed()
        before = len(outbound.messages)
        await session.handle_transport_message(rtvi_command(cmd("pause", "after-close")))
        assert len(outbound.messages) == before
        await runtime.end_session()
        assert runtime.state.mode is LessonMode.FINISHED

    asyncio.run(_run())


def test_deduper_limit_constant() -> None:
    assert REQUEST_ID_HISTORY_LIMIT == 128
