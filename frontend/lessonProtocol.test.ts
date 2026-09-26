import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  CLIENT_PROTOCOL_FORBIDDEN_KEYS,
  ConversationTracker,
  createGotoCommand,
  createPauseCommand,
  createRequestId,
  createResumeCommand,
  formatNarrationStatus,
  LessonStateTracker,
  parseServerMessage,
  type NarrationProgress,
  type LessonStateMessage,
} from './lessonProtocol';

const sampleState = (sequence: number): LessonStateMessage => ({
  type: 'lesson.state',
  version: 1,
  sequence,
  mode: 'PRESENTING',
  slide: { index: 0, number: 1, total: 8, title: 'Welcome & Overview' },
  paused: false,
  can_pause: true,
  can_resume: false,
  can_navigate: true,
  safety_status: 'normal',
  safety_notice: null,
});

describe('lessonProtocol', () => {
  it('parses valid state messages', () => {
    const parsed = parseServerMessage(sampleState(1));
    expect(parsed?.type).toBe('lesson.state');
  });

  it('parses valid narration progress', () => {
    const parsed = parseServerMessage({
      ...sampleState(2),
      narration: {
        current_segment: 2,
        total_segments: 4,
        resume_accuracy: 'segment',
      },
    });
    expect(parsed?.type).toBe('lesson.state');
    if (parsed?.type === 'lesson.state') {
      expect(parsed.narration).toEqual({
        current_segment: 2,
        total_segments: 4,
        resume_accuracy: 'segment',
      });
    }
  });

  it('rejects malformed narration and narration text leaks', () => {
    const base = sampleState(3);
    expect(
      parseServerMessage({
        ...base,
        narration: { total_segments: 2, resume_accuracy: 'segment' },
      })
    ).toBeNull();
    expect(
      parseServerMessage({
        ...base,
        narration: { current_segment: 1, resume_accuracy: 'segment' },
      })
    ).toBeNull();
    expect(
      parseServerMessage({
        ...base,
        narration: {
          current_segment: 1,
          total_segments: 2,
          resume_accuracy: 'segment',
          text: 'private narration',
        },
      })
    ).toBeNull();
    expect(
      parseServerMessage({
        ...base,
        narration: {
          current_segment: 1,
          total_segments: 2,
          resume_accuracy: 'segment',
          segment_text: 'private narration',
        },
      })
    ).toBeNull();
  });

  it('rejects malformed messages', () => {
    expect(parseServerMessage(null)).toBeNull();
    expect(parseServerMessage({ type: 'lesson.state', version: 2 })).toBeNull();
    expect(parseServerMessage({ type: 'lesson.state', version: 1 })).toBeNull();
  });

  it('rejects older sequences and accepts newer ones', () => {
    const tracker = new LessonStateTracker();
    expect(tracker.applyState(sampleState(2))).toBe(true);
    expect(tracker.applyState(sampleState(1))).toBe(false);
    expect(tracker.applyState(sampleState(2))).toBe(false);
    expect(tracker.applyState(sampleState(3))).toBe(true);
    expect(tracker.sequence).toBe(3);
  });

  it('rejects stale narration state through the existing tracker', () => {
    const tracker = new LessonStateTracker();
    const current = {
      ...sampleState(10),
      narration: {
        current_segment: 3,
        total_segments: 4,
        resume_accuracy: 'segment' as const,
      },
    };
    const stale = {
      ...sampleState(9),
      narration: {
        current_segment: 1,
        total_segments: 4,
        resume_accuracy: 'segment' as const,
      },
    };
    expect(tracker.applyState(current)).toBe(true);
    expect(tracker.applyState(stale)).toBe(false);
    expect(tracker.state?.narration?.current_segment).toBe(3);
  });

  it('formats one-based segment progress without claiming exact resume', () => {
    const status = formatNarrationStatus({
      current_segment: 2,
      total_segments: 4,
      resume_accuracy: 'segment',
    });
    expect(status).toContain('Narration segment 2 of 4');
    expect(status).toContain('segment-level');
    expect(status.toLowerCase()).not.toContain('exact');
  });

  it('creates pause and resume commands', () => {
    const pause = createPauseCommand('p1');
    expect(pause).toEqual({
      type: 'lesson.command',
      version: 1,
      request_id: 'p1',
      command: 'pause',
      payload: {},
    });
    const resume = createResumeCommand('r1');
    expect(resume.command).toBe('resume');
  });

  it('exposes labeled Pause/Resume/Connect/Disconnect controls in the app shell', () => {
    const html = readFileSync(resolve(__dirname, 'index.html'), 'utf8');
    expect(html).toMatch(/id="connect-btn"[^>]*>\s*Connect\s*</);
    expect(html).toMatch(/id="disconnect-btn"[^>]*disabled[^>]*>\s*Disconnect\s*</);
    expect(html).toMatch(/id="pause-btn"[^>]*disabled[^>]*>\s*Pause\s*</);
    expect(html).toMatch(/id="resume-btn"[^>]*disabled[^>]*>\s*Resume\s*</);
    expect(html).toMatch(/id="goto-btn"[^>]*disabled[^>]*>\s*Go to Slide\s*</);
    expect(html).toContain('id="slide-select"');
    expect(html).toContain('id="transcript-consent"');
    expect(html).toContain('Transcript storage: OFF');
  });

  it('creates goto commands with zero-based index', () => {
    const goto = createGotoCommand(4, 'g1');
    expect(goto.command).toBe('goto_slide');
    expect(goto.payload).toEqual({ slide_index: 3 });
  });

  it('generates unique request ids', () => {
    const ids = new Set(Array.from({ length: 20 }, () => createRequestId()));
    expect(ids.size).toBe(20);
  });

  it('parses safe error messages', () => {
    const parsed = parseServerMessage({
      type: 'lesson.command_result',
      version: 1,
      request_id: 'x',
      ok: false,
      error: { code: 'INVALID_TRANSITION', message: 'Not paused.' },
    });
    expect(parsed?.type).toBe('lesson.command_result');
    if (parsed && parsed.type === 'lesson.command_result') {
      expect(parsed.ok).toBe(false);
      expect(parsed.error?.code).toBe('INVALID_TRANSITION');
    }
  });

  it('parses safety hold state and disables controls conceptually', () => {
    const hold = sampleState(4);
    hold.safety_status = 'hold';
    hold.safety_notice = 'Please tell a trusted adult.';
    hold.can_resume = false;
    hold.can_navigate = false;
    hold.can_pause = false;
    const parsed = parseServerMessage(hold);
    expect(parsed?.type).toBe('lesson.state');
    if (parsed && parsed.type === 'lesson.state') {
      expect(parsed.safety_status).toBe('hold');
      expect(parsed.can_resume).toBe(false);
      expect(parsed.can_navigate).toBe(false);
      expect(parsed.safety_notice).toContain('trusted adult');
    }
  });

  it('rejects malformed safety status', () => {
    expect(
      parseServerMessage({
        ...sampleState(1),
        safety_status: 'danger',
      })
    ).toBeNull();
  });

  it('keeps disconnect conceptually independent of hold flags', () => {
    const hold = sampleState(5);
    hold.safety_status = 'hold';
    hold.can_pause = false;
    hold.can_resume = false;
    hold.can_navigate = false;
    const parsed = parseServerMessage(hold);
    expect(parsed?.type).toBe('lesson.state');
    if (parsed && parsed.type === 'lesson.state') {
      expect(parsed.can_resume).toBe(false);
      expect(parsed.can_navigate).toBe(false);
      // Protocol has no can_disconnect; UI keeps Disconnect when connected.
      expect('can_disconnect' in parsed).toBe(false);
    }
  });

  it('ignores stale sequences after hold state', () => {
    const tracker = new LessonStateTracker();
    const hold = sampleState(7);
    hold.safety_status = 'hold';
    hold.safety_notice = 'Please tell a trusted adult.';
    expect(tracker.applyState(hold)).toBe(true);
    expect(tracker.applyState(sampleState(6))).toBe(false);
    expect(tracker.state?.safety_status).toBe('hold');
  });

  it('does not declare raw moderation fields in protocol exports', async () => {
    const mod = await import('./lessonProtocol');
    expect(Object.prototype.hasOwnProperty.call(mod, 'category_scores')).toBe(false);
    expect(Object.prototype.hasOwnProperty.call(mod, 'flagged_categories')).toBe(false);
    expect(CLIENT_PROTOCOL_FORBIDDEN_KEYS).toContain('OPENAI_API_KEY');
  });

  it('declares no narration text fields in client protocol types', () => {
    type HasText = 'text' extends keyof NarrationProgress ? true : false;
    type HasSegmentText = 'segment_text' extends keyof NarrationProgress ? true : false;
    const hasText: HasText = false;
    const hasSegmentText: HasSegmentText = false;
    expect(hasText).toBe(false);
    expect(hasSegmentText).toBe(false);
  });
});

