import { describe, expect, it } from 'vitest';
import {
  CLIENT_PROTOCOL_FORBIDDEN_KEYS,
  createGotoCommand,
  createPauseCommand,
  createRequestId,
  createResumeCommand,
  LessonStateTracker,
  parseServerMessage,
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
});

describe('lessonProtocol', () => {
  it('parses valid state messages', () => {
    const parsed = parseServerMessage(sampleState(1));
    expect(parsed?.type).toBe('lesson.state');
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

  it('does not export secret field names from the protocol module', async () => {
    const mod = await import('./lessonProtocol');
    for (const forbidden of CLIENT_PROTOCOL_FORBIDDEN_KEYS) {
      expect(Object.prototype.hasOwnProperty.call(mod, forbidden)).toBe(false);
    }
  });
});
