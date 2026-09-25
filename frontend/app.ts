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
  formatNarrationStatus,
  humanModeLabel,
  LessonStateTracker,
  parseServerMessage,
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
  private transcriptConsent: HTMLInputElement | null = null;
  private transcriptStatusEl: HTMLElement | null = null;
  private botAudio: HTMLAudioElement;
  private stateTracker = new LessonStateTracker();
  private connected = false;
  private commandPending = false;
  private pendingRequestId: string | null = null;
  private knowledgeUploading = false;
  private maxUploadBytes = 5_242_880;
  private configureSent = false;

  constructor() {
    this.botAudio = document.createElement('audio');
    this.botAudio.autoplay = true;
    document.body.appendChild(this.botAudio);

    this.setupDOMElements();
    this.setupEventListeners();
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
    this.transcriptConsent = document.getElementById(
      'transcript-consent'
    ) as HTMLInputElement;
    this.transcriptStatusEl = document.getElementById('transcript-status');
    if (this.transcriptConsent) {
      this.transcriptConsent.checked = false;
      this.transcriptConsent.disabled = false;
    }
    this.setTranscriptStatus(
      'Transcript saving is optional and off by default. Metrics-only summaries may still be stored without conversation text.'
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
  }

  private log(message: string): void {
    if (!this.debugLog) return;
    const entry = document.createElement('div');
    entry.textContent = `${new Date().toISOString()} - ${message}`;
    if (message.startsWith('User: ')) {
      entry.style.color = '#1d4ed8';
    } else if (message.startsWith('Bot: ')) {
      entry.style.color = '#0b6e4f';
    }
    this.debugLog.appendChild(entry);
    this.debugLog.scrollTop = this.debugLog.scrollHeight;
    console.log(message);
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
    if (this.statusSpan) {
      this.statusSpan.textContent = status;
    }
    this.log(`Status: ${status}`);
  }

  private applyLessonState(state: LessonStateMessage): void {
    if (this.modeSpan) {
      this.modeSpan.textContent = humanModeLabel(state.mode);
    }
    if (this.slideSpan) {
      this.slideSpan.textContent = `Slide ${state.slide.number} of ${state.slide.total}`;
    }
    if (this.titleSpan) {
      this.titleSpan.textContent = state.slide.title;
    }
    if (this.slideSelect) {
      this.slideSelect.value = String(state.slide.number);
    }
    if (this.safetyNotice) {
      this.safetyNotice.textContent = state.safety_notice || '';
    }
    if (this.narrationStatusEl) {
      this.narrationStatusEl.textContent = formatNarrationStatus(state.narration);
    }
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
      this.renderKnowledgeSources(retrieval.sources, retrieval.status);
      this.log(
        `Knowledge retrieval ${retrieval.status} sources=${retrieval.sources.length}`
      );
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
    status: string
  ): void {
    if (!this.knowledgeSourcesEl) return;
    if (!sources.length) {
      this.knowledgeSourcesEl.textContent =
        status === 'no_match'
          ? 'No matching uploaded sources for the latest answer.'
          : 'No sources listed for the latest answer.';
      return;
    }
    this.knowledgeSourcesEl.textContent = sources
      .map((s) =>
        s.page != null
          ? `${s.label}: ${s.document_name} (page ${s.page})`
          : `${s.label}: ${s.document_name}`
      )
      .join(' · ');
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

    this.pcClient.on(RTVIEvent.ServerMessage, (data) => {
      this.handleServerPayload(data);
    });
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
      this.configureSent = false;
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
            this.updateStatus('Connected');
            this.applyControlAvailability(this.stateTracker.state);
          },
          onDisconnected: () => {
            this.connected = false;
            this.commandPending = false;
            this.pendingRequestId = null;
            this.configureSent = false;
            this.updateStatus('Disconnected');
            this.applyControlAvailability(null);
            if (this.transcriptConsent) {
              this.transcriptConsent.disabled = false;
            }
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
      this.applyControlAvailability(null);
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
