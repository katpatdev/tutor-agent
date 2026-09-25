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
  humanModeLabel,
  LessonStateTracker,
  parseServerMessage,
  type LessonCommandMessage,
  type LessonCommandResultMessage,
  type LessonStateMessage,
} from './lessonProtocol';

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
  private errorBox: HTMLElement | null = null;
  private debugLog: HTMLElement | null = null;
  private botAudio: HTMLAudioElement;
  private stateTracker = new LessonStateTracker();
  private connected = false;
  private commandPending = false;
  private pendingRequestId: string | null = null;

  constructor() {
    this.botAudio = document.createElement('audio');
    this.botAudio.autoplay = true;
    document.body.appendChild(this.botAudio);

    this.setupDOMElements();
    this.setupEventListeners();
    this.applyControlAvailability(null);
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
    this.errorBox = document.getElementById('command-error');
    this.debugLog = document.getElementById('debug-log');
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

  private handleServerPayload(data: unknown): void {
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
            this.updateStatus('Disconnected');
            this.applyControlAvailability(null);
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
