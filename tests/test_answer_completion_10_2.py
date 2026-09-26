"""Iteration 10.2: answer-completion ownership, TTS packing, RAG empty-store."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List

from knowledge_ingestion import is_conversational_ack
from knowledge_store import InMemoryKnowledgeStore
from lesson_controller import LessonMode
from presentation_runtime import (
    OutputPurpose,
    PresentationRuntime,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_interruption_frame,
)
from speech_chunking import count_tts_units_for_text, pack_spoken_units
from voice_runtime_config import load_tts_speech_speed, load_vad_runtime_config


@dataclass
class FakeTTSSpeakFrame:
    text: str
    kind: str = "tts_speak"


def make_runtime(
    *,
    slide_count: int = 3,
    max_characters: int = 80,
) -> tuple[PresentationRuntime, RecordingFrameSink]:
    sink = RecordingFrameSink()
    runtime = PresentationRuntime(
        slide_prompts=[f"SLIDE {index}" for index in range(slide_count)],
        frame_sink=sink,
        interruption_frame_factory=make_test_interruption_frame,
        messages_append_frame_factory=make_test_append_frame,
        narration_max_characters=max_characters,
        no_answer_timeout_seconds=0.05,
    )
    runtime.set_tts_speak_frame_factory(FakeTTSSpeakFrame)
    return runtime, sink


def test_pack_short_three_sentence_answer_is_one_tts_unit() -> None:
    text = "Great! If anything else comes up, don't hesitate to ask. Let's move on!"
    units = pack_spoken_units(text)
    assert len(units) == 1
    assert "Great!" in units[0]
    assert "Let's move on!" in units[0]
    assert count_tts_units_for_text(text) == 1


def test_pack_preserves_all_sentence_text() -> None:
    text = "One. Two. Three. Four. Five. Six."
    units = pack_spoken_units(text, target_min_characters=20, target_max_characters=40)
    joined = " ".join(units)
    for word in ("One.", "Two.", "Three.", "Four.", "Five.", "Six."):
        assert word in joined
    assert len(units) >= 1


def test_pre_audio_interruption_answer_completion_is_accepted() -> None:
    """Narration LLM started, no BotStartedSpeaking, interrupt, answer completes."""

    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        # Simulate awaiting narration LLM (pre-audio).
        assert runtime._awaiting_narration_llm or runtime.output_purpose in {
            OutputPurpose.SLIDE_NARRATION,
            OutputPurpose.NONE,
        }
        # Force pre-audio presenting purpose without audible speech.
        runtime._output_purpose = OutputPurpose.SLIDE_NARRATION
        runtime._awaiting_narration_llm = True
        runtime._bot_speaking = False
        runtime._utterance_audible = False

        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        assert runtime.pre_audio_cancel_without_stop >= 1
        assert runtime._expected_suppressed_stops == 0

        # Answer plays and completes — must not be eaten by pre-audio cancel.
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION

    asyncio.run(_run())


def test_mid_audio_interruption_suppresses_only_cancelled_utterance() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("First section. Second section.")
        await runtime.on_bot_started_speaking()
        narr_id = runtime._speaking_utterance_id
        assert narr_id is not None

        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        assert runtime._expected_suppressed_stops == 1

        # Cancelled narration stop is ignored.
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        assert runtime.cancelled_completions_suppressed == 1
        assert runtime._expected_suppressed_stops == 0

        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert sink.tts_texts[-1] == "First section."
        assert runtime.segment_replays == 1

    asyncio.run(_run())


def test_second_interruption_during_answer_does_not_eat_final_completion() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("Only one segment.")
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()  # cancelled narration

        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        # Student barges in during answer audio.
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()  # cancelled first answer
        assert runtime.state.mode is LessonMode.ANSWERING

        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.PRESENTING

    asyncio.run(_run())


def test_silence_in_answering_does_not_set_stale_suppress() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("Hello there.")
        # Enter ANSWERING without audible speech (pre-audio style).
        runtime._output_purpose = OutputPurpose.SLIDE_NARRATION
        runtime._bot_speaking = False
        runtime._utterance_audible = False
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        # Speak again while still answering but silent — must not poison completion.
        await runtime.on_user_started_speaking()
        assert runtime._expected_suppressed_stops == 0
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.PRESENTING

    asyncio.run(_run())


def test_empty_knowledge_store_has_zero_chunks() -> None:
    store = InMemoryKnowledgeStore()
    assert store.chunk_count == 0
    assert store.document_count == 0


def test_conversational_acks_bypass_retrieval_eligibility() -> None:
    for utter in ("yes", "okay", "continue", "repeat that"):
        assert is_conversational_ack(utter)


def test_vad_and_tts_speed_unchanged_from_10_1_defaults() -> None:
    vad = load_vad_runtime_config()
    assert vad.stop_secs == 0.28
    assert vad.start_secs == 0.45
    assert load_tts_speech_speed() == 1.05
