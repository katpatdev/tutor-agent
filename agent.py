"""
Copyright (c) 2025, Daily

SPDX-License-Identifier: BSD 2-Clause License
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    InputTransportMessageFrame,
    MetricsFrame,
    OutputTransportMessageUrgentFrame,
    UserStartedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAIRealtimeSTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from curriculum import slide_prompts, TOTAL_SLIDES
from voice_navigation_processor import VoiceNavigationProcessor
from embedding_service import OpenAIEmbeddingClient, load_rag_config
from knowledge_store import SHARED_KNOWLEDGE_STORE
from lesson_protocol import LessonProtocolSession
from moderation_service import OpenAIModerationClient, load_safety_config
from narration_prefetch import NarrationPrefetchCache
from presentation_runtime import (
    ACTIVE_TUTOR_PROMPT_HASH,
    ACTIVE_TUTOR_PROMPT_VERSION,
    BASE_TUTOR_PROMPT,
    TTS_INSTRUCTIONS,
    PresentationRuntime,
)
from safety_policy import SafetyDecision, SafetySource, evaluate_moderation
from narration_plan import load_narration_max_characters
from retrieval_processor import RetrievalProcessor, SessionRetrievalState
from safety_processors import InputSafetyProcessor, OutputSafetyProcessor
from session_config import load_session_data_config
from session_observability import SessionObservability
from session_store import CURRICULUM_VERSION, SessionStore, SessionStoreError
from voice_runtime_config import (
    load_lesson_followup_wait_seconds,
    load_no_answer_timeout_seconds,
    load_qa_silence_timeout_seconds,
    load_tts_speech_speed,
    load_vad_runtime_config,
)

load_dotenv(override=True)

# Validate non-secret configuration at import/startup.
SAFETY_CONFIG = load_safety_config()
RAG_CONFIG = load_rag_config()
SESSION_DATA_CONFIG = load_session_data_config()
NARRATION_MAX_CHARACTERS = load_narration_max_characters()
NO_ANSWER_TIMEOUT_SECONDS = load_no_answer_timeout_seconds()
QA_SILENCE_TIMEOUT_SECONDS = load_qa_silence_timeout_seconds()
LESSON_FOLLOWUP_WAIT_SECONDS = load_lesson_followup_wait_seconds()
TTS_SPEECH_SPEED = load_tts_speech_speed()
VAD_RUNTIME = load_vad_runtime_config()

try:
    SHARED_SESSION_STORE: SessionStore | None = SessionStore(SESSION_DATA_CONFIG)
    try:
        deleted = SHARED_SESSION_STORE.apply_retention()
        if deleted:
            logger.info(f"Session retention deleted {deleted} expired session(s)")
    except Exception:  # noqa: BLE001
        logger.warning("Session retention cleanup failed")
except SessionStoreError:
    SHARED_SESSION_STORE = None
    logger.warning("Session store unavailable at startup")
except Exception:  # noqa: BLE001
    SHARED_SESSION_STORE = None
    logger.warning("Session store initialization failed")


class LessonLifecycleObserver(BaseObserver):
    """Forwards speaking lifecycle, metrics, and inbound transport messages.

    Pipecat notifies observers on every pipeline hop, so the same physical
    frame can appear many times. Handlers run once per frame identity.
    """

    def __init__(
        self,
        runtime: PresentationRuntime,
        protocol: LessonProtocolSession,
        observability: SessionObservability,
        *,
        tts_processor=None,
    ):
        super().__init__()
        self._runtime = runtime
        self._protocol = protocol
        self._observability = observability
        self._tts_processor = tts_processor
        self._seen_frame_ids: set[int] = set()
        self._seen_frame_order: list[int] = []
        self._seen_frame_limit = 4096

    def _mark_seen(self, frame_id: int) -> bool:
        """Return True if this frame id is newly seen (should be handled)."""
        if frame_id in self._seen_frame_ids:
            return False
        self._seen_frame_ids.add(frame_id)
        self._seen_frame_order.append(frame_id)
        if len(self._seen_frame_order) > self._seen_frame_limit:
            old = self._seen_frame_order.pop(0)
            self._seen_frame_ids.discard(old)
        return True

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if isinstance(frame, MetricsFrame):
            self._observability.collector.ingest_metrics_frame(frame)
            return
        if isinstance(frame, ErrorFrame):
            if not self._mark_seen(id(frame)):
                return
            await self._runtime.on_tts_error_frame(
                frame, tts_processor=self._tts_processor
            )
            return
        if not isinstance(
            frame,
            (
                BotStartedSpeakingFrame,
                BotStoppedSpeakingFrame,
                UserStartedSpeakingFrame,
                InputTransportMessageFrame,
            ),
        ):
            return
        if not self._mark_seen(id(frame)):
            return
        if isinstance(frame, BotStartedSpeakingFrame):
            await self._runtime.on_bot_started_speaking()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self._runtime.on_bot_stopped_speaking()
        elif isinstance(frame, UserStartedSpeakingFrame):
            await self._runtime.on_user_started_speaking()
        elif isinstance(frame, InputTransportMessageFrame):
            await self._protocol.handle_transport_message(frame.message)


async def run_bot(websocket_client):
    ws_transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=ProtobufFrameSerializer(),
        ),
    )

    messages = [{"role": "system", "content": BASE_TUTOR_PROMPT}]
    api_key = os.getenv("OPENAI_API_KEY")

    stt = OpenAIRealtimeSTTService(
        api_key=api_key,
        model="gpt-4o-transcribe",
    )

    tts = OpenAITTSService(
        api_key=api_key,
        model="gpt-4o-mini-tts",
        voice="alloy",
        instructions=TTS_INSTRUCTIONS,
        speed=TTS_SPEECH_SPEED,
    )

    llm = OpenAILLMService(
        api_key=api_key,
        model="gpt-4o",
    )

    context = LLMContext(messages)

    vad_params = VADParams(
        confidence=VAD_RUNTIME.confidence,
        start_secs=VAD_RUNTIME.start_secs,
        stop_secs=VAD_RUNTIME.stop_secs,
        min_volume=VAD_RUNTIME.min_volume,
    )
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=vad_params),
        ),
    )

    runtime_holder: dict = {}
    observability = SessionObservability(
        config=SESSION_DATA_CONFIG,
        store=SHARED_SESSION_STORE,
        tutor_prompt_version=ACTIVE_TUTOR_PROMPT_VERSION,
        tutor_prompt_hash=ACTIVE_TUTOR_PROMPT_HASH,
        curriculum_version=CURRICULUM_VERSION,
    )
    if SHARED_SESSION_STORE is None:
        observability.store_unavailable = True

    class _TaskFrameSink:
        async def queue_frames(self, frames):
            task = runtime_holder["task"]
            await task.queue_frames(frames)

    runtime = PresentationRuntime(
        slide_prompts=slide_prompts(),
        frame_sink=_TaskFrameSink(),
        narration_max_characters=NARRATION_MAX_CHARACTERS,
        no_answer_timeout_seconds=NO_ANSWER_TIMEOUT_SECONDS,
        qa_silence_timeout_seconds=QA_SILENCE_TIMEOUT_SECONDS,
        lesson_followup_wait_seconds=LESSON_FOLLOWUP_WAIT_SECONDS,
    )
    runtime.set_observability(observability)

    moderation_client = OpenAIModerationClient(
        api_key=api_key,
        model=SAFETY_CONFIG.moderation_model,
        timeout_seconds=SAFETY_CONFIG.timeout_seconds,
    )

    from openai import AsyncOpenAI

    prefetch_client = AsyncOpenAI(api_key=api_key)

    async def _prefetch_generate(slide_index: int, instruction: str) -> str:
        # Separate bounded context — never the live conversational LLMContext.
        messages = [
            {"role": "system", "content": BASE_TUTOR_PROMPT},
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": (
                    f"Narrate slide {slide_index + 1} now using only the "
                    "curriculum instruction above."
                ),
            },
        ]
        resp = await prefetch_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.4,
        )
        choice = resp.choices[0].message.content if resp.choices else ""
        return (choice or "").strip()

    async def _prefetch_moderate(text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        result = await moderation_client.moderate(cleaned)
        decision = evaluate_moderation(
            result, source=SafetySource.ASSISTANT_OUTPUT
        )
        if decision.decision is not SafetyDecision.ALLOW:
            return ""
        return cleaned

    def _prefetch_event(event: str, fields: dict) -> None:
        try:
            observability.collector.note_answer_stage(event)
        except Exception:  # noqa: BLE001
            pass

    prefetch_cache = NarrationPrefetchCache(
        slide_prompts=slide_prompts(),
        slide_count=TOTAL_SLIDES,
        tutor_system_prompt=BASE_TUTOR_PROMPT,
        generate_fn=_prefetch_generate,
        moderate_fn=_prefetch_moderate,
        max_characters=NARRATION_MAX_CHARACTERS,
        on_event=_prefetch_event,
        curriculum_version=CURRICULUM_VERSION,
        prompt_version=ACTIVE_TUTOR_PROMPT_VERSION,
    )
    runtime.set_prefetch_cache(prefetch_cache)
    embedding_client = OpenAIEmbeddingClient(
        api_key=api_key,
        model=RAG_CONFIG.embedding_model,
        timeout_seconds=RAG_CONFIG.embedding_timeout_seconds,
        batch_size=RAG_CONFIG.embedding_batch_size,
    )
    input_safety = InputSafetyProcessor(
        runtime=runtime,
        moderation_client=moderation_client,
        config=SAFETY_CONFIG,
        observability=observability,
    )
    output_safety = OutputSafetyProcessor(
        runtime=runtime,
        moderation_client=moderation_client,
        config=SAFETY_CONFIG,
        observability=observability,
    )

    retrieval_session = SessionRetrievalState()
    lesson_started = False
    start_lock = asyncio.Lock()

    async def send_outbound(message: dict) -> None:
        task = runtime_holder["task"]
        await task.queue_frames([OutputTransportMessageUrgentFrame(message=message)])

    async def send_retrieval(message: dict) -> None:
        await send_outbound(message)

    async def start_lesson_once() -> None:
        nonlocal lesson_started
        async with start_lock:
            if lesson_started:
                return
            lesson_started = True
            observability.mark_lesson_started()
            await runtime.prepare_and_start_session()

    async def configuration_timeout() -> None:
        try:
            await asyncio.sleep(SESSION_DATA_CONFIG.configuration_timeout_seconds)
            if not observability.configured:
                observability.mark_configured(transcript_consent=False)
            await start_lesson_once()
        except asyncio.CancelledError:
            return

    voice_nav = VoiceNavigationProcessor(runtime)

    retrieval = RetrievalProcessor(
        runtime=runtime,
        store=SHARED_KNOWLEDGE_STORE,
        embedding_client=embedding_client,
        config=RAG_CONFIG,
        send_retrieval_message=send_retrieval,
        session_state=retrieval_session,
        observability=observability,
    )

    pipeline = Pipeline(
        [
            ws_transport.input(),
            stt,
            input_safety,
            voice_nav,
            retrieval,
            context_aggregator.user(),
            llm,
            output_safety,
            tts,
            ws_transport.output(),
            context_aggregator.assistant(),
        ]
    )

    protocol = LessonProtocolSession(
        runtime,
        send_outbound,
        observability=observability,
        transcript_persistence_available=SESSION_DATA_CONFIG.transcript_persistence_enabled,
        on_configured_start=start_lesson_once,
    )
    runtime.set_on_state_changed(protocol.publish_state)

    observer = LessonLifecycleObserver(runtime, protocol, observability, tts_processor=tts)

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[observer],
        enable_turn_tracking=False,
    )
    runtime_holder["task"] = task
    timeout_task: asyncio.Task | None = None

    @ws_transport.event_handler("on_client_connected")
    async def on_client_connected(transport, websocket):
        nonlocal timeout_task
        logger.info("[transport] client connected")
        await protocol.publish_session_ready()
        timeout_task = asyncio.create_task(configuration_timeout())

    @ws_transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, websocket):
        logger.info("[transport] client disconnected")
        protocol.mark_closed()
        if timeout_task is not None:
            timeout_task.cancel()
        await observability.finalize(
            disconnect_reason="client_disconnected",
            lesson_mode=runtime.state.mode.name,
            slide_index=runtime.state.cursor.slide_index,
        )
        await runtime.end_session()
        await runtime.end_session_cleanup_tts()
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
