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

describe('Iteration 11.2 visual refinement', () => {
  it('hides empty Sources section and omits empty-state copy from classroom UI', () => {
    expect(html).toContain('id="sources-card"');
    expect(html).toContain('hidden');
    expect(html).toContain('Sources used');
    expect(html).not.toMatch(/No matching uploaded sources/i);
    expect(html).not.toMatch(/Sources used for latest answer/i);
    expect(html).not.toMatch(/Presented from classroom knowledge/i);
    expect(appTs).toContain('this.sourcesCard.hidden = true');
    expect(appTs).toContain('this.sourcesCard.hidden = false');
    expect(appTs).not.toMatch(/No matching uploaded sources for the latest answer/);
  });

  it('renders Tutor and Student avatars with labels', () => {
    expect(appTs).toContain("createAvatar(isUser ? 'you' : 'tutor')");
    expect(appTs).toContain("'Tutor'");
    expect(appTs).toContain("'You'");
    expect(css).toContain('.conv-avatar-tutor');
    expect(css).toContain('.conv-avatar-you');
    expect(appTs).toContain("setAttribute('aria-hidden', 'true')");
  });

  it('groups consecutive Tutor messages without dropping content', () => {
    expect(appTs).toContain('is-grouped');
    expect(appTs).toContain('grouped');
    expect(appTs).toContain("entry.role === 'assistant'");
    expect(css).toContain('.conv-row.is-grouped');
  });

  it('uses existing runtime state for speaking and listening indicators', () => {
    expect(appTs).toContain('conversationCard.dataset.voice');
    expect(appTs).toContain("label: 'Speaking'");
    expect(appTs).toContain("label: 'Listening'");
    expect(css).toContain('[data-voice="speaking"]');
    expect(css).toContain('[data-voice="listening"]');
    expect(css).toContain('prefers-reduced-motion');
  });

  it('keeps Live Conversation above Technical Logs without a right sidebar', () => {
    const convIdx = html.indexOf('Live Conversation');
    const techIdx = html.indexOf('Technical Logs');
    expect(convIdx).toBeGreaterThan(-1);
    expect(techIdx).toBeGreaterThan(convIdx);
    expect(html).not.toMatch(/right-sidebar|Follow live|follow-live/i);
    expect(css).toMatch(/\.right-sidebar[\s\S]*display:\s*none/);
  });

  it('provides collapsible Technical Logs', () => {
    expect(html).toContain('<details class="activity-card tech-logs"');
    expect(html).toContain('<summary class="tech-logs-summary">');
    expect(html).toContain('id="debug-log"');
    expect(css).toContain('.tech-logs-summary');
  });

  it('supports Jump to latest and preserves control handlers', () => {
    expect(html).toContain('id="jump-latest-btn"');
    expect(appTs).toContain('followConversationLive');
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
    expect(appTs).toContain('createResumeCommand()');
  });

  it('applies teal/green/blue palette and polished slide stage', () => {
    expect(css).toContain('--accent: #0f766e');
    expect(css).toContain('--turquoise: #14b8a6');
    expect(css).toContain('--emerald: #059669');
    expect(css).toContain('--blue: #2563eb');
    expect(css).toContain('.stage-watermark');
    expect(html).toContain('id="stage-watermark"');
    expect(css).toContain(':focus-visible');
    expect(css).toContain('overflow-x: hidden');
    expect(css).toContain('@media (max-width: 1024px)');
    expect(css).toContain('@media (max-width: 768px)');
    expect(css).toContain('@media (max-width: 390px)');
  });

  it('keeps conversation chronological and dedupes entries', () => {
    const tracker = new ConversationTracker();
    tracker.applyEvent('created', {
      entry_id: '1',
      sequence: 1,
      role: 'assistant',
      text: 'A',
      timestamp: 1,
      slide_number: 2,
    });
    tracker.applyEvent('created', {
      entry_id: '2',
      sequence: 2,
      role: 'assistant',
      text: 'B',
      timestamp: 2,
      slide_number: 2,
    });
    tracker.applyEvent('created', {
      entry_id: '3',
      sequence: 3,
      role: 'user',
      text: 'C',
      timestamp: 3,
    });
    expect(tracker.list.map((e) => e.text)).toEqual(['A', 'B', 'C']);
    const snap = parseConversationMessage({
      type: 'conversation.snapshot',
      version: 1,
      entries: tracker.list,
    });
    expect(snap?.type).toBe('conversation.snapshot');
  });
});

