"""Session-scoped moderated slide-narration prefetch (text + plan only).

Uses a separate OpenAI chat context. Never mutates the live conversational
LLMContext. Does not pre-generate TTS audio.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from tutor_agent.narration.narration_plan import (
    NarrationPlan,
    build_narration_plan,
    new_generation_id,
)
from tutor_agent.lesson.slide_narration_prompt import format_slide_narration_instruction

logger = logging.getLogger(__name__)


class PrefetchState(str, Enum):
    NOT_STARTED = "not_started"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


@dataclass
class PrefetchEntry:
    slide_index: int
    state: PrefetchState = PrefetchState.NOT_STARTED
    moderated_text: Optional[str] = None
    plan: Optional[NarrationPlan] = None
    error: Optional[str] = None
    generation_latency_ms: Optional[float] = None
    moderation_latency_ms: Optional[float] = None
    task: Optional[asyncio.Task] = None


ModerationFn = Callable[[str], Awaitable[str]]
"""Return approved text, or raise / return empty on rejection."""

GenerateFn = Callable[[int, str], Awaitable[str]]
"""(slide_index, instruction) -> raw narration text."""


@dataclass
class NarrationPrefetchCache:
    """Sequential background prefetch with single-flight per slide."""

    slide_prompts: Sequence[str]
    slide_count: int
    tutor_system_prompt: str
    generate_fn: GenerateFn
    moderate_fn: ModerationFn
    max_characters: int = 320
    min_characters: int = 1
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None
    _entries: Dict[int, PrefetchEntry] = field(default_factory=dict)
    _worker_task: Optional[asyncio.Task] = None
    _priority: Optional[int] = None
    _cancelled: bool = False
    _paused_for_interaction: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    curriculum_version: str = ""
    prompt_version: str = ""

    def __post_init__(self) -> None:
        for i in range(self.slide_count):
            self._entries[i] = PrefetchEntry(slide_index=i)

    def _emit(self, event: str, **fields: Any) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event, fields)
            except Exception:  # noqa: BLE001
                pass

    def entry(self, slide_index: int) -> PrefetchEntry:
        return self._entries[slide_index]

    def get_ready_plan(self, slide_index: int) -> Optional[NarrationPlan]:
        e = self._entries.get(slide_index)
        if e is None or e.state is not PrefetchState.READY or e.plan is None:
            return None
        # Return a fresh plan copy identity for this presentation generation.
        try:
            return build_narration_plan(
                slide_index=slide_index,
                text=e.moderated_text or "",
                max_characters=self.max_characters,
                min_characters=self.min_characters,
                generation_id=new_generation_id(),
            )
        except (TypeError, ValueError):
            return None

    def get_ready_text(self, slide_index: int) -> Optional[str]:
        e = self._entries.get(slide_index)
        if e is None or e.state is not PrefetchState.READY:
            return None
        return e.moderated_text

    def mark_interaction_priority(self, active: bool) -> None:
        self._paused_for_interaction = active

    def prioritize(self, slide_index: int) -> None:
        if 0 <= slide_index < self.slide_count:
            self._priority = slide_index
            self._emit("prefetch_target_prioritized", slide=slide_index + 1)

    async def ensure_slide(self, slide_index: int) -> Optional[str]:
        """Await single-flight generation for one slide; return moderated text."""
        if slide_index < 0 or slide_index >= self.slide_count:
            return None
        async with self._lock:
            entry = self._entries[slide_index]
            if entry.state is PrefetchState.READY and entry.moderated_text:
                self._emit("prefetch_cache_hit", slide=slide_index + 1)
                return entry.moderated_text
            if entry.state is PrefetchState.GENERATING and entry.task is not None:
                self._emit(
                    "prefetch_duplicate_prevented",
                    slide=slide_index + 1,
                )
                task = entry.task
            else:
                self._emit("prefetch_cache_miss", slide=slide_index + 1)
                task = asyncio.create_task(self._generate_one(slide_index))
                entry.task = task
                entry.state = PrefetchState.GENERATING
        try:
            return await task
        except asyncio.CancelledError:
            return None
        except Exception:  # noqa: BLE001
            return None

    async def _generate_one(self, slide_index: int) -> Optional[str]:
        entry = self._entries[slide_index]
        if self._cancelled:
            entry.state = PrefetchState.FAILED
            entry.error = "cancelled"
            return None
        # Wait while student interaction has priority.
        while self._paused_for_interaction and not self._cancelled:
            await asyncio.sleep(0.05)
        if self._cancelled:
            entry.state = PrefetchState.FAILED
            entry.error = "cancelled"
            return None

        entry.state = PrefetchState.GENERATING
        self._emit("prefetch_started", slide=slide_index + 1)
        instruction = format_slide_narration_instruction(
            slide_index=slide_index,
            slide_count=self.slide_count,
            curriculum_prompt=self.slide_prompts[slide_index],
        )
        t0 = time.perf_counter()
        try:
            raw = await self.generate_fn(slide_index, instruction)
        except Exception as exc:  # noqa: BLE001
            entry.state = PrefetchState.FAILED
            entry.error = f"generate:{exc}"
            self._emit("prefetch_failed", slide=slide_index + 1, reason="generate")
            return None
        gen_ms = (time.perf_counter() - t0) * 1000.0
        entry.generation_latency_ms = gen_ms

        if self._cancelled:
            entry.state = PrefetchState.FAILED
            entry.error = "cancelled"
            return None

        t1 = time.perf_counter()
        try:
            approved = await self.moderate_fn(raw or "")
        except Exception as exc:  # noqa: BLE001
            entry.state = PrefetchState.FAILED
            entry.error = f"moderate:{exc}"
            self._emit("prefetch_failed", slide=slide_index + 1, reason="moderate")
            return None
        entry.moderation_latency_ms = (time.perf_counter() - t1) * 1000.0

        cleaned = (approved or "").strip()
        if not cleaned:
            entry.state = PrefetchState.FAILED
            entry.error = "empty_or_rejected"
            self._emit("prefetch_failed", slide=slide_index + 1, reason="empty")
            return None

        try:
            plan = build_narration_plan(
                slide_index=slide_index,
                text=cleaned,
                max_characters=self.max_characters,
                min_characters=self.min_characters,
                generation_id=new_generation_id(),
            )
        except (TypeError, ValueError):
            entry.state = PrefetchState.FAILED
            entry.error = "plan_invalid"
            self._emit("prefetch_failed", slide=slide_index + 1, reason="plan")
            return None

        if not plan.segments:
            entry.state = PrefetchState.FAILED
            entry.error = "empty_plan"
            self._emit("prefetch_failed", slide=slide_index + 1, reason="plan")
            return None

        entry.moderated_text = cleaned
        entry.plan = plan
        entry.state = PrefetchState.READY
        entry.task = None
        self._emit(
            "prefetch_ready",
            slide=slide_index + 1,
            gen_ms=round(gen_ms, 1),
            mod_ms=round(entry.moderation_latency_ms or 0.0, 1),
        )
        return cleaned

    async def store_live_approved(self, slide_index: int, text: str) -> None:
        """Cache narration produced by the live pipeline (already moderated)."""
        cleaned = (text or "").strip()
        if not cleaned or slide_index < 0 or slide_index >= self.slide_count:
            return
        try:
            plan = build_narration_plan(
                slide_index=slide_index,
                text=cleaned,
                max_characters=self.max_characters,
                min_characters=self.min_characters,
                generation_id=new_generation_id(),
            )
        except (TypeError, ValueError):
            return
        if not plan.segments:
            return
        entry = self._entries[slide_index]
        entry.moderated_text = cleaned
        entry.plan = plan
        entry.state = PrefetchState.READY
        entry.error = None
        self._emit("prefetch_ready", slide=slide_index + 1, source="live")

    def start_background(self, *, after_slide: int = 0) -> None:
        """Start sequential worker for slides after ``after_slide`` (0-based)."""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._cancelled = False
        self._worker_task = asyncio.create_task(self._worker(after_slide + 1))

    async def _worker(self, start_index: int) -> None:
        order: List[int] = list(range(start_index, self.slide_count))
        while not self._cancelled and order:
            if self._priority is not None and self._priority in order:
                idx = self._priority
                self._priority = None
            else:
                idx = order[0]
            order = [i for i in order if i != idx]
            entry = self._entries[idx]
            if entry.state is PrefetchState.READY:
                continue
            if entry.state is PrefetchState.GENERATING and entry.task is not None:
                try:
                    await entry.task
                except Exception:  # noqa: BLE001
                    pass
                continue
            await self.ensure_slide(idx)

    async def cancel(self) -> None:
        self._cancelled = True
        self._emit("prefetch_cancelled")
        tasks = []
        if self._worker_task is not None:
            tasks.append(self._worker_task)
            self._worker_task.cancel()
        for entry in self._entries.values():
            if entry.task is not None and not entry.task.done():
                entry.task.cancel()
                tasks.append(entry.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._worker_task = None
        for entry in self._entries.values():
            entry.task = None
            if entry.state is PrefetchState.GENERATING:
                entry.state = PrefetchState.FAILED
                entry.error = "cancelled"
