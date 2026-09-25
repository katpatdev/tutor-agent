"""Versioned lesson control protocol (OpenAI-independent).

Wire transport:
  Client -> Server: RTVI ``client-message`` with ``t="lesson.command"``
  Server -> Client: RTVI ``server-message`` whose ``data`` is a lesson envelope

Protocol version is always 1 for this module.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Set

from curriculum import TOTAL_SLIDES, slide_title
from lesson_controller import InvalidLessonTransition, LessonMode, LessonState
from presentation_runtime import PresentationRuntime

PROTOCOL_VERSION = 1
MSG_COMMAND = "lesson.command"
MSG_STATE = "lesson.state"
MSG_RESULT = "lesson.command_result"
MSG_SESSION_READY = "session.ready"
MSG_SESSION_CONFIGURE = "session.configure"
MSG_SESSION_CONFIGURE_RESULT = "session.configure_result"
SUPPORTED_COMMANDS = frozenset({"pause", "resume", "goto_slide", "get_state"})
REQUEST_ID_HISTORY_LIMIT = 128

RTVI_LABEL = "rtvi-ai"
RTVI_CLIENT_MESSAGE = "client-message"
RTVI_SERVER_MESSAGE = "server-message"
LESSON_COMMAND_TYPE = "lesson.command"
SESSION_CONFIGURE_TYPE = "session.configure"


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ParsedCommand:
    request_id: str
    command: str
    payload: Dict[str, Any]


SendOutbound = Callable[[Mapping[str, Any]], Awaitable[None]]


def build_ws_url(*, scheme: str, host: str, path: str = "/ws") -> str:
    """Map HTTP(S) request scheme/host to a WebSocket URL."""
    normalized = scheme.lower()
    if normalized in {"https", "wss"}:
        ws_scheme = "wss"
    elif normalized in {"http", "ws"}:
        ws_scheme = "ws"
    else:
        raise ValueError(f"Unsupported scheme for WebSocket URL: {scheme}")
    host = host.strip()
    if not host:
        raise ValueError("host must be non-empty")
    if not path.startswith("/"):
        path = "/" + path
    return f"{ws_scheme}://{host}{path}"


def control_availability(
    state: LessonState, *, safety_status: str = "normal"
) -> Dict[str, bool]:
    if safety_status == "hold":
        return {
            "paused": True,
            "can_pause": False,
            "can_resume": False,
            "can_navigate": False,
        }
    paused = state.mode is LessonMode.PAUSED
    can_pause = state.mode in {
        LessonMode.PRESENTING,
        LessonMode.INTERRUPTED,
        LessonMode.ANSWERING,
        LessonMode.QA_MODE,
    }
    can_resume = paused
    can_navigate = state.mode in {LessonMode.PRESENTING, LessonMode.QA_MODE}
    if safety_status == "redirecting":
        can_pause = False
        can_resume = False
        can_navigate = False
    return {
        "paused": paused,
        "can_pause": can_pause,
        "can_resume": can_resume,
        "can_navigate": can_navigate,
    }


def build_state_message(
    state: LessonState,
    *,
    sequence: int,
    safety_status: str = "normal",
    safety_notice: Optional[str] = None,
) -> Dict[str, Any]:
    index = state.cursor.slide_index
    flags = control_availability(state, safety_status=safety_status)
    return {
        "type": MSG_STATE,
        "version": PROTOCOL_VERSION,
        "sequence": sequence,
        "mode": state.mode.name,
        "slide": {
            "index": index,
            "number": index + 1,
            "total": state.slide_count,
            "title": slide_title(index) if 0 <= index < TOTAL_SLIDES else f"Slide {index + 1}",
        },
        "paused": flags["paused"],
        "can_pause": flags["can_pause"],
        "can_resume": flags["can_resume"],
        "can_navigate": flags["can_navigate"],
        "safety_status": safety_status,
        "safety_notice": safety_notice,
    }


def build_command_result(
    request_id: str, *, ok: bool, code: Optional[str] = None, message: Optional[str] = None
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "type": MSG_RESULT,
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": ok,
    }
    if not ok:
        result["error"] = {"code": code or "INVALID_REQUEST", "message": message or "Rejected"}
    return result


def wrap_rtvi_server_message(data: Mapping[str, Any]) -> Dict[str, Any]:
    return {"label": RTVI_LABEL, "type": RTVI_SERVER_MESSAGE, "data": dict(data)}


def extract_rtvi_client_envelope(transport_message: Any) -> Optional[tuple[str, Any]]:
    """Return ``(message_type, payload)`` from an RTVI client-message, if present."""
    if not isinstance(transport_message, Mapping):
        return None
    if transport_message.get("label") != RTVI_LABEL:
        return None
    if transport_message.get("type") != RTVI_CLIENT_MESSAGE:
        return None
    data = transport_message.get("data")
    if not isinstance(data, Mapping):
        return None
    msg_type = data.get("t")
    if not isinstance(msg_type, str) or not msg_type:
        return None
    return msg_type, data.get("d")


def extract_lesson_command_payload(transport_message: Any) -> Optional[Any]:
    """Return the lesson.command envelope from an RTVI client-message, if present."""
    extracted = extract_rtvi_client_envelope(transport_message)
    if extracted is None:
        return None
    msg_type, payload = extracted
    if msg_type != LESSON_COMMAND_TYPE:
        return None
    return payload


def build_session_ready(
    *,
    session_id: str,
    transcript_persistence_available: bool,
) -> Dict[str, Any]:
    return {
        "type": MSG_SESSION_READY,
        "version": PROTOCOL_VERSION,
        "session_id": session_id,
        "transcript_persistence_available": transcript_persistence_available,
    }


def build_session_configure_result(
    request_id: str,
    *,
    ok: bool,
    transcript_active: bool,
    reason: str,
    code: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "type": MSG_SESSION_CONFIGURE_RESULT,
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": ok,
        "transcript_active": transcript_active,
        "reason": reason,
    }
    if not ok:
        result["error"] = {
            "code": code or "INVALID_REQUEST",
            "message": message or "Rejected",
        }
    return result


def parse_session_configure(raw: Any) -> tuple[str, bool]:
    if not isinstance(raw, Mapping):
        raise ProtocolError("MALFORMED_MESSAGE", "Configure must be a JSON object.")
    if raw.get("type") != MSG_SESSION_CONFIGURE:
        raise ProtocolError("MALFORMED_MESSAGE", "Unsupported message type.")
    if raw.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("UNSUPPORTED_VERSION", "Unsupported protocol version.")
    request_id = raw.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ProtocolError("INVALID_REQUEST_ID", "request_id must be a non-empty string.")
    consent = raw.get("transcript_consent")
    if not isinstance(consent, bool):
        raise ProtocolError(
            "INVALID_CONSENT",
            "transcript_consent must be a Boolean.",
        )
    return request_id, consent


def parse_command_envelope(raw: Any) -> ParsedCommand:
    if not isinstance(raw, Mapping):
        raise ProtocolError("MALFORMED_MESSAGE", "Command must be a JSON object.")
    if raw.get("type") != MSG_COMMAND:
        raise ProtocolError("MALFORMED_MESSAGE", "Unsupported message type.")
    if raw.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("UNSUPPORTED_VERSION", "Unsupported protocol version.")
    request_id = raw.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ProtocolError("INVALID_REQUEST_ID", "request_id must be a non-empty string.")
    command = raw.get("command")
    if not isinstance(command, str) or command not in SUPPORTED_COMMANDS:
        raise ProtocolError("UNKNOWN_COMMAND", "Unknown or missing command.")
    payload = raw.get("payload", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise ProtocolError("INVALID_PAYLOAD", "payload must be an object.")
    payload_dict = dict(payload)

    if command == "goto_slide":
        if "slide_index" not in payload_dict:
            raise ProtocolError("MISSING_SLIDE_INDEX", "goto_slide requires slide_index.")
        slide_index = payload_dict["slide_index"]
        if isinstance(slide_index, bool) or not isinstance(slide_index, int):
            raise ProtocolError(
                "INVALID_SLIDE_INDEX",
                "slide_index must be an integer (Boolean values are not allowed).",
            )
        if slide_index < 0 or slide_index >= TOTAL_SLIDES:
            raise ProtocolError(
                "SLIDE_OUT_OF_RANGE",
                f"slide_index must be in [0, {TOTAL_SLIDES}).",
            )

    return ParsedCommand(request_id=request_id, command=command, payload=payload_dict)


class RequestIdDeduper:
    """Bounded idempotency cache for client request_id values."""

    def __init__(self, limit: int = REQUEST_ID_HISTORY_LIMIT) -> None:
        self._limit = limit
        self._results: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        cached = self._results.get(request_id)
        if cached is None:
            return None
        self._results.move_to_end(request_id)
        return dict(cached)

    def put(self, request_id: str, result: Mapping[str, Any]) -> None:
        self._results[request_id] = dict(result)
        self._results.move_to_end(request_id)
        while len(self._results) > self._limit:
            self._results.popitem(last=False)

    def __contains__(self, request_id: str) -> bool:
        return request_id in self._results

    def __len__(self) -> int:
        return len(self._results)


class LessonProtocolSession:
    """Per-WebSocket protocol bridge: validate commands, call runtime, publish state."""

    def __init__(
        self,
        runtime: PresentationRuntime,
        send_outbound: SendOutbound,
        *,
        request_history_limit: int = REQUEST_ID_HISTORY_LIMIT,
        observability: Any = None,
        transcript_persistence_available: bool = False,
        on_configured_start: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self._runtime = runtime
        self._send_outbound = send_outbound
        self._deduper = RequestIdDeduper(limit=request_history_limit)
        self._sequence = 0
        self._closed = False
        self._observability = observability
        self._transcript_persistence_available = transcript_persistence_available
        self._on_configured_start = on_configured_start
        self._configure_deduper: Dict[str, Dict[str, Any]] = {}

    @property
    def sequence(self) -> int:
        return self._sequence

    async def publish_session_ready(self) -> Dict[str, Any]:
        session_id = (
            self._observability.session_id
            if self._observability is not None
            else "unknown"
        )
        message = build_session_ready(
            session_id=session_id,
            transcript_persistence_available=self._transcript_persistence_available,
        )
        await self._send_outbound(wrap_rtvi_server_message(message))
        return message

    async def publish_state(self) -> Dict[str, Any]:
        self._sequence += 1
        message = build_state_message(
            self._runtime.state,
            sequence=self._sequence,
            safety_status=self._runtime.safety_status.value,
            safety_notice=self._runtime.safety_notice,
        )
        await self._send_outbound(wrap_rtvi_server_message(message))
        return message

    async def handle_transport_message(self, transport_message: Any) -> None:
        if self._closed:
            return
        extracted = extract_rtvi_client_envelope(transport_message)
        if extracted is None:
            return
        msg_type, payload = extracted
        if msg_type == SESSION_CONFIGURE_TYPE:
            await self.handle_session_configure(payload)
            return
        if msg_type == LESSON_COMMAND_TYPE:
            await self.handle_command_payload(payload)

    async def handle_session_configure(self, raw: Any) -> Dict[str, Any]:
        try:
            request_id, consent = parse_session_configure(raw)
        except ProtocolError as exc:
            request_id = "unknown"
            if isinstance(raw, Mapping) and isinstance(raw.get("request_id"), str):
                request_id = raw["request_id"]
            result = build_session_configure_result(
                request_id,
                ok=False,
                transcript_active=False,
                reason="error",
                code=exc.code,
                message=exc.message,
            )
            await self._send_outbound(wrap_rtvi_server_message(result))
            return result

        if request_id in self._configure_deduper:
            cached = self._configure_deduper[request_id]
            await self._send_outbound(wrap_rtvi_server_message(cached))
            return cached

        if self._observability is not None and self._observability.lesson_started:
            result = build_session_configure_result(
                request_id,
                ok=False,
                transcript_active=self._observability.collector.transcript_storage_active,
                reason="late_change_rejected",
                code="CONSENT_LOCKED",
                message="Transcript consent cannot change after the lesson starts. Reconnect to change it.",
            )
            await self._send_outbound(wrap_rtvi_server_message(result))
            self._configure_deduper[request_id] = result
            return result

        reason = "declined"
        if self._observability is not None:
            reason = self._observability.mark_configured(transcript_consent=consent)
        transcript_active = reason == "enabled"
        result = build_session_configure_result(
            request_id,
            ok=True,
            transcript_active=transcript_active,
            reason=reason,
        )
        await self._send_outbound(wrap_rtvi_server_message(result))
        self._configure_deduper[request_id] = result
        if self._on_configured_start is not None and (
            self._observability is None or not self._observability.lesson_started
        ):
            await self._on_configured_start()
        return result

    async def handle_command_payload(self, raw: Any) -> Dict[str, Any]:
        try:
            parsed = parse_command_envelope(raw)
        except ProtocolError as exc:
            # Malformed messages without a usable request_id cannot be acknowledged.
            request_id = None
            if isinstance(raw, Mapping) and isinstance(raw.get("request_id"), str):
                request_id = raw["request_id"]
            if request_id:
                result = build_command_result(
                    request_id, ok=False, code=exc.code, message=exc.message
                )
                await self._send_outbound(wrap_rtvi_server_message(result))
                return result
            return build_command_result(
                "unknown", ok=False, code=exc.code, message=exc.message
            )

        cached = self._deduper.get(parsed.request_id)
        if cached is not None:
            await self._send_outbound(wrap_rtvi_server_message(cached))
            return cached

        try:
            result = await self._dispatch_command(parsed)
        except InvalidLessonTransition as exc:
            result = build_command_result(
                parsed.request_id,
                ok=False,
                code="INVALID_TRANSITION",
                message=str(exc) or "The requested action is not valid in the current lesson state.",
            )
            await self._send_outbound(wrap_rtvi_server_message(result))
            self._deduper.put(parsed.request_id, result)
            return result
        except ProtocolError as exc:
            result = build_command_result(
                parsed.request_id, ok=False, code=exc.code, message=exc.message
            )
            await self._send_outbound(wrap_rtvi_server_message(result))
            self._deduper.put(parsed.request_id, result)
            return result

        self._deduper.put(parsed.request_id, result)
        return result

    async def _dispatch_command(self, parsed: ParsedCommand) -> Dict[str, Any]:
        if parsed.command == "get_state":
            result = build_command_result(parsed.request_id, ok=True)
            await self._send_outbound(wrap_rtvi_server_message(result))
            await self.publish_state()
            return result

        if parsed.command == "pause":
            await self._runtime.pause()
            if self._observability is not None:
                self._observability.collector.note_pause()
        elif parsed.command == "resume":
            await self._runtime.resume()
            if self._observability is not None:
                self._observability.collector.note_resume()
        elif parsed.command == "goto_slide":
            await self._runtime.go_to_slide(int(parsed.payload["slide_index"]))
            if self._observability is not None:
                self._observability.collector.note_navigation()
        else:
            raise ProtocolError("UNKNOWN_COMMAND", "Unknown command.")

        # State publication is performed by PresentationRuntime.on_state_changed.
        result = build_command_result(parsed.request_id, ok=True)
        await self._send_outbound(wrap_rtvi_server_message(result))
        return result

    def mark_closed(self) -> None:
        self._closed = True


# Secret field names that must never appear in outbound protocol messages.
FORBIDDEN_OUTBOUND_KEYS: Set[str] = {
    "api_key",
    "openai_api_key",
    "OPENAI_API_KEY",
    "authorization",
    "password",
    "token",
    "secret",
    "system_prompt",
    "stack",
    "traceback",
}


def assert_no_secrets(message: Mapping[str, Any]) -> None:
    def _walk(obj: Any) -> None:
        if isinstance(obj, Mapping):
            for key, value in obj.items():
                if str(key).lower() in {k.lower() for k in FORBIDDEN_OUTBOUND_KEYS}:
                    raise AssertionError(f"Forbidden outbound key: {key}")
                _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(message)
