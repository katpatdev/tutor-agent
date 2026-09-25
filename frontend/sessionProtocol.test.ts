import { describe, expect, it } from 'vitest';
import {
  SESSION_PROTOCOL_FORBIDDEN_KEYS,
  createSessionConfigureCommand,
  parseSessionConfigureResult,
  parseSessionReadyMessage,
} from './sessionProtocol';

describe('sessionProtocol', () => {
  it('creates configure commands with explicit consent defaulting caller choice', () => {
    const msg = createSessionConfigureCommand(false, 'r1');
    expect(msg).toEqual({
      type: 'session.configure',
      version: 1,
      request_id: 'r1',
      transcript_consent: false,
    });
  });

  it('parses session.ready', () => {
    const parsed = parseSessionReadyMessage({
      type: 'session.ready',
      version: 1,
      session_id: 'abc',
      transcript_persistence_available: false,
    });
    expect(parsed?.transcript_persistence_available).toBe(false);
  });

  it('parses configure result and rejects transcript payloads', () => {
    expect(
      parseSessionConfigureResult({
        type: 'session.configure_result',
        version: 1,
        request_id: 'r1',
        ok: true,
        transcript_active: false,
        reason: 'server_disabled',
      })?.reason
    ).toBe('server_disabled');
    expect(
      parseSessionConfigureResult({
        type: 'session.configure_result',
        version: 1,
        request_id: 'r1',
        ok: true,
        transcript_active: true,
        reason: 'enabled',
        transcript_text: 'nope',
      })
    ).toBeNull();
  });

  it('does not declare transcript content keys', () => {
    expect(SESSION_PROTOCOL_FORBIDDEN_KEYS).toContain('transcript_text');
    expect(SESSION_PROTOCOL_FORBIDDEN_KEYS).toContain('OPENAI_API_KEY');
  });
});
