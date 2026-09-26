/**
 * Copyright (c) 2024–2025, Daily
 *
 * SPDX-License-Identifier: BSD 2-Clause License
 */

import {
  PipecatClient,
  type PipecatClientOptions,
  RTVIEvent,
} from '@pipecat-ai/client-js';
import { WebSocketTransport } from '@pipecat-ai/websocket-transport';
import {
  createGetStateCommand,
  createGotoCommand,
  createPauseCommand,
  createResumeCommand,
  ConversationTracker,
  formatNarrationStatus,
  humanModeLabel,
  LessonStateTracker,
  parseConversationMessage,
  parseServerMessage,
  type ConversationEntry,
  type LessonCommandMessage,
  type LessonCommandResultMessage,
  type LessonStateMessage,
} from './lessonProtocol';
import {
  createSessionConfigureCommand,
  parseSessionConfigureResult,
  parseSessionReadyMessage,
} from './sessionProtocol';
import {
  parseKnowledgeRetrievalMessage,
  parseKnowledgeStatusResponse,
  parseKnowledgeUploadResponse,
  validateKnowledgeFileClient,
  type KnowledgeRetrievalSource,
} from './knowledgeProtocol';

const BOT_API_URL =
  import.meta.env.VITE_BOT_API_URL || 'http://localhost:7860';

const WELCOME_TITLE = 'Learn science through conversation';
const WELCOME_SUPPORTING =
  'Meet your friendly AI tutor for a guided lesson about natural disasters. Listen at your own pace, ask questions at any time, pause when needed, or move between lesson topics.';
const WELCOME_PROGRESS_TITLE = 'Connect when you are ready';
const PREPARING_MESSAGE = 'Preparing your lesson…';
const CONTROLS_INTRO_DISCONNECTED =
  'Connect when you are ready. During the lesson, you can pause the tutor, resume from the supported narration point, or move to another slide.';
const CONTROLS_INTRO_CONNECTED =
  'Use Pause, Resume, or Go to Slide while the lesson is connected.';

type LessonDisplayPhase = 'welcome' | 'preparing' | 'active';

class WebsocketClientApp {
  private pcClient: PipecatClient | null = null;
  private connectBtn: HTMLButtonElement | null = null;
  private disconnectBtn: HTMLButtonElement | null = null;
  private pauseBtn: HTMLButtonElement | null = null;
  private resumeBtn: HTMLButtonElement | null = null;
  private gotoBtn: HTMLButtonElement | null = null;
  private slideSelect: HTMLSelectElement | null = null;
  private statusSpan: HTMLElement | null = null;
  private modeSpan: HTMLElement | null = null;
  private slideSpan: HTMLElement | null = null;
  private titleSpan: HTMLElement | null = null;
  private narrationStatusEl: HTMLElement | null = null;
  private errorBox: HTMLElement | null = null;
  private safetyNotice: HTMLElement | null = null;
  private debugLog: HTMLElement | null = null;
  private knowledgeFile: HTMLInputElement | null = null;
  private knowledgeUploadBtn: HTMLButtonElement | null = null;
  private knowledgeStatusEl: HTMLElement | null = null;
  private knowledgeErrorEl: HTMLElement | null = null;
  private knowledgeCountsEl: HTMLElement | null = null;
  private knowledgeSourcesEl: HTMLElement | null = null;
  private sourcesCard: HTMLElement | null = null;
  private conversationCard: HTMLElement | null = null;
  private conversationVoiceHint: HTMLElement | null = null;
  private progressCurrentTitle: HTMLElement | null = null;
  private stageWatermark: HTMLElement | null = null;
  private transcriptConsent: HTMLInputElement | null = null;
  private transcriptStatusEl: HTMLElement | null = null;
  private transcriptStorageStateEl: HTMLElement | null = null;
  private connectionPill: HTMLElement | null = null;
  private tutorStatusEl: HTMLElement | null = null;
  private tutorStatusLabel: HTMLElement | null = null;
  private stageSlideNumber: HTMLElement | null = null;
  private slideTrack: HTMLElement | null = null;
  private progressBar: HTMLElement | null = null;
  private progressFill: HTMLElement | null = null;
  private progressCard: HTMLElement | null = null;
  private slideStage: HTMLElement | null = null;
  private welcomeExpect: HTMLElement | null = null;
  private conversationEmpty: HTMLElement | null = null;
  private controlsIntro: HTMLElement | null = null;
  private clearActivityBtn: HTMLButtonElement | null = null;
  private conversationLog: HTMLElement | null = null;
  private jumpLatestBtn: HTMLButtonElement | null = null;
  private lessonDisplayPhase: LessonDisplayPhase = 'welcome';
  private botAudio: HTMLAudioElement;
  private stateTracker = new LessonStateTracker();
  private conversationTracker = new ConversationTracker();
  private followConversationLive = true;
  private connected = false;
  private commandPending = false;
  private pendingRequestId: string | null = null;
  private knowledgeUploading = false;
  private maxUploadBytes = 5_242_880;
  private configureSent = false;
  private sessionConsentLocked = false;
  private readonly slideTotal = 8;

