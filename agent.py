"""
Copyright (c) 2025, Daily

SPDX-License-Identifier: BSD 2-Clause License
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputTransportMessageFrame,
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

from curriculum import slide_prompts
from embedding_service import OpenAIEmbeddingClient, load_rag_config
from knowledge_store import SHARED_KNOWLEDGE_STORE
from lesson_protocol import LessonProtocolSession
from moderation_service import OpenAIModerationClient, load_safety_config
from presentation_runtime import (
    BASE_TUTOR_PROMPT,
    TTS_INSTRUCTIONS,
    PresentationRuntime,
)
from retrieval_processor import RetrievalProcessor, SessionRetrievalState
from safety_processors import InputSafetyProcessor, OutputSafetyProcessor

load_dotenv(override=True)

# Validate non-secret configuration at import/startup.
SAFETY_CONFIG = load_safety_config()
RAG_CONFIG = load_rag_config()


class LessonLifecycleObserver(BaseObserver):
    """Forwards speaking lifecycle and inbound transport messages to the session."""

    def __init__(self, runtime: PresentationRuntime, protocol: LessonProtocolSession):
        super().__init__()
        self._runtime = runtime
        self._protocol = protocol

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
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
    )

    llm = OpenAILLMService(
        api_key=api_key,
        model="gpt-4o",
    )

    context = LLMContext(messages)

    vad_params = VADParams(
        confidence=0.85,
        start_secs=0.45,
        stop_secs=0.35,
        min_volume=0.7,
    )
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=vad_params),
        ),
    )

    runtime_holder: dict = {}

    class _TaskFrameSink:
        async def queue_frames(self, frames):
            task = runtime_holder["task"]
            await task.queue_frames(frames)

    runtime = PresentationRuntime(
        slide_prompts=slide_prompts(),
        frame_sink=_TaskFrameSink(),
    )

    moderation_client = OpenAIModerationClient(
        api_key=api_key,
        model=SAFETY_CONFIG.moderation_model,
        timeout_seconds=SAFETY_CONFIG.timeout_seconds,
    )
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
    )
    output_safety = OutputSafetyProcessor(
        runtime=runtime,
        moderation_client=moderation_client,
        config=SAFETY_CONFIG,
    )

    retrieval_session = SessionRetrievalState()

    async def send_outbound(message: dict) -> None:
        task = runtime_holder["task"]
        await task.queue_frames([OutputTransportMessageUrgentFrame(message=message)])

    async def send_retrieval(message: dict) -> None:
        await send_outbound(message)

    retrieval = RetrievalProcessor(
        runtime=runtime,
        store=SHARED_KNOWLEDGE_STORE,
        embedding_client=embedding_client,
        config=RAG_CONFIG,
        send_retrieval_message=send_retrieval,
        session_state=retrieval_session,
    )

    pipeline = Pipeline(
        [
            ws_transport.input(),
            stt,
            input_safety,
            retrieval,
            context_aggregator.user(),
            llm,
            output_safety,
            tts,
            ws_transport.output(),
            context_aggregator.assistant(),
        ]
    )

    protocol = LessonProtocolSession(runtime, send_outbound)
    runtime.set_on_state_changed(protocol.publish_state)

    observer = LessonLifecycleObserver(runtime, protocol)

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

    @ws_transport.event_handler("on_client_connected")
    async def on_client_connected(transport, websocket):
        logger.info("[transport] client connected")
        await runtime.start_session()

    @ws_transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, websocket):
        logger.info("[transport] client disconnected")
        protocol.mark_closed()
        await runtime.end_session()
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
