"""OpenAI Embeddings abstraction and non-secret RAG configuration."""

from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Protocol, Sequence

# Verified against openai==3.19.2 EmbeddingModel and OpenAI Embeddings guide.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_TOP_K = 4
DEFAULT_MIN_SIMILARITY = 0.30
DEFAULT_CHUNK_MAX_CHARACTERS = 1600
DEFAULT_CHUNK_OVERLAP_CHARACTERS = 200
DEFAULT_MAX_UPLOAD_BYTES = 5_242_880
DEFAULT_MAX_DOCUMENT_CHARACTERS = 500_000
DEFAULT_MAX_PDF_PAGES = 100
DEFAULT_EMBEDDING_BATCH_SIZE = 32
DEFAULT_EMBEDDING_TIMEOUT_SECONDS = 10.0


class EmbeddingUnavailable(Exception):
    """Raised when the embedding service fails for a non-timeout reason."""


class EmbeddingTimeout(Exception):
    """Raised when embedding exceeds the configured timeout."""


class EmbeddingDimensionError(Exception):
    """Raised when embedding vectors have inconsistent or invalid dimensions."""


class RagConfigError(Exception):
    """Invalid non-secret RAG configuration."""


@dataclass(frozen=True)
class RagConfig:
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    top_k: int = DEFAULT_TOP_K
    min_similarity: float = DEFAULT_MIN_SIMILARITY
    chunk_max_characters: int = DEFAULT_CHUNK_MAX_CHARACTERS
    chunk_overlap_characters: int = DEFAULT_CHUNK_OVERLAP_CHARACTERS
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    max_document_characters: int = DEFAULT_MAX_DOCUMENT_CHARACTERS
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES
    embedding_batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE
    embedding_timeout_seconds: float = DEFAULT_EMBEDDING_TIMEOUT_SECONDS
    upload_enabled: bool = True

    def validate(self) -> None:
        if not self.embedding_model.strip():
            raise RagConfigError("OPENAI_EMBEDDING_MODEL must be a non-empty string")
        if self.top_k < 1:
            raise RagConfigError("RAG_TOP_K must be >= 1")
        if not (-1.0 <= self.min_similarity <= 1.0):
            raise RagConfigError(
                "RAG_MIN_SIMILARITY must be within cosine similarity range [-1, 1]"
            )
        if self.chunk_max_characters < 1:
            raise RagConfigError("RAG_CHUNK_MAX_CHARACTERS must be >= 1")
        if self.chunk_overlap_characters < 0:
            raise RagConfigError("RAG_CHUNK_OVERLAP_CHARACTERS must be >= 0")
        if self.chunk_overlap_characters >= self.chunk_max_characters:
            raise RagConfigError(
                "RAG_CHUNK_OVERLAP_CHARACTERS must be smaller than RAG_CHUNK_MAX_CHARACTERS"
            )
        if self.max_upload_bytes < 1:
            raise RagConfigError("RAG_MAX_UPLOAD_BYTES must be >= 1")
        if self.max_document_characters < 1:
            raise RagConfigError("RAG_MAX_DOCUMENT_CHARACTERS must be >= 1")
        if self.max_pdf_pages < 1:
            raise RagConfigError("RAG_MAX_PDF_PAGES must be >= 1")
        if self.embedding_batch_size < 1:
            raise RagConfigError("RAG_EMBEDDING_BATCH_SIZE must be >= 1")
        if self.embedding_timeout_seconds <= 0:
            raise RagConfigError("RAG_EMBEDDING_TIMEOUT_SECONDS must be > 0")


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise RagConfigError(f"{key} must be an integer") from exc


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise RagConfigError(f"{key} must be a number") from exc


