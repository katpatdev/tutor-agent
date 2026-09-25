#!/usr/bin/env python3
"""Print non-sensitive startup configuration summary. Never prints API keys."""

from __future__ import annotations

import os
from pathlib import Path


def _bool_present(key: str) -> bool:
    val = os.environ.get(key)
    return bool(val and val.strip())


def main() -> int:
    # Load dotenv if present without printing values.
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except Exception:
        pass

    root = Path(__file__).resolve().parents[1]
    print("=== Tutor Agent configuration summary (non-secret) ===")
    print(f"project_root: {root}")
    print(f"OPENAI_API_KEY_present: {_bool_present('OPENAI_API_KEY')}")
    groups = {
        "OpenAI models": [
            "OPENAI_MODERATION_MODEL",
            "OPENAI_EMBEDDING_MODEL",
            "OPENAI_EVAL_MODEL",
            "OPENAI_PROMPT_OPTIMIZER_MODEL",
        ],
        "narration": ["NARRATION_SEGMENT_MAX_CHARACTERS", "TUTOR_PROMPT_VERSION"],
        "safety": ["MODERATION_TIMEOUT_SECONDS", "MODERATION_MAX_CHARACTERS"],
        "RAG": [
            "RAG_TOP_K",
            "RAG_MIN_SIMILARITY",
            "RAG_CHUNK_MAX_CHARACTERS",
            "RAG_MAX_UPLOAD_BYTES",
            "RAG_EMBEDDING_TIMEOUT_SECONDS",
        ],
        "session": [
            "TRANSCRIPT_PERSISTENCE_ENABLED",
            "METRICS_PERSISTENCE_ENABLED",
            "SESSION_RETENTION_DAYS",
            "SESSION_DB_PATH",
            "SESSION_CONFIGURATION_TIMEOUT_SECONDS",
            "TRANSCRIPT_MAX_EVENT_CHARACTERS",
        ],
        "flywheel": ["ALLOW_LIVE_FLYWHEEL_OPENAI", "EVAL_REPETITIONS"],
        "frontend": ["VITE_BOT_API_URL"],
    }
    for title, keys in groups.items():
        print(f"[{title}]")
        for key in keys:
            raw = os.environ.get(key)
            if raw is None:
                print(f"  {key}: (unset — code/default applies)")
            else:
                print(f"  {key}: {raw}")
    print("No secret values printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
