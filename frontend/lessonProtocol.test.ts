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
  safety_status: 'normal',
  safety_notice: null,
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
});
