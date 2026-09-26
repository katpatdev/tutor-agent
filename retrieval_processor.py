"""Pipecat retrieval processor: post-safety, pre-user-aggregator RAG context."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, List, Optional, Sequence

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    InputTransportMessageFrame,
    LLMMessagesAppendFrame,
    LLMMessagesTransformFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from embedding_service import (
    EmbeddingClient,
    EmbeddingTimeout,
    EmbeddingUnavailable,
    EmbeddingDimensionError,
    RagConfig,
)
from knowledge_ingestion import (
    build_rag_system_message,
    format_source_heading,
    is_conversational_ack,
    looks_like_question,
    source_label,
    strip_rag_messages,
)
from knowledge_store import InMemoryKnowledgeStore, SearchHit, VectorStoreError
from lesson_controller import LessonMode
from lesson_context import (
    lesson_context_message_dict,
    strip_lesson_context_messages,
)
from lesson_protocol import wrap_rtvi_server_message
from presentation_runtime import OutputPurpose, PresentationRuntime
from safety_policy import SafetyStatus


SendRetrievalMessage = Callable[[dict], Awaitable[None]]


@dataclass(frozen=True)
class RetrievalEvent:
    timestamp: float
    query_id: str
    attempted: bool
    store_empty: bool
    match_count: int
    source_ids: tuple[str, ...]
    embedding_latency_ms: Optional[float]
    retrieval_latency_ms: Optional[float]
    fallback_reason: Optional[str]


@dataclass
class SessionRetrievalState:
    """Per-session RAG diagnostics (no raw question/chunk text)."""

    events: List[RetrievalEvent] = field(default_factory=list)
    last_query_id: Optional[str] = None


class RetrievalProcessor(FrameProcessor):
    """Embed allowed student questions and inject temporary RAG context."""

    def __init__(
        self,
        *,
        runtime: PresentationRuntime,
        store: InMemoryKnowledgeStore,
        embedding_client: EmbeddingClient,
        config: RagConfig,
        send_retrieval_message: Optional[SendRetrievalMessage] = None,
        session_state: Optional[SessionRetrievalState] = None,
        observability=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._runtime = runtime
        self._store = store
        self._client = embedding_client
        self._config = config
        self._send = send_retrieval_message
        self._session = session_state or SessionRetrievalState()
        self._observability = observability

    @property
    def session_state(self) -> SessionRetrievalState:
        return self._session

    def _should_retrieve(self, text: str) -> bool:
        if self._runtime.session_ended:
            return False
        if self._runtime.safety_status is not SafetyStatus.NORMAL:
            return False
        if is_conversational_ack(text):
            return False
        purpose = self._runtime.output_purpose
        if purpose in {
            OutputPurpose.SLIDE_NARRATION,
            OutputPurpose.RESUMED_NARRATION,
            OutputPurpose.SAFETY_REDIRECT,
            OutputPurpose.SAFETY_MESSAGE,
            OutputPurpose.QA_TRANSITION,
        }:
            return False
        mode = self._runtime.state.mode
        if mode is LessonMode.QA_MODE:
            # Q&A turns generally need lesson/document grounding; acks already skipped.
            return True
        if mode in {LessonMode.ANSWERING, LessonMode.INTERRUPTED, LessonMode.PRESENTING}:
            return looks_like_question(text)
        return False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterimTranscriptionFrame):
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputTransportMessageFrame):
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if not text:
                return

            self._runtime.note_student_question(text)

            if not self._should_retrieve(text):
                await self.push_frame(
                    LLMMessagesTransformFrame(
                        transform=self._strip_and_inject_lesson_context,
                        run_llm=False,
                    ),
                    direction,
                )
                await self.push_frame(frame, direction)
                return

            await self._retrieve_and_inject(text, frame, direction)
            return

        await self.push_frame(frame, direction)

    def _strip_and_inject_lesson_context(self, messages):
        cleaned = strip_lesson_context_messages(strip_rag_messages(messages))
        snap = self._runtime.build_lesson_context_snapshot(
            latest_question=self._runtime._latest_student_question  # noqa: SLF001
        )
        cleaned.append(lesson_context_message_dict(snap))
        return cleaned

    async def _retrieve_and_inject(
        self, text: str, frame: TranscriptionFrame, direction: FrameDirection
    ) -> None:
        query_id = str(uuid.uuid4())
        self._session.last_query_id = query_id
        store_empty = self._store.chunk_count == 0
        embed_ms: Optional[float] = None
        search_ms: Optional[float] = None
        hits: List[SearchHit] = []
        fallback: Optional[str] = None

        # Strip prior RAG + lesson context; re-inject authoritative snapshot.
        await self.push_frame(
            LLMMessagesTransformFrame(
                transform=self._strip_and_inject_lesson_context,
                run_llm=False,
            ),
            direction,
        )

        if store_empty:
            fallback = "store_empty"
            self._record(
                query_id=query_id,
                attempted=True,
                store_empty=True,
                match_count=0,
                source_ids=(),
                embed_ms=None,
                search_ms=None,
                fallback=fallback,
            )
            if self._observability is not None:
                try:
                    self._observability.collector.note_answer_stage("rag_skipped_empty")
                    self._observability.record_rag_event(
                        attempted=True,
                        hit_count=0,
                        source_ids=(),
                        embedding_latency_ms=None,
                        retrieval_latency_ms=None,
                        fallback_reason=fallback,
                    )
                except Exception:  # noqa: BLE001
                    pass
            await self._emit_sources(query_id, [], status="no_match")
            await self.push_frame(frame, direction)
            return

        if self._observability is not None:
            try:
                self._observability.collector.note_answer_stage("rag_start")
            except Exception:  # noqa: BLE001
                pass

        try:
            t0 = time.perf_counter()
            embedded = await self._client.embed([text])
            embed_ms = (time.perf_counter() - t0) * 1000.0
            if not embedded.vectors:
                raise EmbeddingUnavailable("Empty embedding result")
            query_vec = embedded.vectors[0]
            t1 = time.perf_counter()
            hits = await self._store.search(
                query_vec,
                top_k=self._config.top_k,
                min_similarity=self._config.min_similarity,
            )
            search_ms = (time.perf_counter() - t1) * 1000.0
        except (
            EmbeddingTimeout,
            EmbeddingUnavailable,
            EmbeddingDimensionError,
            VectorStoreError,
        ):
            fallback = "retrieval_failed"
            hits = []
        except Exception:  # noqa: BLE001
            fallback = "retrieval_failed"
            hits = []

        if self._observability is not None:
            try:
                self._observability.collector.note_answer_stage("rag_end")
            except Exception:  # noqa: BLE001
                pass

        labeled: List[tuple[str, str]] = []
        sources_meta: List[dict] = []
        source_ids: List[str] = []
        for i, hit in enumerate(hits, start=1):
            label = source_label(i)
            heading = format_source_heading(
                label, hit.document_name, hit.page_start, hit.chunk_index
            )
            labeled.append((heading, hit.text))
            source_ids.append(hit.document_id)
            sources_meta.append(
                {
                    "label": label,
                    "document_name": hit.document_name,
                    "page": hit.page_start,
                    "chunk_index": hit.chunk_index,
                }
            )

        if labeled:
            rag_msg = build_rag_system_message(labeled)
            await self.push_frame(
                LLMMessagesAppendFrame(messages=[rag_msg], run_llm=False),
                direction,
            )
            status = "ok"
        else:
            status = "no_match"
            if fallback is None:
                fallback = "no_match"

        self._record(
            query_id=query_id,
            attempted=True,
            store_empty=False,
            match_count=len(hits),
            source_ids=tuple(source_ids),
            embed_ms=embed_ms,
            search_ms=search_ms,
            fallback=fallback,
        )
        if self._observability is not None:
            try:
                self._observability.record_rag_event(
                    attempted=True,
                    hit_count=len(hits),
                    source_ids=tuple(source_ids),
                    embedding_latency_ms=embed_ms,
                    retrieval_latency_ms=search_ms,
                    fallback_reason=fallback,
                )
            except Exception:  # noqa: BLE001
                pass
        await self._emit_sources(query_id, sources_meta, status=status)
        await self.push_frame(frame, direction)

    def _record(
        self,
        *,
        query_id: str,
        attempted: bool,
        store_empty: bool,
        match_count: int,
        source_ids: Sequence[str],
        embed_ms: Optional[float],
        search_ms: Optional[float],
        fallback: Optional[str],
    ) -> None:
        self._session.events.append(
            RetrievalEvent(
                timestamp=time.time(),
                query_id=query_id,
                attempted=attempted,
                store_empty=store_empty,
                match_count=match_count,
                source_ids=tuple(source_ids),
                embedding_latency_ms=embed_ms,
                retrieval_latency_ms=search_ms,
                fallback_reason=fallback,
            )
        )

    async def _emit_sources(
        self, query_id: str, sources: List[dict], *, status: str
    ) -> None:
        if self._send is None:
            return
        payload = {
            "type": "knowledge.retrieval",
            "version": 1,
            "query_id": query_id,
            "status": status,
            "sources": [
                {
                    "label": s["label"],
                    "document_name": s["document_name"],
                    "page": s.get("page"),
                }
                for s in sources
            ],
        }
        await self._send(wrap_rtvi_server_message(payload))
