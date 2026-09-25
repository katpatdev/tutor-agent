#!/usr/bin/env python3
"""Minimal OpenAI capability check for services used by the tutor agent.

Never prints API keys, generated text, transcripts, embeddings, moderation
scores, or full API responses. One minimal request per required service.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# Load .env via python-dotenv without printing values.
try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except Exception:
    pass

# Application-configured models (match agent.py / safety / RAG defaults).
LLM_MODEL = "gpt-4o"
TTS_MODEL = "gpt-4o-mini-tts"
STT_MODEL = "gpt-4o-transcribe"
MODERATION_MODEL = os.environ.get("OPENAI_MODERATION_MODEL", "omni-moderation-latest").strip()
EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small").strip()

DEFAULT_TIMEOUT_S = 30.0


class CapabilityError(Exception):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def _classify_exception(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = str(exc).lower()
    # Prefer structured OpenAI errors when available.
    status = getattr(exc, "status_code", None)
    if status is None and hasattr(exc, "response"):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    code = getattr(exc, "code", None) or getattr(exc, "type", None)
    code_s = str(code or "").lower()

    if "timeout" in name.lower() or "timed out" in msg or "timeout" in msg:
        return "TIMEOUT"
    if status in {401, 403} or "authentication" in msg or "invalid_api_key" in msg or "incorrect api key" in msg:
        return "AUTHENTICATION_FAILED"
    if status == 429 or "rate_limit" in msg or "rate limit" in msg:
        if "insufficient_quota" in msg or "billing" in msg or "quota" in msg:
            return "QUOTA_OR_BILLING_UNAVAILABLE"
        return "RATE_LIMITED"
    if "insufficient_quota" in msg or "billing" in msg or "payment" in msg:
        return "QUOTA_OR_BILLING_UNAVAILABLE"
    if status == 404 or "model_not_found" in msg or "does not have access" in msg or "not found" in code_s:
        return "MODEL_ACCESS_DENIED"
    if "connection" in msg or "network" in msg or "dns" in msg or "connect" in name.lower():
        return "NETWORK_ERROR"
    if "api_key" in msg and ("missing" in msg or "required" in msg or "empty" in msg):
        return "INVALID_CONFIGURATION"
    return "UNKNOWN_OPENAI_ERROR"


def _key_present() -> bool:
    val = os.environ.get("OPENAI_API_KEY")
    return bool(val and val.strip())


def _make_client():
    from openai import OpenAI

    if not _key_present():
        raise CapabilityError("INVALID_CONFIGURATION")
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), timeout=DEFAULT_TIMEOUT_S)


Row = Tuple[str, str, str, str, str]  # service, model, pass/fail, latency, error category


def _run_check(service: str, model: str, fn: Callable[[], None]) -> Row:
    started = time.perf_counter()
    try:
        fn()
        latency_ms = f"{(time.perf_counter() - started) * 1000.0:.0f}ms"
        return (service, model, "PASS", latency_ms, "-")
    except CapabilityError as exc:
        latency_ms = f"{(time.perf_counter() - started) * 1000.0:.0f}ms"
        return (service, model, "FAIL", latency_ms, exc.category)
    except Exception as exc:  # noqa: BLE001
        latency_ms = f"{(time.perf_counter() - started) * 1000.0:.0f}ms"
        return (service, model, "FAIL", latency_ms, _classify_exception(exc))


def check_auth(client) -> None:
    # Smallest authenticated probe used by the app stack: list models head or chat.
    # Use a tiny chat completion for auth+LLM together is separate; here models.list once.
    client.models.list()


def check_llm(client) -> None:
    client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        max_tokens=3,
        temperature=0,
    )


def check_tts(client, out_path: Path) -> None:
    response = client.audio.speech.create(
        model=TTS_MODEL,
        voice="alloy",
        input="ok",
        response_format="wav",
    )
    # SDK may return binary stream/object with write_to_file or content.
    if hasattr(response, "write_to_file"):
        response.write_to_file(out_path)
    elif hasattr(response, "content"):
        out_path.write_bytes(response.content)
    elif hasattr(response, "read"):
        out_path.write_bytes(response.read())
    else:
        # Streaming response iterator
        chunks = []
        for chunk in response:
            if isinstance(chunk, bytes):
                chunks.append(chunk)
            else:
                chunks.append(bytes(chunk))
        out_path.write_bytes(b"".join(chunks))
    if out_path.stat().st_size < 16:
        raise CapabilityError("UNKNOWN_OPENAI_ERROR")


def check_stt(client, audio_path: Path) -> None:
    with audio_path.open("rb") as fh:
        client.audio.transcriptions.create(
            model=STT_MODEL,
            file=fh,
        )


def check_moderation(client) -> None:
    client.moderations.create(
        model=MODERATION_MODEL,
        input="hello",
    )


def check_embeddings(client) -> None:
    client.embeddings.create(
        model=EMBEDDING_MODEL,
        input="ok",
    )


def _print_table(rows: List[Row]) -> None:
    headers = ("Service", "Model", "Result", "Latency", "Error category")
    widths = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def main() -> int:
    print("OpenAI capability check (minimal; no content logged)")
    print(f"OPENAI_API_KEY present: {_key_present()}")
    if not _key_present():
        print("FAIL: INVALID_CONFIGURATION (API key missing)")
        return 1

    rows: List[Row] = []
    tmp_audio: Optional[Path] = None
    client = None
    try:
        try:
            client = _make_client()
        except CapabilityError as exc:
            rows.append(("authentication", "-", "FAIL", "-", exc.category))
            _print_table(rows)
            return 1
        except Exception as exc:  # noqa: BLE001
            rows.append(("authentication", "-", "FAIL", "-", _classify_exception(exc)))
            _print_table(rows)
            return 1

        auth_row = _run_check("authentication", "-", lambda: check_auth(client))
        rows.append(auth_row)
        if auth_row[2] != "PASS":
            _print_table(rows)
            print("Stopping: authentication failed; no further OpenAI calls.")
            return 1

        rows.append(_run_check("llm", LLM_MODEL, lambda: check_llm(client)))
        if rows[-1][2] != "PASS" and rows[-1][4] == "AUTHENTICATION_FAILED":
            _print_table(rows)
            print("Stopping: authentication failed.")
            return 1

        fd, tmp_name = tempfile.mkstemp(prefix="tutor_tts_", suffix=".wav")
        os.close(fd)
        tmp_audio = Path(tmp_name)
        rows.append(
            _run_check("tts", TTS_MODEL, lambda: check_tts(client, tmp_audio))
        )
        if rows[-1][2] == "PASS":
            rows.append(
                _run_check("stt", STT_MODEL, lambda: check_stt(client, tmp_audio))
            )
        else:
            rows.append(("stt", STT_MODEL, "FAIL", "-", "INVALID_CONFIGURATION"))

        rows.append(
            _run_check("moderation", MODERATION_MODEL, lambda: check_moderation(client))
        )
        rows.append(
            _run_check("embeddings", EMBEDDING_MODEL, lambda: check_embeddings(client))
        )
    finally:
        if tmp_audio is not None:
            try:
                tmp_audio.unlink(missing_ok=True)
            except OSError:
                pass

    _print_table(rows)
    failed = [r for r in rows if r[2] != "PASS"]
    if failed:
        print(f"FAIL: {len(failed)} required service check(s) failed.")
        return 1
    print("PASS: all required OpenAI service checks succeeded.")
    print("No generated content, transcripts, embeddings, or keys were printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
