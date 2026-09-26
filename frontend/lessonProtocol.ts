/**
 * Lesson control protocol (v1) — pure TypeScript, no DOM.
 * Never include API keys or server secrets in these types or helpers.
 */

export const PROTOCOL_VERSION = 1 as const;

export type LessonCommandName = 'pause' | 'resume' | 'goto_slide' | 'get_state';

export type LessonCommandMessage = {
  type: 'lesson.command';
  version: 1;
  request_id: string;
  command: LessonCommandName;
  payload: Record<string, unknown>;
};

export type NarrationProgress = {
  current_segment: number;
  total_segments: number;
  resume_accuracy: 'segment' | 'exact_playback';
  error?: string;
};

export type LessonStateMessage = {
  type: 'lesson.state';
  version: 1;
  sequence: number;
  mode: string;
  slide: {
    index: number;
    number: number;
    total: number;
    title: string;
  };
  paused: boolean;
  can_pause: boolean;
  can_resume: boolean;
  can_navigate: boolean;
  safety_status: 'normal' | 'redirecting' | 'hold';
  safety_notice: string | null;
  audio_warning?: string | null;
  narration?: NarrationProgress | null;
};

export type LessonCommandResultMessage = {
  type: 'lesson.command_result';
  version: 1;
  request_id: string;
  ok: boolean;
  error?: {
    code: string;
    message: string;
  };
};

export type LessonServerMessage =
  | LessonStateMessage
  | LessonCommandResultMessage;

export type ConversationEntry = {
  entry_id: string;
  sequence: number;
  role: 'user' | 'assistant';
  text: string;
  timestamp: number;
  slide_number?: number;
  slide_title?: string;
  mode?: string;
  source?: string;
  playback_status?: string;
};

export type ConversationEntryMessage = {
  type: 'conversation.entry';
  version: 1;
  kind: 'created' | 'updated';
  entry: ConversationEntry;
};

export type ConversationSnapshotMessage = {
  type: 'conversation.snapshot';
  version: 1;
  entries: ConversationEntry[];
};

export type ConversationServerMessage =
  | ConversationEntryMessage
  | ConversationSnapshotMessage;

const MODE_LABELS: Record<string, string> = {
  IDLE: 'Idle',
  PRESENTING: 'Presenting',
  INTERRUPTED: 'Interrupted',
  ANSWERING: 'Answering',
  PAUSED: 'Paused',
  QA_MODE: 'Q&A',
  FINISHED: 'Finished',
};

export function humanModeLabel(mode: string): string {
  return MODE_LABELS[mode] ?? mode;
}

export function createRequestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function createPauseCommand(requestId: string = createRequestId()): LessonCommandMessage {
  return {
    type: 'lesson.command',
    version: 1,
    request_id: requestId,
    command: 'pause',
    payload: {},
  };
}

export function createResumeCommand(requestId: string = createRequestId()): LessonCommandMessage {
  return {
    type: 'lesson.command',
    version: 1,
    request_id: requestId,
    command: 'resume',
    payload: {},
  };
}

/** `displaySlideNumber` is 1-based UI value; payload uses zero-based index. */
export function createGotoCommand(
  displaySlideNumber: number,
  requestId: string = createRequestId()
): LessonCommandMessage {
  return {
    type: 'lesson.command',
    version: 1,
    request_id: requestId,
    command: 'goto_slide',
    payload: { slide_index: displaySlideNumber - 1 },
  };
}

