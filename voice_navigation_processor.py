"""Classroom-control frame processor (post input-safety, pre-RAG).

Intercepts deterministic navigation, acknowledgement, continuation, repeat,
Q&A-completion, return-origin, and clarification utterances so they never
reach embeddings or the LLM with a false navigation promise.
"""

from __future__ import annotations

from typing import Any

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from classroom_control import ClassroomControlKind, parse_classroom_control


class VoiceNavigationProcessor(FrameProcessor):
    """Intercept clear classroom-control commands; do not forward them to RAG/LLM."""

    def __init__(self, runtime: Any):
        super().__init__()
        self._runtime = runtime

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if not text:
                return
            slide_count = getattr(self._runtime.state, "slide_count", None)
            intent = parse_classroom_control(text, slide_count=slide_count)
            if intent is None:
                await self.push_frame(frame, direction)
                return
            handled = await self._runtime.handle_classroom_control(intent)
            if not handled:
                # Only fall through for non-clarify failures; never let a
                # suspected-control utterance invent a slide move via the LLM.
                if intent.kind is ClassroomControlKind.CLARIFY:
                    return
                await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
