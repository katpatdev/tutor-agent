"""Session persistence configuration (non-secret)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


class SessionConfigError(Exception):
    """Invalid session/metrics/transcript configuration."""


@dataclass(frozen=True)
class SessionDataConfig:
    transcript_persistence_enabled: bool = False
    metrics_persistence_enabled: bool = True
    session_retention_days: int = 30
    session_db_path: str = "data/tutor_sessions.sqlite3"
    configuration_timeout_seconds: float = 5.0
    transcript_max_event_characters: int = 4000
    schema_version: int = 2

    def validate(self) -> None:
        if self.session_retention_days < 1:
            raise SessionConfigError(
                "SESSION_RETENTION_DAYS must be >= 1 (zero is rejected; it does not mean delete-all)"
            )
        if not self.session_db_path.strip():
            raise SessionConfigError("SESSION_DB_PATH must be a non-empty path")
        if self.configuration_timeout_seconds <= 0:
            raise SessionConfigError("SESSION_CONFIGURATION_TIMEOUT_SECONDS must be > 0")
        if self.transcript_max_event_characters < 1:
            raise SessionConfigError("TRANSCRIPT_MAX_EVENT_CHARACTERS must be >= 1")


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise SessionConfigError(f"{key} must be a boolean")


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise SessionConfigError(f"{key} must be an integer") from exc


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise SessionConfigError(f"{key} must be a number") from exc


def load_session_data_config(
    environ: Optional[Mapping[str, str]] = None,
) -> SessionDataConfig:
    env = environ if environ is not None else os.environ
    config = SessionDataConfig(
        transcript_persistence_enabled=_env_bool(
            env, "TRANSCRIPT_PERSISTENCE_ENABLED", False
        ),
        metrics_persistence_enabled=_env_bool(
            env, "METRICS_PERSISTENCE_ENABLED", True
        ),
        session_retention_days=_env_int(env, "SESSION_RETENTION_DAYS", 30),
        session_db_path=env.get("SESSION_DB_PATH", "data/tutor_sessions.sqlite3").strip(),
        configuration_timeout_seconds=_env_float(
            env, "SESSION_CONFIGURATION_TIMEOUT_SECONDS", 5.0
        ),
        transcript_max_event_characters=_env_int(
            env, "TRANSCRIPT_MAX_EVENT_CHARACTERS", 4000
        ),
    )
    config.validate()
    return config
