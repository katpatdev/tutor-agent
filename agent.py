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
from lesson_protocol import LessonProtocolSession
from presentation_runtime import (
    BASE_TUTOR_PROMPT,
    TTS_INSTRUCTIONS,
    PresentationRuntime,
)

load_dotenv(override=True)


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

    stt = OpenAIRealtimeSTTService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-transcribe",
    )

    tts = OpenAITTSService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini-tts",
        voice="alloy",
        instructions=TTS_INSTRUCTIONS,
    )

    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
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

    pipeline = Pipeline(
        [
            ws_transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            ws_transport.output(),
            context_aggregator.assistant(),
        ]
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

    async def send_outbound(message: dict) -> None:
        task = runtime_holder["task"]
        await task.queue_frames([OutputTransportMessageUrgentFrame(message=message)])

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
