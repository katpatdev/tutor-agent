"""Process-local in-memory vector store for knowledge chunks."""

from __future__ import annotations

import asyncio
import math
import threading
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


class VectorStoreError(Exception):
    """Invalid vector-store operation."""


@dataclass(frozen=True)
class StoredChunk:
    document_id: str
    document_name: str
    content_hash: str
    chunk_index: int
    text: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    embedding: tuple[float, ...] = ()


@dataclass(frozen=True)
class SearchHit:
    document_id: str
    document_name: str
    chunk_index: int
    page_start: Optional[int]
    page_end: Optional[int]
    similarity: float
    text: str


@dataclass
class DocumentRecord:
    document_id: str
    name: str
    content_hash: str
    chunk_count: int
    status: str = "ready"


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise VectorStoreError(
            f"Cannot compare vectors of different dimensions ({len(a)} vs {len(b)})"
        )
    if not a:
        raise VectorStoreError("Cannot compare empty vectors")
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        fx = float(x)
        fy = float(y)
        if not math.isfinite(fx) or not math.isfinite(fy):
            raise VectorStoreError("Vector contains non-finite values")
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class InMemoryKnowledgeStore:
    """Application-wide in-memory store (one process). Not multi-worker safe across processes."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._thread_lock = threading.RLock()
        self._documents: Dict[str, DocumentRecord] = {}
        self._hash_to_id: Dict[str, str] = {}
        self._chunks: List[StoredChunk] = []
        self._dimension: Optional[int] = None
        self._embedding_model: Optional[str] = None

    @property
    def document_count(self) -> int:
        with self._thread_lock:
            return len(self._documents)

    @property
    def chunk_count(self) -> int:
        with self._thread_lock:
            return len(self._chunks)

    @property
    def dimension(self) -> Optional[int]:
        with self._thread_lock:
            return self._dimension

    @property
    def embedding_model(self) -> Optional[str]:
        with self._thread_lock:
            return self._embedding_model

    def find_by_content_hash(self, content_hash: str) -> Optional[DocumentRecord]:
        with self._thread_lock:
            doc_id = self._hash_to_id.get(content_hash)
            if not doc_id:
                return None
            return self._documents.get(doc_id)

    async def insert_document(
        self,
        *,
        name: str,
        content_hash: str,
        chunks: Sequence[StoredChunk],
        embedding_model: str,
        dimension: int,
    ) -> DocumentRecord:
        async with self._lock:
            with self._thread_lock:
                existing = self._hash_to_id.get(content_hash)
                if existing:
                    return self._documents[existing]

                if self._dimension is not None and self._dimension != dimension:
                    raise VectorStoreError(
                        "Embedding dimension does not match existing store dimension"
                    )
                if self._embedding_model is not None and self._embedding_model != embedding_model:
                    raise VectorStoreError(
                        "Embedding model does not match existing store model"
                    )
                for chunk in chunks:
                    if len(chunk.embedding) != dimension:
                        raise VectorStoreError("Chunk embedding dimension mismatch")
                    for v in chunk.embedding:
                        if not math.isfinite(float(v)):
                            raise VectorStoreError("Chunk embedding has non-finite values")

                document_id = str(uuid.uuid4())
                stored = [
                    StoredChunk(
                        document_id=document_id,
                        document_name=name,
                        content_hash=content_hash,
                        chunk_index=c.chunk_index,
                        text=c.text,
                        page_start=c.page_start,
                        page_end=c.page_end,
                        embedding=c.embedding,
                    )
                    for c in chunks
                ]
                record = DocumentRecord(
                    document_id=document_id,
                    name=name,
                    content_hash=content_hash,
                    chunk_count=len(stored),
                    status="ready",
                )
                # Atomic commit: assign all fields only after validation.
                self._documents[document_id] = record
                self._hash_to_id[content_hash] = document_id
                self._chunks.extend(stored)
                self._dimension = dimension
                self._embedding_model = embedding_model
                return record

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int,
        min_similarity: float,
    ) -> List[SearchHit]:
        async with self._lock:
            with self._thread_lock:
                if not self._chunks:
                    return []
                if self._dimension is None:
                    return []
                if len(query_embedding) != self._dimension:
                    raise VectorStoreError(
                        f"Query dimension {len(query_embedding)} != store {self._dimension}"
                    )
                scored: List[Tuple[float, int, str, StoredChunk]] = []
                for chunk in self._chunks:
                    sim = cosine_similarity(query_embedding, chunk.embedding)
                    if sim < min_similarity:
                        continue
                    # Stable tie-break: similarity desc, document_id asc, chunk_index asc
                    scored.append((sim, chunk.chunk_index, chunk.document_id, chunk))
                scored.sort(key=lambda item: (-item[0], item[2], item[1]))
                hits: List[SearchHit] = []
                for sim, _, _, chunk in scored[: max(0, top_k)]:
                    hits.append(
                        SearchHit(
                            document_id=chunk.document_id,
                            document_name=chunk.document_name,
                            chunk_index=chunk.chunk_index,
                            page_start=chunk.page_start,
                            page_end=chunk.page_end,
                            similarity=sim,
                            text=chunk.text,
                        )
                    )
                return hits

    def clear_for_tests(self) -> None:
        """Test helper only — not exposed via HTTP."""
        with self._thread_lock:
            self._documents.clear()
            self._hash_to_id.clear()
            self._chunks.clear()
            self._dimension = None
            self._embedding_model = None


# Process-wide shared store (one Python process).
SHARED_KNOWLEDGE_STORE = InMemoryKnowledgeStore()