describe('Iteration 11.4 welcome screen and copy', () => {
  it('shows welcome copy and what-to-expect items while disconnected HTML defaults', () => {
    expect(html).toContain('AI Science Tutor');
    expect(html).toContain('A friendly, voice-guided science lesson');
    expect(html).toContain('Learn science through conversation');
    expect(html).toMatch(
      /Meet your friendly AI tutor for a guided lesson about natural\s+disasters/
    );
    expect(html).toContain('8 guided topics');
    expect(html).toContain('Ask questions anytime');
    expect(html).toContain('Pause or revisit a topic');
    expect(html).toContain('Lesson not started');
    expect(html).toContain('Voice-guided lesson');
    expect(html).toMatch(/id="lesson-mode"[^>]*>Ready</);
    expect(html).not.toMatch(/id="lesson-slide"[^>]*>Slide 0 of 8</);
    expect(html).not.toMatch(/id="lesson-slide"[^>]*>Slide 1 of 8</);
    expect(html).toContain('id="slide-track"');
    expect(html).toContain('id="progress-bar"');
  });

  it('implements preparing, IDLE gate, PRESENTING, and disconnect display rules', () => {
    expect(appTs).toContain('showWelcomeDisplay');
    expect(appTs).toContain('showPreparingDisplay');
    expect(appTs).toContain("Lesson not started");
    expect(appTs).toContain('Slide 0 of ${this.slideTotal}');
    expect(appTs).toContain('Preparing your lesson…');
    expect(appTs).toContain('Getting ready');
    expect(appTs).toContain("state.mode === 'IDLE'");
    expect(appTs).toContain('this.showPreparingDisplay()');
    expect(appTs).toContain("Slide ${state.slide.number} of ${state.slide.total}");
    expect(appTs).toContain('this.showPreparingDisplay();');
    expect(appTs).toContain('this.showWelcomeDisplay()');
    expect(appTs).toMatch(/connect\(\)[\s\S]*showPreparingDisplay/);
    expect(appTs).toMatch(/disconnect\(\)[\s\S]*showWelcomeDisplay/);
    expect(appTs).toMatch(/onDisconnected:[\s\S]*showWelcomeDisplay/);
    expect(appTs).toContain('updateProgress(0, this.slideTotal)');
    expect(appTs).toContain("setLessonDisplayPhase('active')");
  });

  it('keeps IDLE backend slide numbers out of the visible progress label', () => {
    const idleBranch = appTs.slice(
      appTs.indexOf("if (state.mode === 'IDLE')"),
      appTs.indexOf("this.setLessonDisplayPhase('active')")
    );
    expect(idleBranch).toContain('showPreparingDisplay');
    expect(idleBranch).not.toContain('state.slide.title');
    expect(idleBranch).not.toMatch(
      /slideSpan\.textContent = `Slide \$\{state\.slide\.number\}/
    );
  });

  it('keeps transcript consent optional, clear, and non-retraining', () => {
    expect(html).toContain('Help improve future lessons');
    expect(html).toContain('id="transcript-consent"');
    expect(html).toMatch(
      /<label[^>]*for="transcript-consent"[\s\S]*<input type="checkbox" id="transcript-consent"/
    );
    expect(html).toMatch(/redacted conversation transcript[\s\S]*stored\s+locally/i);
    expect(html).toMatch(/does not\s+automatically retrain/i);
    expect(html).not.toMatch(/automatically retrain the tutor for this session/i);
    expect(appTs).toContain('this.transcriptConsent.checked = false');
  });

  it('explains temporary Classroom Knowledge storage without vector-db wording', () => {
    expect(html).toContain('Add your classroom notes');
    expect(html).toContain('Classroom Knowledge');
    expect(html).toContain('id="knowledge-storage-note"');
    expect(html).toMatch(/server memory for the current backend session/i);
    expect(html).toMatch(/cleared when\s+the backend restarts/i);
    expect(html.toLowerCase()).not.toContain('vector database');
  });

  it('keeps sources hidden until real matches and softens empty source UI', () => {
    expect(html).toContain('id="sources-card"');
    expect(html).toMatch(/id="sources-card"[\s\S]*hidden/);
    expect(html).not.toMatch(/Sources used for latest answer:\s*—/);
    expect(appTs).toContain('this.sourcesCard.hidden = true');
    expect(appTs).toContain('this.sourcesCard.hidden = false');
  });

  it('provides Live Conversation empty state that clears after entries', () => {
    expect(html).toContain('id="conversation-empty"');
    expect(html).toMatch(
      /Your conversation with the tutor will appear here after the lesson\s+begins\./
    );
    expect(appTs).toContain('this.conversationEmpty.hidden = entries.length > 0');
  });

  it('clarifies Technical Logs without changing their structure', () => {
    expect(html).toContain('Technical Logs');
    expect(html).toContain(
      'Connection events, lesson state, commands, and diagnostics appear'
    );
    expect(html).toContain('<details class="activity-card tech-logs"');
    expect(html).toContain('id="debug-log"');
  });

  it('preserves control IDs and protocol/conversation behavior', () => {
    for (const id of [
      'connect-btn',
      'disconnect-btn',
      'pause-btn',
      'resume-btn',
      'goto-btn',
      'slide-select',
      'transcript-consent',
      'knowledge-upload-btn',
      'knowledge-file',
    ]) {
      expect(html).toContain(`id="${id}"`);
    }
    expect(appTs).toContain('parseServerMessage');
    expect(appTs).toContain('parseConversationMessage');
    expect(appTs).toContain('ConversationTracker');
    expect(css).toContain('@media (max-width: 768px)');
  });
});
