"""
Copyright (c) 2025, Daily

SPDX-License-Identifier: BSD 2-Clause License
"""

from __future__ import annotations

from typing import List
import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
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

from presentation_runtime import (
    BASE_TUTOR_PROMPT,
    TTS_INSTRUCTIONS,
    PresentationRuntime,
)

load_dotenv(override=True)


SLIDE_SYSTEM_MESSAGES: List[str] = [
    # Slide 1 – welcome & overview (index 0)
    (
        "SLIDE 1: WELCOME & OVERVIEW\n\n"
        "Welcome the audience and briefly introduce the topic: Natural Disasters. "
        "Explain that this presentation will walk through what natural disasters are, why they occur, "
        "and how they affect people and the environment. "
        "Mention that questions are welcome at any time and that you will continue guiding them through the slides."
    ),
    # Slide 2 – what are natural disasters (index 1)
    (
        "SLIDE 2: WHAT ARE NATURAL DISASTERS\n\n"
        "Explain that natural disasters are extreme natural events that cause major damage to life, property, "
        "or the environment. Examples include earthquakes, floods, hurricanes, volcanic eruptions, and droughts. "
        "Emphasize that these events are caused by natural processes of the Earth."
    ),
    # Slide 3 – why they happen (index 2)
    (
        "SLIDE 3: WHY NATURAL DISASTERS HAPPEN\n\n"
        "Describe the main reasons natural disasters occur: movement of tectonic plates, extreme weather patterns, "
        "volcanic activity, and climate-related changes. "
        "Briefly mention that some disasters are sudden while others develop slowly over time."
    ),
    # Slide 4 – major types (index 3)
    (
        "SLIDE 4: MAJOR TYPES OF NATURAL DISASTERS\n\n"
        "Introduce the most common categories such as earthquakes, floods, cyclones, wildfires, landslides, "
        "and volcanic eruptions. "
        "Explain that each type has different causes and impacts depending on geography and climate."
    ),
    # Slide 5 – impacts on people (index 4)
    (
        "SLIDE 5: IMPACT ON PEOPLE\n\n"
        "Explain how natural disasters affect communities: loss of life, injuries, destruction of homes, "
        "and displacement of families. "
        "Also mention disruption to healthcare, education, and daily life."
    ),
    # Slide 6 – environmental effects (index 5)
    (
        "SLIDE 6: ENVIRONMENTAL EFFECTS\n\n"
        "Describe how natural disasters affect ecosystems: deforestation from wildfires, flooding of habitats, "
        "soil erosion, and pollution of water sources. "
        "Mention that while disasters cause destruction, some also reshape landscapes and ecosystems."
    ),
    # Slide 7 – preparedness and safety (index 6)
    (
        "SLIDE 7: PREPAREDNESS AND SAFETY\n\n"
        "Explain how preparation can reduce damage and save lives. "
        "Discuss early warning systems, evacuation plans, emergency kits, and community awareness. "
        "Highlight that education and planning are key to disaster resilience."
    ),
    # Slide 8 – conclusion & discussion (index 7)
    (
        "SLIDE 8: CONCLUSION & DISCUSSION\n\n"
        "Summarize that natural disasters are powerful natural events that can have serious impacts on society "
        "and the environment. "
        "Emphasize the importance of preparedness, scientific understanding, and community cooperation. "
        "Invite the audience to ask questions or request clarification on any slide."
    ),
]


class LessonLifecycleObserver(BaseObserver):
    """Forwards verified speaking lifecycle frames to PresentationRuntime.

    Does not own lesson progression and does not use silence timers.
    """

    def __init__(self, runtime: PresentationRuntime):
        super().__init__()
        self._runtime = runtime

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if isinstance(frame, BotStartedSpeakingFrame):
            await self._runtime.on_bot_started_speaking()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self._runtime.on_bot_stopped_speaking()
        elif isinstance(frame, UserStartedSpeakingFrame):
            await self._runtime.on_user_started_speaking()


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

    # Placeholder task reference; runtime needs queue_frames from the real task.
    # We construct task after observer, then bind runtime to task as frame sink.
    runtime_holder: dict = {}

    class _TaskFrameSink:
        async def queue_frames(self, frames):
            task = runtime_holder["task"]
            await task.queue_frames(frames)

    runtime = PresentationRuntime(
        slide_prompts=SLIDE_SYSTEM_MESSAGES,
        frame_sink=_TaskFrameSink(),
    )
    observer = LessonLifecycleObserver(runtime)

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
        await runtime.end_session()
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