  constructor() {
    const existingAudio = document.getElementById(
      'bot-audio'
    ) as HTMLAudioElement | null;
    this.botAudio = existingAudio ?? document.createElement('audio');
    this.botAudio.autoplay = true;
    if (!existingAudio) {
      document.body.appendChild(this.botAudio);
    }

    this.setupDOMElements();
    this.setupEventListeners();
    this.showWelcomeDisplay();
    this.applyControlAvailability(null);
    void this.refreshKnowledgeStatus();
  }

  private setupDOMElements(): void {
    this.connectBtn = document.getElementById('connect-btn') as HTMLButtonElement;
    this.disconnectBtn = document.getElementById(
      'disconnect-btn'
    ) as HTMLButtonElement;
    this.pauseBtn = document.getElementById('pause-btn') as HTMLButtonElement;
    this.resumeBtn = document.getElementById('resume-btn') as HTMLButtonElement;
    this.gotoBtn = document.getElementById('goto-btn') as HTMLButtonElement;
    this.slideSelect = document.getElementById(
      'slide-select'
    ) as HTMLSelectElement;
    this.statusSpan = document.getElementById('connection-status');
    this.modeSpan = document.getElementById('lesson-mode');
    this.slideSpan = document.getElementById('lesson-slide');
    this.titleSpan = document.getElementById('lesson-title');
    this.narrationStatusEl = document.getElementById('narration-status');
    this.errorBox = document.getElementById('command-error');
    this.safetyNotice = document.getElementById('safety-notice');
    this.debugLog = document.getElementById('debug-log');
    this.knowledgeFile = document.getElementById(
      'knowledge-file'
    ) as HTMLInputElement;
    this.knowledgeUploadBtn = document.getElementById(
      'knowledge-upload-btn'
    ) as HTMLButtonElement;
    this.knowledgeStatusEl = document.getElementById('knowledge-upload-status');
    this.knowledgeErrorEl = document.getElementById('knowledge-upload-error');
    this.knowledgeCountsEl = document.getElementById('knowledge-counts');
    this.knowledgeSourcesEl = document.getElementById('knowledge-sources');
    this.sourcesCard = document.getElementById('sources-card');
    this.conversationCard = document.getElementById('conversation-card');
    this.conversationVoiceHint = document.getElementById(
      'conversation-voice-hint'
    );
    this.progressCurrentTitle = document.getElementById(
      'progress-current-title'
    );
    this.stageWatermark = document.getElementById('stage-watermark');
    this.transcriptConsent = document.getElementById(
      'transcript-consent'
    ) as HTMLInputElement;
    this.transcriptStatusEl = document.getElementById('transcript-status');
    this.transcriptStorageStateEl = document.getElementById(
      'transcript-storage-state'
    );
    this.connectionPill = document.getElementById('connection-pill');
    this.tutorStatusEl = document.getElementById('tutor-status');
    this.tutorStatusLabel = document.getElementById('tutor-status-label');
    this.stageSlideNumber = document.getElementById('stage-slide-number');
    this.slideTrack = document.getElementById('slide-track');
    this.progressBar = document.getElementById('progress-bar');
    this.progressFill = document.getElementById('progress-fill');
    this.progressCard = document.getElementById('progress-card');
    this.slideStage = document.getElementById('slide-stage');
    this.welcomeExpect = document.getElementById('welcome-expect');
    this.conversationEmpty = document.getElementById('conversation-empty');
    this.controlsIntro = document.getElementById('controls-intro');
    this.clearActivityBtn = document.getElementById(
      'clear-activity-btn'
    ) as HTMLButtonElement;
    this.conversationLog = document.getElementById('conversation-log');
    this.jumpLatestBtn = document.getElementById(
      'jump-latest-btn'
    ) as HTMLButtonElement;
    if (this.transcriptConsent) {
      this.transcriptConsent.checked = false;
      this.transcriptConsent.disabled = false;
      this.transcriptConsent.addEventListener('change', () => {
        if (this.sessionConsentLocked) {
          return;
        }
        this.updateTranscriptStorageLabel();
      });
    }
    this.updateTranscriptStorageLabel();
    this.setConnectionVisual('disconnected', 'Disconnected');
    this.setTranscriptStatus(
      'Transcript saving is optional and off by default. Check the box before Connect. Changes after Connect apply only to the next session.'
    );
  }

  private setupEventListeners(): void {
    this.connectBtn?.addEventListener('click', () => this.connect());
    this.disconnectBtn?.addEventListener('click', () => this.disconnect());
    this.pauseBtn?.addEventListener('click', () =>
      this.sendCommand(createPauseCommand())
    );
    this.resumeBtn?.addEventListener('click', () =>
      this.sendCommand(createResumeCommand())
    );
    this.gotoBtn?.addEventListener('click', () => {
      const value = Number(this.slideSelect?.value || '1');
      void this.sendCommand(createGotoCommand(value));
    });
    this.knowledgeUploadBtn?.addEventListener('click', () => {
      void this.uploadKnowledge();
    });
    this.clearActivityBtn?.addEventListener('click', () => {
      if (this.debugLog) {
        this.debugLog.replaceChildren();
      }
    });
    this.jumpLatestBtn?.addEventListener('click', () => {
      this.followConversationLive = true;
      this.hideJumpLatest();
      this.scrollConversationToLatest();
    });
    this.conversationLog?.addEventListener('scroll', () => {
      this.onConversationScroll();
    });
  }