describe('ConversationTracker idempotency', () => {
  it('does not duplicate when snapshot is followed by the same created event', () => {
    const tracker = new ConversationTracker();
    const entry = {
      entry_id: 'narr-1',
      sequence: 1,
      role: 'assistant' as const,
      text: 'First section.',
      timestamp: 100,
      source: 'narration',
    };
    tracker.applySnapshot([entry]);
    expect(tracker.applyEvent('created', { ...entry, playback_status: 'queued' })).toBe(
      true
    );
    expect(tracker.list).toHaveLength(1);
    expect(tracker.list[0].entry_id).toBe('narr-1');
    expect(tracker.list[0].playback_status).toBe('queued');
  });

  it('keeps duplicate updated events idempotent by entry_id', () => {
    const tracker = new ConversationTracker();
    const entry = {
      entry_id: 'narr-2',
      sequence: 2,
      role: 'assistant' as const,
      text: 'Hello',
      timestamp: 200,
    };
    expect(tracker.applyEvent('created', entry)).toBe(true);
    expect(
      tracker.applyEvent('updated', {
        ...entry,
        sequence: 2,
        playback_status: 'speaking',
      })
    ).toBe(true);
    expect(
      tracker.applyEvent('updated', {
        ...entry,
        sequence: 2,
        playback_status: 'spoken',
      })
    ).toBe(true);
    expect(tracker.list).toHaveLength(1);
    expect(tracker.list[0].playback_status).toBe('spoken');
  });

  it('does not create a second row for a duplicated created event', () => {
    const tracker = new ConversationTracker();
    const entry = {
      entry_id: 'same',
      sequence: 5,
      role: 'assistant' as const,
      text: 'Same',
      timestamp: 1,
    };
    expect(tracker.applyEvent('created', entry)).toBe(true);
    expect(tracker.applyEvent('created', entry)).toBe(true);
    expect(tracker.list).toHaveLength(1);
  });
});
