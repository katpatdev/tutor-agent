"""OpenAI Moderation abstraction (application-owned, no raw transcript storage)."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Protocol, Sequence

# Verified against openai==3.19.2 ModerationModel literal and OpenAI Moderations API.
DEFAULT_MODERATION_MODEL = "omni-moderation-latest"
DEFAULT_TIMEOUT_SECONDS = 3.0
DEFAULT_MAX_CHARACTERS = 12000


class ModerationUnavailable(Exception):
    """Raised when the moderation service fails for a non-timeout reason."""


class ModerationTimeout(Exception):
    """Raised when moderation exceeds the configured timeout."""


class SafetyConfigError(Exception):
    """Invalid non-secret safety configuration."""


@dataclass(frozen=True)
class SafetyConfig:
    moderation_model: str = DEFAULT_MODERATION_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_characters: int = DEFAULT_MAX_CHARACTERS

    def validate(self) -> None:
        if not self.moderation_model.strip():
            raise SafetyConfigError("OPENAI_MODERATION_MODEL must be a non-empty string")
        if self.timeout_seconds <= 0:
            raise SafetyConfigError("MODERATION_TIMEOUT_SECONDS must be > 0")
        if self.max_characters < 1:
            raise SafetyConfigError("MODERATION_MAX_CHARACTERS must be >= 1")


def load_safety_config(environ: Optional[Mapping[str, str]] = None) -> SafetyConfig:
    env = environ if environ is not None else os.environ
    model = env.get("OPENAI_MODERATION_MODEL", DEFAULT_MODERATION_MODEL).strip()
    try:
        timeout = float(env.get("MODERATION_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    except ValueError as exc:
        raise SafetyConfigError("MODERATION_TIMEOUT_SECONDS must be a number") from exc
    try:
        max_chars = int(env.get("MODERATION_MAX_CHARACTERS", str(DEFAULT_MAX_CHARACTERS)))
    except ValueError as exc:
        raise SafetyConfigError("MODERATION_MAX_CHARACTERS must be an integer") from exc
    config = SafetyConfig(
        moderation_model=model,
        timeout_seconds=timeout,
        max_characters=max_chars,
    )
    config.validate()
    return config


@dataclass(frozen=True)
class ModerationCategory:
    name: str
    flagged: bool
    score: float


@dataclass(frozen=True)
class ModerationResult:
    """Normalized moderation outcome. Must not contain the moderated text."""

    flagged: bool
    categories: tuple[ModerationCategory, ...] = field(default_factory=tuple)
    request_id: Optional[str] = None

    @property
    def flagged_category_names(self) -> List[str]:
        return [c.name for c in self.categories if c.flagged]

    def score_map(self) -> Dict[str, float]:
        return {c.name: c.score for c in self.categories}


class ModerationClient(Protocol):
    async def moderate(self, text: str) -> ModerationResult: ...


class OpenAIModerationClient:
    """Production client using openai.AsyncOpenAI.moderations.create."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODERATION_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: Optional[object] = None,
    ) -> None:
        self._model = model
        self._timeout_seconds = timeout_seconds
        if client is not None:
            self._client = client
        else:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key)

    async def moderate(self, text: str) -> ModerationResult:
        try:
            response = await asyncio.wait_for(
                self._client.moderations.create(input=text, model=self._model),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise ModerationTimeout("Moderation request timed out") from exc
        except asyncio.CancelledError:
            raise
        except ModerationTimeout:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider errors
            raise ModerationUnavailable(str(exc) or "Moderation unavailable") from exc

        if not getattr(response, "results", None):
            raise ModerationUnavailable("Empty moderation response")

        first = response.results[0]
        categories_obj = first.categories
        scores_obj = first.category_scores
        cats: List[ModerationCategory] = []
        for name in sorted(categories_obj.model_dump().keys()):
            cats.append(
                ModerationCategory(
                    name=name,
                    flagged=bool(getattr(categories_obj, name)),
                    score=float(getattr(scores_obj, name)),
                )
            )
        return ModerationResult(
            flagged=bool(first.flagged),
            categories=tuple(cats),
            request_id=getattr(response, "id", None),
        )


@dataclass
class FakeModerationClient:
    """Deterministic test double. Maps substrings → ModerationResult."""

    default: ModerationResult = field(
        default_factory=lambda: ModerationResult(flagged=False, categories=())
    )
    rules: Sequence[tuple[str, ModerationResult]] = ()
    delay_seconds: float = 0.0
    raise_timeout: bool = False
    raise_unavailable: bool = False
    calls: List[str] = field(default_factory=list)

    async def moderate(self, text: str) -> ModerationResult:
        # Record only a non-reversible fingerprint for test assertions (not the text).
        self.calls.append(f"len={len(text)}")
        if self.raise_timeout:
            raise ModerationTimeout("fake timeout")
        if self.raise_unavailable:
            raise ModerationUnavailable("fake unavailable")
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        lowered = text.lower()
        for needle, result in self.rules:
            if needle.lower() in lowered:
                return result
        return self.default


def flagged_result(*category_names: str, score: float = 0.95) -> ModerationResult:
    cats = [
        ModerationCategory(name=name, flagged=True, score=score) for name in category_names
    ]
    return ModerationResult(flagged=True, categories=tuple(cats), request_id="fake")