def load_rag_config(environ: Optional[Mapping[str, str]] = None) -> RagConfig:
    env = environ if environ is not None else os.environ
    model = env.get("OPENAI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
    config = RagConfig(
        embedding_model=model,
        top_k=_env_int(env, "RAG_TOP_K", DEFAULT_TOP_K),
        min_similarity=_env_float(env, "RAG_MIN_SIMILARITY", DEFAULT_MIN_SIMILARITY),
        chunk_max_characters=_env_int(
            env, "RAG_CHUNK_MAX_CHARACTERS", DEFAULT_CHUNK_MAX_CHARACTERS
        ),
        chunk_overlap_characters=_env_int(
            env, "RAG_CHUNK_OVERLAP_CHARACTERS", DEFAULT_CHUNK_OVERLAP_CHARACTERS
        ),
        max_upload_bytes=_env_int(env, "RAG_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES),
        max_document_characters=_env_int(
            env, "RAG_MAX_DOCUMENT_CHARACTERS", DEFAULT_MAX_DOCUMENT_CHARACTERS
        ),
        max_pdf_pages=_env_int(env, "RAG_MAX_PDF_PAGES", DEFAULT_MAX_PDF_PAGES),
        embedding_batch_size=_env_int(
            env, "RAG_EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE
        ),
        embedding_timeout_seconds=_env_float(
            env, "RAG_EMBEDDING_TIMEOUT_SECONDS", DEFAULT_EMBEDDING_TIMEOUT_SECONDS
        ),
    )
    config.validate()
    return config


@dataclass(frozen=True)
class EmbeddingResult:
    """Normalized embedding outcome. Must not store the embedded text."""

    vectors: tuple[tuple[float, ...], ...]
    model: str
    dimensions: int


class EmbeddingClient(Protocol):
    async def embed(self, texts: Sequence[str]) -> EmbeddingResult: ...


def _validate_vector(values: Sequence[float], *, expected_dim: Optional[int]) -> tuple[float, ...]:
    if not values:
        raise EmbeddingDimensionError("Empty embedding vector")
    out: List[float] = []
    for v in values:
        fv = float(v)
        if not math.isfinite(fv):
            raise EmbeddingDimensionError("Embedding contains non-finite values")
        out.append(fv)
    if expected_dim is not None and len(out) != expected_dim:
        raise EmbeddingDimensionError(
            f"Inconsistent embedding dimensions: expected {expected_dim}, got {len(out)}"
        )
    return tuple(out)


class OpenAIEmbeddingClient:
    """Production client using openai.AsyncOpenAI.embeddings.create."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = DEFAULT_EMBEDDING_MODEL,
        timeout_seconds: float = DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
        client: Optional[object] = None,
    ) -> None:
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._batch_size = batch_size
        if client is not None:
            self._client = client
        else:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key)

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), model=self._model, dimensions=0)

        all_vectors: List[tuple[float, ...]] = []
        dimensions: Optional[int] = None

        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            try:
                response = await asyncio.wait_for(
                    self._client.embeddings.create(input=batch, model=self._model),
                    timeout=self._timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise EmbeddingTimeout("Embedding request timed out") from exc
            except asyncio.CancelledError:
                raise
            except EmbeddingTimeout:
                raise
            except Exception as exc:  # noqa: BLE001
                raise EmbeddingUnavailable(str(exc) or "Embedding unavailable") from exc

            data = getattr(response, "data", None) or []
            if len(data) != len(batch):
                raise EmbeddingUnavailable(
                    f"Embedding count mismatch: expected {len(batch)}, got {len(data)}"
                )
            # Preserve input order by sorting on index when present.
            ordered = sorted(data, key=lambda item: getattr(item, "index", 0))
            for item in ordered:
                vec = _validate_vector(item.embedding, expected_dim=dimensions)
                if dimensions is None:
                    dimensions = len(vec)
                all_vectors.append(vec)

        assert dimensions is not None
        return EmbeddingResult(
            vectors=tuple(all_vectors),
            model=self._model,
            dimensions=dimensions,
        )


@dataclass
class FakeEmbeddingClient:
    """Deterministic hash-based embeddings for offline tests."""

    dimensions: int = 8
    model: str = "fake-embedding"
    delay_seconds: float = 0.0
    raise_timeout: bool = False
    raise_unavailable: bool = False
    force_dimension: Optional[int] = None
    inject_nonfinite: bool = False
    calls: List[int] = field(default_factory=list)

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        self.calls.append(len(texts))
        if self.raise_timeout:
            raise EmbeddingTimeout("fake timeout")
        if self.raise_unavailable:
            raise EmbeddingUnavailable("fake unavailable")
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)

        dim = self.force_dimension if self.force_dimension is not None else self.dimensions
        vectors: List[tuple[float, ...]] = []
        for text in texts:
            # Stable pseudo-embedding from character codes (content-free call log only).
            base = [((ord(ch) % 97) + 1) / 100.0 for ch in (text[:dim] or "a")]
            while len(base) < dim:
                base.append(0.01 * (len(base) + 1))
            if self.inject_nonfinite:
                base[0] = float("nan")
            vectors.append(_validate_vector(base[:dim], expected_dim=None) if not self.inject_nonfinite else tuple(base[:dim]))
        if self.inject_nonfinite:
            # Force validation path used by callers that re-check.
            raise EmbeddingDimensionError("Embedding contains non-finite values")
        return EmbeddingResult(
            vectors=tuple(vectors),
            model=self.model,
            dimensions=dim,
        )