  private classifyLogMessage(message: string): string {
    if (message.startsWith('User: ')) return 'entry-user';
    if (message.startsWith('Bot: ')) return 'entry-bot';
    if (
      message.startsWith('State ') ||
      message.startsWith('Ignored stale state')
    ) {
      return 'entry-state';
    }
    if (
      message.startsWith('Status:') ||
      message.startsWith('Sent ') ||
      message.startsWith('Command ok') ||
      message.startsWith('Bot ready') ||
      message.startsWith('Connecting') ||
      message.startsWith('Connection complete') ||
      message.startsWith('Initializing') ||
      message.startsWith('Client disconnected') ||
      message.startsWith('Setting up audio') ||
      message.startsWith('Track stopped') ||
      message.startsWith('Knowledge retrieval')
    ) {
      return 'entry-status';
    }
    if (
      message.startsWith('Error') ||
      message.startsWith('Command error') ||
      message.startsWith('Failed') ||
      message.startsWith('Audio warning')
    ) {
      return 'entry-error';
    }
    return 'entry-status';
  }

  private log(message: string): void {
    if (!this.debugLog) return;
    const entry = document.createElement('div');
    entry.className = `activity-entry ${this.classifyLogMessage(message)}`;
    const time = document.createElement('span');
    time.className = 'entry-time';
    time.textContent = new Date().toLocaleTimeString();
    const body = document.createElement('span');
    body.textContent = message;
    entry.append(time, body);
    this.debugLog.appendChild(entry);
    this.debugLog.scrollTop = this.debugLog.scrollHeight;
  }