export function createGetStateCommand(
  requestId: string = createRequestId()
): LessonCommandMessage {
  return {
    type: 'lesson.command',
    version: 1,
    request_id: requestId,
    command: 'get_state',
    payload: {},
  };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function parseServerMessage(raw: unknown): LessonServerMessage | null {
  if (!isObject(raw)) return null;
  const type = raw.type;
  const version = raw.version;
  if (version !== 1) return null;

  if (type === 'lesson.state') {
    const slide = raw.slide;
    if (!isObject(slide)) return null;
    if (typeof raw.sequence !== 'number') return null;
    if (typeof raw.mode !== 'string') return null;
    if (typeof slide.index !== 'number') return null;
    if (typeof slide.number !== 'number') return null;
    if (typeof slide.total !== 'number') return null;
    if (typeof slide.title !== 'string') return null;
    if (typeof raw.paused !== 'boolean') return null;
    if (typeof raw.can_pause !== 'boolean') return null;
    if (typeof raw.can_resume !== 'boolean') return null;
    if (typeof raw.can_navigate !== 'boolean') return null;
    if (
      raw.safety_status !== 'normal' &&
      raw.safety_status !== 'redirecting' &&
      raw.safety_status !== 'hold'
    ) {
      return null;
    }
    if (!(raw.safety_notice === null || typeof raw.safety_notice === 'string')) {
      return null;
    }
    if (
      raw.audio_warning !== undefined &&
      raw.audio_warning !== null &&
      typeof raw.audio_warning !== 'string'
    ) {
      return null;
    }
    if (raw.narration !== undefined && raw.narration !== null) {
      if (!isObject(raw.narration)) return null;
      if (typeof raw.narration.current_segment !== 'number') return null;
      if (typeof raw.narration.total_segments !== 'number') return null;
      if (
        raw.narration.resume_accuracy !== 'segment' &&
        raw.narration.resume_accuracy !== 'exact_playback'
      ) {
        return null;
      }
      // Reject narration text leaks in state metadata
      if ('text' in raw.narration || 'segment_text' in raw.narration) return null;
      if (raw.narration.error !== undefined && typeof raw.narration.error !== 'string') {
        return null;
      }
    }
    return raw as LessonStateMessage;
  }

  if (type === 'lesson.command_result') {
    if (typeof raw.request_id !== 'string') return null;
    if (typeof raw.ok !== 'boolean') return null;
    if (raw.ok === false) {
      if (!isObject(raw.error)) return null;
      if (typeof raw.error.code !== 'string') return null;
      if (typeof raw.error.message !== 'string') return null;
    }
    return raw as LessonCommandResultMessage;
  }

  return null;
}

function parseConversationEntry(raw: unknown): ConversationEntry | null {
  if (!isObject(raw)) return null;
  if (typeof raw.entry_id !== 'string') return null;
  if (typeof raw.sequence !== 'number') return null;
  if (raw.role !== 'user' && raw.role !== 'assistant') return null;
  if (typeof raw.text !== 'string') return null;
  if (typeof raw.timestamp !== 'number') return null;
  const entry: ConversationEntry = {
    entry_id: raw.entry_id,
    sequence: raw.sequence,
    role: raw.role,
    text: raw.text,
    timestamp: raw.timestamp,
  };
  if (typeof raw.slide_number === 'number') entry.slide_number = raw.slide_number;
  if (typeof raw.slide_title === 'string') entry.slide_title = raw.slide_title;
  if (typeof raw.mode === 'string') entry.mode = raw.mode;
  if (typeof raw.source === 'string') entry.source = raw.source;
  if (typeof raw.playback_status === 'string') {
    entry.playback_status = raw.playback_status;
  }
  return entry;
}

export function parseConversationMessage(
  raw: unknown
): ConversationServerMessage | null {
  if (!isObject(raw)) return null;
  if (raw.version !== 1) return null;
  if (raw.type === 'conversation.snapshot') {
    if (!Array.isArray(raw.entries)) return null;
    const entries: ConversationEntry[] = [];
    for (const item of raw.entries) {
      const parsed = parseConversationEntry(item);
      if (!parsed) return null;
      entries.push(parsed);
    }
    return { type: 'conversation.snapshot', version: 1, entries };
  }
  if (raw.type === 'conversation.entry') {
    if (raw.kind !== 'created' && raw.kind !== 'updated') return null;
    const entry = parseConversationEntry(raw.entry);
    if (!entry) return null;
    return {
      type: 'conversation.entry',
      version: 1,
      kind: raw.kind,
      entry,
    };
  }
  return null;
}

export class ConversationTracker {
  private entries: ConversationEntry[] = [];
  private byId = new Map<string, ConversationEntry>();
  private latestSequence = 0;

  get list(): ConversationEntry[] {
    return this.entries.slice();
  }

  reset(): void {
    this.entries = [];
    this.byId.clear();
    this.latestSequence = 0;
  }

  applySnapshot(entries: ConversationEntry[]): void {
    this.reset();
    for (const entry of entries) {
      this.upsert(entry, { allowStale: true });
    }
  }

  applyEvent(
    kind: 'created' | 'updated',
    entry: ConversationEntry
  ): boolean {
    return this.upsert(entry, { allowStale: kind === 'updated' });
  }

  private upsert(
    entry: ConversationEntry,
    opts: { allowStale: boolean }
  ): boolean {
    const existing = this.byId.get(entry.entry_id);
    if (existing) {
      if (!opts.allowStale && entry.sequence < existing.sequence) {
        return false;
      }
      if (entry.sequence < existing.sequence) {
        return false;
      }
      Object.assign(existing, entry);
      this.latestSequence = Math.max(this.latestSequence, entry.sequence);
      return true;
    }
    if (this.byId.has(entry.entry_id)) {
      return false;
    }
    this.byId.set(entry.entry_id, entry);
    this.entries.push(entry);
    this.entries.sort((a, b) => a.sequence - b.sequence);
    this.latestSequence = Math.max(this.latestSequence, entry.sequence);
    return true;
  }
}

export class LessonStateTracker {
  private latestSequence = -1;
  private latestState: LessonStateMessage | null = null;

  get sequence(): number {
    return this.latestSequence;
  }

  get state(): LessonStateMessage | null {
    return this.latestState;
  }

  /**
   * Accept a state message only when its sequence is newer than the last applied.
   * Returns true when applied.
   */
  applyState(message: LessonStateMessage): boolean {
    if (message.sequence <= this.latestSequence) {
      return false;
    }
    this.latestSequence = message.sequence;
    this.latestState = message;
    return true;
  }

  reset(): void {
    this.latestSequence = -1;
    this.latestState = null;
  }
}

export function formatNarrationStatus(narration: NarrationProgress | null | undefined): string {
  if (!narration || narration.total_segments <= 0) {
    return '';
  }
  const accuracy =
    narration.resume_accuracy === 'exact_playback'
      ? 'exact playback'
      : 'segment-level';
  return `Narration segment ${narration.current_segment} of ${narration.total_segments} · Resume accuracy: ${accuracy}`;
}

/** Client protocol types must never declare secret fields. */
export const CLIENT_PROTOCOL_FORBIDDEN_KEYS = [
  'OPENAI_API_KEY',
  'api_key',
  'authorization',
  'password',
  'token',
  'secret',
] as const;
