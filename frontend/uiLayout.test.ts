import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  ConversationTracker,
  parseConversationMessage,
} from './lessonProtocol';

const root = resolve(__dirname);
const html = readFileSync(resolve(root, 'index.html'), 'utf8');
const css = readFileSync(resolve(root, 'style.css'), 'utf8');
const appTs = readFileSync(resolve(root, 'app.ts'), 'utf8');

describe('Iteration 11.1 UI layout and conversation', () => {
  it('centers Live Conversation below slide and above technical logs', () => {
    expect(html).toContain('class="app-shell"');
    expect(html).toContain('Live Conversation');
    expect(html).toContain('Technical Logs');
    expect(html).toContain('id="conversation-log"');
    expect(html).toContain('id="debug-log"');
    const stageIdx = html.indexOf('class="slide-stage"');
    const convIdx = html.indexOf('class="conversation-card"');
    const techIdx = html.indexOf('Technical Logs');
    expect(stageIdx).toBeGreaterThan(-1);
    expect(convIdx).toBeGreaterThan(stageIdx);
    expect(techIdx).toBeGreaterThan(convIdx);
  });

  it('has no permanent right sidebar or Follow-live checkbox', () => {
    expect(html).not.toMatch(/right-sidebar|Follow live|follow-live/i);
    expect(appTs).not.toMatch(/Follow live|followLive|follow-live/);
    expect(css).toMatch(/\.right-sidebar[\s\S]*display:\s*none/);
  });

  it('renders Tutor and You conversation styling classes', () => {
    expect(css).toContain('.conv-row.tutor');
    expect(css).toContain('.conv-row.you');
    expect(css).toContain('.conv-slide-badge');
    expect(appTs).toContain("'Tutor'");
    expect(appTs).toContain("'You'");
  });

  it('supports Jump to latest without Follow-live checkbox', () => {
    expect(html).toContain('id="jump-latest-btn"');
    expect(html).toContain('Jump to latest');
    expect(appTs).toContain('jumpLatestBtn');
    expect(appTs).toContain('followConversationLive');
  });

  it('keeps technical logs separate from conversation', () => {
    expect(html).toContain('aria-label="Technical Logs"');
    expect(html).toContain('aria-label="Live Conversation"');
    expect(appTs).toContain('conversation-log');
    expect(appTs).toContain('debug-log');
  });

  it('applies teal / green / blue theme tokens', () => {
    expect(css).toContain('--accent: #0f766e');
    expect(css).toContain('--turquoise: #14b8a6');
    expect(css).toContain('--emerald: #059669');
    expect(css).toContain('--blue: #2563eb');
    expect(css).toContain('--bg-mid: #f0fdfa');
  });

  it('preserves control handlers and focus styles', () => {
    for (const id of [
      'connect-btn',
      'disconnect-btn',
      'pause-btn',
      'resume-btn',
      'goto-btn',
      'slide-select',
    ]) {
      expect(html).toContain(`id="${id}"`);
    }
    expect(appTs).toContain('createPauseCommand()');
    expect(css).toContain(':focus-visible');
    expect(css).toContain('prefers-reduced-motion');
  });

  it('ignores duplicate conversation ids and stale sequences', () => {
    const tracker = new ConversationTracker();
    const base = {
      entry_id: 'e1',
      sequence: 2,
      role: 'assistant' as const,
      text: 'Hello',
      timestamp: 1,
    };
    expect(tracker.applyEvent('created', base)).toBe(true);
    expect(tracker.applyEvent('updated', { ...base, sequence: 1, text: 'Old' })).toBe(
      false
    );
    expect(tracker.applyEvent('updated', { ...base, sequence: 3, text: 'Hi' })).toBe(
      true
    );
    expect(tracker.list).toHaveLength(1);
    expect(tracker.list[0].text).toBe('Hi');
  });

  it('parses conversation snapshot without duplicating on re-apply', () => {
    const msg = parseConversationMessage({
      type: 'conversation.snapshot',
      version: 1,
      entries: [
        {
          entry_id: 'a',
          sequence: 1,
          role: 'user',
          text: 'Hi',
          timestamp: 1,
        },
      ],
    });
    expect(msg?.type).toBe('conversation.snapshot');
    const tracker = new ConversationTracker();
    if (msg && msg.type === 'conversation.snapshot') {
      tracker.applySnapshot(msg.entries);
      tracker.applySnapshot(msg.entries);
    }
    expect(tracker.list).toHaveLength(1);
  });
});