  private onConversationScroll(): void {
    const el = this.conversationLog;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distance > 48) {
      this.followConversationLive = false;
      this.showJumpLatest();
    } else {
      this.followConversationLive = true;
      this.hideJumpLatest();
    }
  }

  private showJumpLatest(): void {
    if (this.jumpLatestBtn) this.jumpLatestBtn.hidden = false;
  }

  private hideJumpLatest(): void {
    if (this.jumpLatestBtn) this.jumpLatestBtn.hidden = true;
  }

  private scrollConversationToLatest(): void {
    const el = this.conversationLog;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }

  private renderConversation(): void {
    const el = this.conversationLog;
    if (!el) return;
    el.replaceChildren();
    const entries = this.conversationTracker.list;
    for (let i = 0; i < entries.length; i += 1) {
      const entry = entries[i];
      const prev = i > 0 ? entries[i - 1] : null;
      const grouped =
        prev != null &&
        prev.role === entry.role &&
        entry.role === 'assistant' &&
        prev.slide_number === entry.slide_number;
      el.appendChild(this.buildConversationRow(entry, { grouped }));
    }
    if (this.conversationEmpty) {
      this.conversationEmpty.hidden = entries.length > 0;
    }
    if (this.followConversationLive) {
      this.scrollConversationToLatest();
    }
  }

  private setLessonDisplayPhase(phase: LessonDisplayPhase): void {
    this.lessonDisplayPhase = phase;
    document.body.dataset.lessonPhase = phase;
    if (this.progressCard) {
      this.progressCard.dataset.phase = phase;
    }
    if (this.slideStage) {
      this.slideStage.dataset.phase = phase;
    }
    if (this.welcomeExpect) {
      this.welcomeExpect.hidden = phase !== 'welcome';
    }
  }

  private setProgressChromeVisible(visible: boolean): void {
    if (this.slideTrack) {
      this.slideTrack.hidden = !visible;
      this.slideTrack.setAttribute('aria-hidden', visible ? 'false' : 'true');
      if (!visible) {
        this.slideTrack.replaceChildren();
      }
    }
    if (this.progressBar) {
      this.progressBar.hidden = !visible;
      this.progressBar.setAttribute('aria-hidden', visible ? 'false' : 'true');
      if (!visible) {
        this.progressBar.setAttribute('aria-valuenow', '0');
      }
    }
  }

  private showWelcomeDisplay(): void {
    this.setLessonDisplayPhase('welcome');
    this.setProgressChromeVisible(false);
    if (this.progressFill) {
      this.progressFill.style.width = '0%';
    }
    if (this.slideSpan) {
      this.slideSpan.textContent = 'Lesson not started';
    }
    if (this.progressCurrentTitle) {
      this.progressCurrentTitle.textContent = WELCOME_PROGRESS_TITLE;
    }
    if (this.modeSpan) {
      this.modeSpan.textContent = 'Ready';
    }
    if (this.titleSpan) {
      this.titleSpan.textContent = WELCOME_TITLE;
    }
    if (this.narrationStatusEl) {
      this.narrationStatusEl.textContent = WELCOME_SUPPORTING;
    }
    if (this.stageSlideNumber) {
      this.stageSlideNumber.textContent = 'Voice-guided lesson';
    }
    if (this.stageWatermark) {
      this.stageWatermark.textContent = '—';
    }
    if (this.controlsIntro) {
      this.controlsIntro.textContent = CONTROLS_INTRO_DISCONNECTED;
    }
    this.setTutorStatusFromMode(null);
  }

  private showPreparingDisplay(): void {
    this.setLessonDisplayPhase('preparing');
    this.setProgressChromeVisible(true);
    this.updateProgress(0, this.slideTotal);
    if (this.slideSpan) {
      this.slideSpan.textContent = `Slide 0 of ${this.slideTotal}`;
    }
    if (this.progressCurrentTitle) {
      this.progressCurrentTitle.textContent = PREPARING_MESSAGE;
    }
    if (this.modeSpan) {
      this.modeSpan.textContent = 'Ready';
    }
    if (this.titleSpan) {
      this.titleSpan.textContent = PREPARING_MESSAGE;
    }
    if (this.narrationStatusEl) {
      this.narrationStatusEl.textContent =
        'Connecting to your tutor and preparing the first topic.';
    }
    if (this.stageSlideNumber) {
      this.stageSlideNumber.textContent = 'Getting ready';
    }
    if (this.stageWatermark) {
      this.stageWatermark.textContent = '0';
    }
    if (this.controlsIntro) {
      this.controlsIntro.textContent = CONTROLS_INTRO_CONNECTED;
    }
    this.setTutorStatusFromMode('IDLE');
  }

  private createAvatar(kind: 'tutor' | 'you'): HTMLElement {
    const avatar = document.createElement('div');
    avatar.className = `conv-avatar conv-avatar-${kind}`;
    avatar.setAttribute('aria-hidden', 'true');
    avatar.innerHTML =
      kind === 'tutor'
        ? `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 8l10 5 10-5-10-5Z"/><path d="M6 10.5V16c0 1.5 2.7 3 6 3s6-1.5 6-3v-5.5"/><path d="M22 8v7"/></svg>`
        : `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="3.5"/><path d="M5 19.5c1.8-3.2 4.2-4.8 7-4.8s5.2 1.6 7 4.8"/></svg>`;
    return avatar;
  }

  private buildConversationRow(
    entry: ConversationEntry,
    opts: { grouped: boolean }
  ): HTMLElement {
    const row = document.createElement('div');
    const isUser = entry.role === 'user';
    row.className = `conv-row ${isUser ? 'you' : 'tutor'}${
      opts.grouped ? ' is-grouped' : ''
    }`;

    if (!opts.grouped) {
      row.appendChild(this.createAvatar(isUser ? 'you' : 'tutor'));
    } else {
      const spacer = document.createElement('div');
      spacer.className = 'conv-avatar-spacer';
      spacer.setAttribute('aria-hidden', 'true');
      row.appendChild(spacer);
    }

    const bubble = document.createElement('div');
    bubble.className = 'conv-bubble';
    const meta = document.createElement('div');
    meta.className = 'conv-meta';
    if (!opts.grouped) {
      const role = document.createElement('span');
      role.className = 'conv-role';
      role.textContent = isUser ? 'You' : 'Tutor';
      meta.appendChild(role);
      if (!isUser) {
        const speaking = document.createElement('span');
        speaking.className = 'conv-speaking-dots';
        speaking.setAttribute('aria-hidden', 'true');
        speaking.innerHTML = '<i></i><i></i><i></i>';
        meta.appendChild(speaking);
      }
    }
    if (entry.slide_number != null && !opts.grouped) {
      const badge = document.createElement('span');
      badge.className = 'conv-slide-badge';
      badge.textContent =
        entry.slide_title != null && entry.slide_title.length > 0
          ? `Slide ${entry.slide_number}`
          : `Slide ${entry.slide_number}`;
      meta.appendChild(badge);
    }
    const time = document.createElement('span');
    time.className = 'conv-time';
    time.textContent = new Date(entry.timestamp * 1000).toLocaleTimeString();
    meta.appendChild(time);
    const text = document.createElement('p');
    text.className = 'conv-text';
    text.textContent = entry.text;
    bubble.append(meta, text);
    row.appendChild(bubble);
    return row;
  }

  private setConnectionVisual(
    status: 'connected' | 'connecting' | 'disconnected' | 'error',
    label: string
  ): void {
    if (this.connectionPill) {
      this.connectionPill.dataset.status = status;
    }
    if (this.statusSpan) {
      this.statusSpan.textContent = label;
    }
  }

  private setTutorStatusFromMode(mode: string | null): void {
    if (!this.tutorStatusEl || !this.tutorStatusLabel) return;
    const map: Record<string, { status: string; label: string }> = {
      IDLE: { status: 'idle', label: 'Ready' },
      PRESENTING: { status: 'speaking', label: 'Speaking' },
      INTERRUPTED: { status: 'listening', label: 'Listening' },
      ANSWERING: { status: 'answering', label: 'Answering' },
      PAUSED: { status: 'paused', label: 'Paused' },
      QA_MODE: { status: 'qa', label: 'Waiting for questions' },
      FINISHED: { status: 'finished', label: 'Finished' },
    };
    const resolved = (mode && map[mode]) || { status: 'idle', label: 'Ready' };
    this.tutorStatusEl.dataset.status = resolved.status;
    this.tutorStatusLabel.textContent = resolved.label;
    if (this.conversationCard) {
      this.conversationCard.dataset.voice = resolved.status;
    }
    if (this.conversationVoiceHint) {
      if (resolved.status === 'speaking' || resolved.status === 'answering') {
        this.conversationVoiceHint.textContent = 'Tutor is speaking';
      } else if (resolved.status === 'listening' || resolved.status === 'qa') {
        this.conversationVoiceHint.textContent = 'Listening for you';
      } else {
        this.conversationVoiceHint.textContent = '';
      }
    }
  }

  private renderSlideTrack(currentNumber: number): void {
    if (!this.slideTrack) return;
    this.slideTrack.replaceChildren();
    for (let i = 1; i <= this.slideTotal; i += 1) {
      const dot = document.createElement('span');
      dot.className = 'slide-dot';
      dot.setAttribute('role', 'listitem');
      dot.title = `Slide ${i}`;
      if (i === currentNumber) {
        dot.classList.add('is-current');
        dot.setAttribute('aria-current', 'step');
      } else if (i < currentNumber) {
        dot.classList.add('is-complete');
      } else {
        dot.classList.add('is-upcoming');
      }
      this.slideTrack.appendChild(dot);
    }
  }

  private updateProgress(slideNumber: number, total: number): void {
    const safeTotal = total > 0 ? total : this.slideTotal;
    const clamped = Math.max(0, Math.min(slideNumber, safeTotal));
    this.renderSlideTrack(clamped);
    if (this.progressFill) {
      const pct = safeTotal > 0 ? (clamped / safeTotal) * 100 : 0;
      this.progressFill.style.width = `${pct}%`;
    }
    if (this.progressBar) {
      this.progressBar.setAttribute('aria-valuemax', String(safeTotal));
      this.progressBar.setAttribute('aria-valuenow', String(clamped));
    }
    if (this.lessonDisplayPhase === 'active' && this.stageSlideNumber) {
      this.stageSlideNumber.textContent =
        clamped > 0 ? `Slide ${clamped} of ${safeTotal}` : 'Getting ready';
    }
    if (this.lessonDisplayPhase === 'active' && this.stageWatermark) {
      this.stageWatermark.textContent = clamped > 0 ? String(clamped) : '0';
    }
  }

  private setError(message: string): void {
    if (this.errorBox) {
      this.errorBox.textContent = message;
    }
  }

  private clearError(): void {
    this.setError('');
  }

  private updateStatus(status: string): void {
    const normalized = status.trim().toLowerCase();
    if (normalized.includes('error')) {
      this.setConnectionVisual('error', status);
    } else if (normalized.includes('connecting')) {
      this.setConnectionVisual('connecting', status);
    } else if (normalized.includes('connected')) {
      this.setConnectionVisual('connected', status);
    } else if (normalized.includes('disconnected')) {
      this.setConnectionVisual('disconnected', status);
    } else {
      if (this.statusSpan) {
        this.statusSpan.textContent = status;
      }
    }
    this.log(`Status: ${status}`);
  }

  private applyLessonState(state: LessonStateMessage): void {
    // Display-only: IDLE keeps preparing chrome so backend slide 1 does not leak.
    if (state.mode === 'IDLE') {
      this.showPreparingDisplay();
      if (this.modeSpan) {
        this.modeSpan.textContent = humanModeLabel(state.mode);
      }
      if (this.slideSelect) {
        this.slideSelect.value = String(state.slide.number);
      }
      if (this.safetyNotice) {
        const safety = state.safety_notice || '';
        const audio = state.audio_warning || '';
        this.safetyNotice.textContent = [safety, audio].filter(Boolean).join(' ');
        if (audio) {
          this.log(`Audio warning: ${audio}`);
        }
      }
      this.applyControlAvailability(state);
      return;
    }

    this.setLessonDisplayPhase('active');
    this.setProgressChromeVisible(true);
    if (this.controlsIntro) {
      this.controlsIntro.textContent = CONTROLS_INTRO_CONNECTED;
    }
    if (this.modeSpan) {
      this.modeSpan.textContent = humanModeLabel(state.mode);
    }
    if (this.slideSpan) {
      this.slideSpan.textContent = `Slide ${state.slide.number} of ${state.slide.total}`;
    }
    if (this.titleSpan) {
      this.titleSpan.textContent = state.slide.title;
    }
    if (this.progressCurrentTitle) {
      this.progressCurrentTitle.textContent = state.slide.title;
    }
    if (this.slideSelect) {
      this.slideSelect.value = String(state.slide.number);
    }
    if (this.safetyNotice) {
      const safety = state.safety_notice || '';
      const audio = state.audio_warning || '';
      this.safetyNotice.textContent = [safety, audio].filter(Boolean).join(' ');
      if (audio) {
        this.log(`Audio warning: ${audio}`);
      }
    }
    if (this.narrationStatusEl) {
      const narration = formatNarrationStatus(state.narration);
      this.narrationStatusEl.textContent =
        narration || WELCOME_SUPPORTING;
    }
    this.setTutorStatusFromMode(state.mode);
    this.updateProgress(state.slide.number, state.slide.total);
    this.applyControlAvailability(state);
  }

  private applyControlAvailability(state: LessonStateMessage | null): void {
    const lessonEnabled = this.connected && !this.commandPending;
    if (this.pauseBtn) {
      this.pauseBtn.disabled = !(lessonEnabled && !!state?.can_pause);
    }
    if (this.resumeBtn) {
      this.resumeBtn.disabled = !(lessonEnabled && !!state?.can_resume);
    }
    if (this.gotoBtn) {
      this.gotoBtn.disabled = !(lessonEnabled && !!state?.can_navigate);
    }
    if (this.slideSelect) {
      this.slideSelect.disabled = !(lessonEnabled && !!state?.can_navigate);
    }
    if (this.connectBtn) {
      this.connectBtn.disabled = this.connected;
    }
    if (this.disconnectBtn) {
      this.disconnectBtn.disabled = !this.connected;
    }
  }

  private setTranscriptStatus(message: string): void {
    if (this.transcriptStatusEl) {
      this.transcriptStatusEl.textContent = message;
    }
  }

  private updateTranscriptStorageLabel(): void {
    if (!this.transcriptStorageStateEl) {
      return;
    }
    const on = !!this.transcriptConsent?.checked;
    if (this.sessionConsentLocked) {
      this.transcriptStorageStateEl.textContent = on
        ? 'Transcript storage: ON (locked for this session)'
        : 'Transcript storage: OFF (locked for this session)';
      return;
    }
    this.transcriptStorageStateEl.textContent = on
      ? 'Transcript storage: ON'
      : 'Transcript storage: OFF';
  }

  private handleServerPayload(data: unknown): void {
    const ready = parseSessionReadyMessage(data);
    if (ready) {
      if (!ready.transcript_persistence_available) {
        this.setTranscriptStatus('Transcript storage is disabled on this server.');
        if (this.transcriptConsent) {
          this.transcriptConsent.checked = false;
          this.transcriptConsent.disabled = true;
        }
      } else {
        this.setTranscriptStatus(
          'Server can store a redacted transcript if you consented before Connect.'
        );
      }
      if (!this.configureSent && this.pcClient) {
        this.configureSent = true;
        const consent = !!this.transcriptConsent?.checked;
        const command = createSessionConfigureCommand(consent);
        this.pcClient.sendClientMessage('session.configure', command);
        this.log(`Sent session.configure consent=${consent}`);
      }
      return;
    }

    const configureResult = parseSessionConfigureResult(data);
    if (configureResult) {
      if (!configureResult.ok) {
        this.setTranscriptStatus(
          configureResult.error?.message ||
            'Transcript configuration was rejected.'
        );
      } else if (configureResult.reason === 'server_disabled') {
        this.setTranscriptStatus('Transcript storage is disabled on this server.');
      } else if (configureResult.reason === 'declined') {
        this.setTranscriptStatus(
          'Transcript saving is off for this session. Content-free metrics may still be stored.'
        );
      } else if (configureResult.transcript_active) {
        this.setTranscriptStatus(
          'Redacted transcript saving is active for this session.'
        );
      }
      return;
    }

    const retrieval = parseKnowledgeRetrievalMessage(data);
    if (retrieval) {
      this.log(
        `Knowledge retrieval ${retrieval.status} sources=${retrieval.sources.length}`
      );
      this.renderKnowledgeSources(retrieval.sources, retrieval.status);
      return;
    }

    const conversation = parseConversationMessage(data);
    if (conversation) {
      if (conversation.type === 'conversation.snapshot') {
        this.conversationTracker.applySnapshot(conversation.entries);
        this.renderConversation();
      } else if (
        this.conversationTracker.applyEvent(conversation.kind, conversation.entry)
      ) {
        this.renderConversation();
      }
      return;
    }

    const parsed = parseServerMessage(data);
    if (!parsed) {
      this.log(`Ignored malformed server message: ${JSON.stringify(data)}`);
      return;
    }

    if (parsed.type === 'lesson.state') {
      if (this.stateTracker.applyState(parsed)) {
        this.applyLessonState(parsed);
        this.log(
          `State seq=${parsed.sequence} mode=${parsed.mode} slide=${parsed.slide.number}`
        );
      } else {
        this.log(`Ignored stale state sequence ${parsed.sequence}`);
      }
      return;
    }

    this.handleCommandResult(parsed);
  }

  private renderKnowledgeSources(
    sources: KnowledgeRetrievalSource[],
    _status: string
  ): void {
    if (!this.sourcesCard || !this.knowledgeSourcesEl) return;
    // Empty retrieval is normal (curriculum / lesson context). Hide the section.
    if (!sources.length) {
      this.sourcesCard.hidden = true;
      this.knowledgeSourcesEl.replaceChildren();
      return;
    }
    this.sourcesCard.hidden = false;
    this.knowledgeSourcesEl.replaceChildren();
    for (const source of sources) {
      const item = document.createElement('li');
      item.className = 'sources-item';
      const label = document.createElement('span');
      label.className = 'sources-label';
      label.textContent = source.label;
      const name = document.createElement('span');
      name.className = 'sources-name';
      name.textContent = source.document_name;
      item.append(label, name);
      if (source.page != null) {
        const page = document.createElement('span');
        page.className = 'sources-page';
        page.textContent = `Page ${source.page}`;
        item.appendChild(page);
      }
      this.knowledgeSourcesEl.appendChild(item);
    }
  }

  private setKnowledgeStatus(message: string): void {
    if (this.knowledgeStatusEl) {
      this.knowledgeStatusEl.textContent = message;
    }
  }

  private setKnowledgeError(message: string): void {
    if (this.knowledgeErrorEl) {
      this.knowledgeErrorEl.textContent = message;
    }
  }

  private async refreshKnowledgeStatus(): Promise<void> {
    try {
      const response = await fetch(`${BOT_API_URL}/knowledge/status`);
      if (!response.ok) {
        this.setKnowledgeStatus('Knowledge status unavailable');
        return;
      }
      const parsed = parseKnowledgeStatusResponse(await response.json());
      if (!parsed) {
        this.setKnowledgeStatus('Knowledge status unavailable');
        return;
      }
      this.maxUploadBytes = parsed.limits.max_upload_bytes;
      if (this.knowledgeCountsEl) {
        this.knowledgeCountsEl.textContent = `${parsed.document_count} document(s), ${parsed.chunk_count} chunk(s)`;
      }
      this.setKnowledgeStatus(
        parsed.upload_enabled
          ? 'Ready for upload (in-memory until server restart)'
          : 'Upload disabled'
      );
    } catch {
      this.setKnowledgeStatus('Knowledge status unavailable');
    }
  }

  private async uploadKnowledge(): Promise<void> {
    if (this.knowledgeUploading) return;
    const file = this.knowledgeFile?.files?.[0];
    if (!file) {
      this.setKnowledgeError('Choose a .txt, .md, or .pdf file first.');
      return;
    }
    const validation = validateKnowledgeFileClient(
      { name: file.name, size: file.size },
      this.maxUploadBytes
    );
    if (!validation.ok) {
      this.setKnowledgeError(validation.message);
      return;
    }

    this.knowledgeUploading = true;
    if (this.knowledgeUploadBtn) this.knowledgeUploadBtn.disabled = true;
    this.setKnowledgeError('');
    this.setKnowledgeStatus('Uploading…');
    try {
      const body = new FormData();
      body.append('file', file);
      const response = await fetch(`${BOT_API_URL}/knowledge/documents`, {
        method: 'POST',
        body,
      });
      const raw = await response.json().catch(() => null);
      if (!response.ok) {
        const message =
          raw &&
          typeof raw === 'object' &&
          raw !== null &&
          'error' in raw &&
          typeof (raw as { error?: { message?: string } }).error?.message ===
            'string'
            ? (raw as { error: { message: string } }).error.message
            : 'Upload failed';
        this.setKnowledgeError(message);
        this.setKnowledgeStatus('Upload failed');
        return;
      }
      const parsed = parseKnowledgeUploadResponse(raw);
      if (!parsed) {
        this.setKnowledgeError('Unexpected upload response');
        this.setKnowledgeStatus('Upload failed');
        return;
      }
      this.setKnowledgeStatus(
        parsed.duplicate
          ? `Duplicate document reused (${parsed.chunk_count} chunks)`
          : `Uploaded ${parsed.name} (${parsed.chunk_count} chunks)`
      );
      await this.refreshKnowledgeStatus();
    } catch (error) {
      this.setKnowledgeError((error as Error).message || 'Upload failed');
      this.setKnowledgeStatus('Upload failed');
    } finally {
      this.knowledgeUploading = false;
      if (this.knowledgeUploadBtn) this.knowledgeUploadBtn.disabled = false;
    }
  }

  private handleCommandResult(result: LessonCommandResultMessage): void {
    if (this.pendingRequestId && result.request_id === this.pendingRequestId) {
      this.commandPending = false;
      this.pendingRequestId = null;
      this.applyControlAvailability(this.stateTracker.state);
    }
    if (!result.ok) {
      const message = result.error?.message || 'Command failed';
      this.setError(message);
      this.log(`Command error: ${result.error?.code || 'ERROR'} — ${message}`);
    } else {
      this.clearError();
      this.log(`Command ok: ${result.request_id}`);
    }
  }

  private async sendCommand(command: LessonCommandMessage): Promise<void> {
    if (!this.pcClient || !this.connected || this.commandPending) {
      return;
    }
    this.clearError();
    this.commandPending = true;
    this.pendingRequestId = command.request_id;
    this.applyControlAvailability(this.stateTracker.state);
    try {
      this.pcClient.sendClientMessage('lesson.command', command);
      this.log(`Sent command ${command.command} (${command.request_id})`);
    } catch (error) {
      this.commandPending = false;
      this.pendingRequestId = null;
      this.applyControlAvailability(this.stateTracker.state);
      this.setError((error as Error).message);
      this.log(`Failed to send command: ${(error as Error).message}`);
    }
  }

  setupMediaTracks() {
    if (!this.pcClient) return;
    const tracks = this.pcClient.tracks();
    if (tracks.bot?.audio) {
      this.setupAudioTrack(tracks.bot.audio);
    }
  }

  setupTrackListeners() {
    if (!this.pcClient) return;

    this.pcClient.on(RTVIEvent.TrackStarted, (track, participant) => {
      if (!participant?.local && track.kind === 'audio') {
        this.setupAudioTrack(track);
      }
    });

    this.pcClient.on(RTVIEvent.TrackStopped, (track, participant) => {
      this.log(
        `Track stopped: ${track.kind} from ${participant?.name || 'unknown'}`
      );
    });
    // Server messages are handled only via callbacks.onServerMessage to avoid
    // duplicate State / Command-ok processing for a single outbound payload.
  }

  private setupAudioTrack(track: MediaStreamTrack): void {
    this.log('Setting up audio track');
    if (
      this.botAudio.srcObject &&
      'getAudioTracks' in this.botAudio.srcObject
    ) {
      const oldTrack = this.botAudio.srcObject.getAudioTracks()[0];
      if (oldTrack?.id === track.id) return;
    }
    this.botAudio.srcObject = new MediaStream([track]);
  }

  public async connect(): Promise<void> {
    try {
      const startTime = Date.now();
      this.clearError();
      this.stateTracker.reset();
      this.conversationTracker.reset();
      this.followConversationLive = true;
      this.hideJumpLatest();
      this.renderConversation();
      this.configureSent = false;
      this.showPreparingDisplay();
      this.updateStatus('Connecting');
      if (this.transcriptConsent) {
        this.transcriptConsent.disabled = true;
      }

      const PipecatConfig: PipecatClientOptions = {
        transport: new WebSocketTransport(),
        enableMic: true,
        enableCam: false,
        callbacks: {
          onConnected: () => {
            this.connected = true;
            this.sessionConsentLocked = true;
            if (this.transcriptConsent) {
              this.transcriptConsent.disabled = true;
            }
            this.updateTranscriptStorageLabel();
            this.updateStatus('Connected');
            this.applyControlAvailability(this.stateTracker.state);
          },
          onDisconnected: () => {
            this.connected = false;
            this.commandPending = false;
            this.pendingRequestId = null;
            this.configureSent = false;
            this.sessionConsentLocked = false;
            this.stateTracker.reset();
            this.showWelcomeDisplay();
            this.updateStatus('Disconnected');
            this.applyControlAvailability(null);
            if (this.transcriptConsent) {
              this.transcriptConsent.disabled = false;
            }
            this.updateTranscriptStorageLabel();
            this.log('Client disconnected');
          },
          onBotReady: (data) => {
            this.log(`Bot ready: ${JSON.stringify(data)}`);
            this.setupMediaTracks();
            void this.sendCommand(createGetStateCommand());
          },
          onUserTranscript: (data) => {
            if (data.final) {
              this.log(`User: ${data.text}`);
            }
          },
          onBotTranscript: (data) => this.log(`Bot: ${data.text}`),
          onServerMessage: (data) => this.handleServerPayload(data),
          onMessageError: (error) => console.error('Message error:', error),
          onError: (error) => console.error('Error:', error),
        },
      };
      this.pcClient = new PipecatClient(PipecatConfig);
      // @ts-ignore
      window.pcClient = this.pcClient;
      this.setupTrackListeners();

      this.log('Initializing devices...');
      await this.pcClient.initDevices();

      this.log(`Connecting to bot via ${BOT_API_URL}/connect ...`);
      await this.pcClient.startBotAndConnect({
        endpoint: `${BOT_API_URL}/connect`,
      });

      const timeTaken = Date.now() - startTime;
      this.log(`Connection complete, timeTaken: ${timeTaken}`);
    } catch (error) {
      this.log(`Error connecting: ${(error as Error).message}`);
      this.updateStatus('Error');
      this.connected = false;
      this.showWelcomeDisplay();
      this.applyControlAvailability(null);
      if (this.transcriptConsent) {
        this.transcriptConsent.disabled = false;
      }
      this.updateTranscriptStorageLabel();
      if (this.pcClient) {
        try {
          await this.pcClient.disconnect();
        } catch (disconnectError) {
          this.log(`Error during disconnect: ${disconnectError}`);
        }
      }
    }
  }

  public async disconnect(): Promise<void> {
    if (this.pcClient) {
      try {
        await this.pcClient.disconnect();
        this.pcClient = null;
        this.connected = false;
        this.commandPending = false;
        this.pendingRequestId = null;
        this.stateTracker.reset();
        this.showWelcomeDisplay();
        this.applyControlAvailability(null);
        if (
          this.botAudio.srcObject &&
          'getAudioTracks' in this.botAudio.srcObject
        ) {
          this.botAudio.srcObject
            .getAudioTracks()
            .forEach((track) => track.stop());
          this.botAudio.srcObject = null;
        }
      } catch (error) {
        this.log(`Error disconnecting: ${(error as Error).message}`);
      }
    }
  }
}

declare global {
  interface Window {
    WebsocketClientApp: typeof WebsocketClientApp;
  }
}

window.addEventListener('DOMContentLoaded', () => {
  window.WebsocketClientApp = WebsocketClientApp;
  new WebsocketClientApp();
});
