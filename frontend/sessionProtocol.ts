/**
 * Session consent / configuration protocol helpers (no DOM, no secrets).
 */

import { createRequestId, PROTOCOL_VERSION } from './lessonProtocol';

export type SessionReadyMessage = {
  type: 'session.ready';
  version: 1;
  session_id: string;
  transcript_persistence_available: boolean;
};

export type SessionConfigureMessage = {
  type: 'session.configure';
  version: 1;
  request_id: string;
  transcript_consent: boolean;
};

export type SessionConfigureResultMessage = {
  type: 'session.configure_result';
  version: 1;
  request_id: string;
  ok: boolean;
  transcript_active: boolean;
  reason: string;
  error?: { code: string; message: string };
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function createSessionConfigureCommand(
  transcriptConsent: boolean,
  requestId: string = createRequestId()
): SessionConfigureMessage {
  return {
    type: 'session.configure',
    version: PROTOCOL_VERSION,
    request_id: requestId,
    transcript_consent: transcriptConsent,
  };
}

export function parseSessionReadyMessage(raw: unknown): SessionReadyMessage | null {
  if (!isObject(raw)) return null;
  if (raw.type !== 'session.ready') return null;
  if (raw.version !== 1) return null;
  if (typeof raw.session_id !== 'string') return null;
  if (typeof raw.transcript_persistence_available !== 'boolean') return null;
  return raw as SessionReadyMessage;
}

export function parseSessionConfigureResult(
  raw: unknown
): SessionConfigureResultMessage | null {
  if (!isObject(raw)) return null;
  if (raw.type !== 'session.configure_result') return null;
  if (raw.version !== 1) return null;
  if (typeof raw.request_id !== 'string') return null;
  if (typeof raw.ok !== 'boolean') return null;
  if (typeof raw.transcript_active !== 'boolean') return null;
  if (typeof raw.reason !== 'string') return null;
  if ('transcript_text' in raw || 'redacted_text' in raw) return null;
  return raw as SessionConfigureResultMessage;
}

export const SESSION_PROTOCOL_FORBIDDEN_KEYS = [
  'transcript_text',
  'redacted_text',
  'OPENAI_API_KEY',
  'db_path',
] as const;
