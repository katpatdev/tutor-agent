"""Read-only conversation mirror for UI (never drives lesson or LLMContext)."""

from __future__ import annotations

import itertools
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional


class ConversationRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class TutorSource(str, Enum):
    NARRATION = "narration"
    ANSWER = "answer"
    TRANSITION = "transition"
    RESUME = "resume"
    CHECKPOINT = "checkpoint"
    QA = "qa"
    CLARIFICATION = "clarification"
    REPEAT = "repeat"
    OTHER = "other"


class PlaybackStatus(str, Enum):
    QUEUED = "queued"
    SPEAKING = "speaking"
    SPOKEN = "spoken"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


DEFAULT_MAX_ENTRIES = 1000

PublishFn = Callable[[Dict[str, Any]], Awaitable[None]]


@dataclass
class ConversationEntry:
    entry_id: str
    sequence: int
    role: str
    text: str
    timestamp: float
    slide_number: Optional[int] = None
    slide_title: Optional[str] = None
    mode: Optional[str] = None
    source: Optional[str] = None
    tts_unit_id: Optional[str] = None
    playback_status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ConversationLedger:
    """Observer-only session history. Failures must not affect the lesson."""

    max_entries: int = DEFAULT_MAX_ENTRIES
    publish: Optional[PublishFn] = None
    _entries: List[ConversationEntry] = field(default_factory=list)
    _by_id: Dict[str, ConversationEntry] = field(default_factory=dict)
    _by_tts: Dict[str, str] = field(default_factory=dict)
    _seq: itertools.count = field(default_factory=lambda: itertools.count(1))
    entries_created: int = 0
    entries_updated: int = 0
    duplicates_ignored: int = 0
    trimmed: int = 0
    publish_failures: int = 0

    def snapshot(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._entries]

    def _trim(self) -> None:
        while len(self._entries) > self.max_entries:
            old = self._entries.pop(0)
            self._by_id.pop(old.entry_id, None)
            if old.tts_unit_id:
                self._by_tts.pop(old.tts_unit_id, None)
            self.trimmed += 1

    async def _emit(self, kind: str, entry: ConversationEntry) -> None:
        if self.publish is None:
            return
        try:
            await self.publish(
                {
                    "type": "conversation.entry",
                    "version": 1,
                    "kind": kind,
                    "entry": entry.to_dict(),
                }
            )
        except Exception:  # noqa: BLE001
            self.publish_failures += 1

    async def add_user(
        self,
        text: str,
        *,
        slide_number: Optional[int] = None,
        slide_title: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> Optional[ConversationEntry]:
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        try:
            entry = ConversationEntry(
                entry_id=str(uuid.uuid4()),
                sequence=next(self._seq),
                role=ConversationRole.USER.value,
                text=cleaned,
                timestamp=time.time(),
                slide_number=slide_number,
                slide_title=slide_title,
                mode=mode,
            )
            self._entries.append(entry)
            self._by_id[entry.entry_id] = entry
            self.entries_created += 1
            self._trim()
            await self._emit("created", entry)
            return entry
        except Exception:  # noqa: BLE001
            self.publish_failures += 1
            return None

    async def add_or_update_tutor(
        self,
        text: str,
        *,
        source: TutorSource,
        tts_unit_id: Optional[str] = None,
        slide_number: Optional[int] = None,
        slide_title: Optional[str] = None,
        mode: Optional[str] = None,
        playback_status: PlaybackStatus = PlaybackStatus.QUEUED,
    ) -> Optional[ConversationEntry]:
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        try:
            if tts_unit_id and tts_unit_id in self._by_tts:
                existing = self._by_id.get(self._by_tts[tts_unit_id])
                if existing is not None:
                    self.duplicates_ignored += 1
                    existing.text = cleaned
                    existing.playback_status = playback_status.value
                    existing.source = source.value
                    self.entries_updated += 1
                    await self._emit("updated", existing)
                    return existing

            entry = ConversationEntry(
                entry_id=str(uuid.uuid4()),
                sequence=next(self._seq),
                role=ConversationRole.ASSISTANT.value,
                text=cleaned,
                timestamp=time.time(),
                slide_number=slide_number,
                slide_title=slide_title,
                mode=mode,
                source=source.value,
                tts_unit_id=tts_unit_id,
                playback_status=playback_status.value,
            )
            self._entries.append(entry)
            self._by_id[entry.entry_id] = entry
            if tts_unit_id:
                self._by_tts[tts_unit_id] = entry.entry_id
            self.entries_created += 1
            self._trim()
            await self._emit("created", entry)
            return entry
        except Exception:  # noqa: BLE001
            self.publish_failures += 1
            return None

    async def update_playback(
        self,
        *,
        tts_unit_id: Optional[str] = None,
        status: PlaybackStatus,
    ) -> Optional[ConversationEntry]:
        try:
            if not tts_unit_id:
                return None
            eid = self._by_tts.get(tts_unit_id)
            if not eid:
                return None
            entry = self._by_id.get(eid)
            if entry is None:
                return None
            if (
                entry.playback_status == PlaybackStatus.FAILED.value
                and status is PlaybackStatus.SPOKEN
            ):
                return entry
            entry.playback_status = status.value
            self.entries_updated += 1
            await self._emit("updated", entry)
            return entry
        except Exception:  # noqa: BLE001
            self.publish_failures += 1
            return None
