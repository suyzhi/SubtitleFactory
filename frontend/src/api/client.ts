// 字幕工厂 - 底层 HTTP 会话、桌面原生文件交付与错误处理

import { invoke } from '@tauri-apps/api/core';
import { openUrl } from '@tauri-apps/plugin-opener';

let BASE_URL =
  import.meta.env.VITE_API_BASE_URL === 'same-origin'
    ? ''
    : import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';
let API_TOKEN = '';
let sessionInitialization: Promise<void> | null = null;

export type DistributionChannel = 'direct' | 'app_store';

let DISTRIBUTION_CHANNEL: DistributionChannel =
  import.meta.env.VITE_DISTRIBUTION_CHANNEL === 'app_store' ? 'app_store' : 'direct';
let YOUTUBE_ENABLED = DISTRIBUTION_CHANNEL !== 'app_store';
let FILESYSTEM_AUTOMATION_ENABLED = DISTRIBUTION_CHANNEL !== 'app_store';
let EXTERNAL_RUNTIME_PATHS_ENABLED = DISTRIBUTION_CHANNEL !== 'app_store';

export interface BackendErrorPayload {
  code: string;
  message: string;
  suggestion?: string;
  details?: Record<string, unknown>;
  recoverable?: boolean;
}

export class BackendError extends Error {
  code: string;
  suggestion: string;
  details: Record<string, unknown>;
  recoverable: boolean;
  status: number;

  constructor(status: number, payload: BackendErrorPayload) {
    super(payload.message);
    this.name = 'BackendError';
    this.status = status;
    this.code = payload.code;
    this.suggestion = payload.suggestion || '';
    this.details = payload.details || {};
    this.recoverable = payload.recoverable ?? status < 500;
  }
}

export interface PreparedFile {
  path: string;
  filename: string;
  size?: number;
}

export function getBaseUrl(): string {
  return BASE_URL;
}

export function isTauriDesktop(): boolean {
  return typeof window !== 'undefined' && Boolean((window as any).__TAURI_INTERNALS__);
}

export type PublicLink = 'privacy';
const PUBLIC_LINKS: Record<PublicLink, string> = {
  privacy: 'https://github.com/suyzhi/SubtitleFactory/blob/main/docs/PRIVACY.md',
};

export async function openPublicLink(link: PublicLink): Promise<void> {
  const url = PUBLIC_LINKS[link];
  if (isTauriDesktop()) {
    await openUrl(url);
    return;
  }
  const opened = window.open(url, '_blank', 'noopener,noreferrer');
  if (!opened) throw new Error('浏览器阻止了新窗口，请允许后重试');
}

function nativeError(error: unknown): Error {
  if (error instanceof Error) return error;
  return new Error(typeof error === 'string' ? error : '系统文件操作失败');
}

export async function saveManagedFile(sourcePath: string, suggestedName: string): Promise<boolean> {
  if (!isTauriDesktop()) return false;
  try {
    const destination = await invoke<string | null>('save_managed_file', {
      sourcePath,
      suggestedName,
    });
    return destination !== null;
  } catch (error) {
    throw nativeError(error);
  }
}

export async function saveTextFile(suggestedName: string, contents: string): Promise<boolean> {
  if (!isTauriDesktop()) {
    const blob = new Blob([contents], { type: 'text/plain;charset=utf-8' });
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = suggestedName;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    return true;
  }
  try {
    const destination = await invoke<string | null>('save_text_file', {
      suggestedName,
      contents,
    });
    return destination !== null;
  } catch (error) {
    throw nativeError(error);
  }
}

export async function restartDesktopApp(): Promise<boolean> {
  if (!isTauriDesktop()) return false;
  await invoke('restart_app');
  return true;
}

export async function revealLocalPath(path: string): Promise<void> {
  if ((window as any).__TAURI_INTERNALS__) await invoke('reveal_path', { path });
}

export function getDistributionChannel(): DistributionChannel {
  return DISTRIBUTION_CHANNEL;
}

export function youtubeFeaturesEnabled(): boolean {
  return YOUTUBE_ENABLED;
}

export function filesystemAutomationEnabled(): boolean {
  return FILESYSTEM_AUTOMATION_ENABLED;
}

export function externalRuntimePathsEnabled(): boolean {
  return EXTERNAL_RUNTIME_PATHS_ENABLED;
}

export function initializeBackendSession(): Promise<void> {
  if (sessionInitialization) return sessionInitialization;
  sessionInitialization = (async () => {
    if (!(window as any).__TAURI_INTERNALS__) {
      // 浏览器直接访问本地后端，无需会话令牌
      return;
    }
    const session = await invoke<{
      baseUrl: string;
      token: string;
      distributionChannel: DistributionChannel;
      youtubeEnabled: boolean;
      filesystemAutomationEnabled: boolean;
      externalRuntimePathsEnabled: boolean;
    }>('backend_session');
    BASE_URL = session.baseUrl;
    API_TOKEN = session.token;
    DISTRIBUTION_CHANNEL = session.distributionChannel;
    YOUTUBE_ENABLED = session.youtubeEnabled;
    FILESYSTEM_AUTOMATION_ENABLED = session.filesystemAutomationEnabled;
    EXTERNAL_RUNTIME_PATHS_ENABLED = session.externalRuntimePathsEnabled;
  })();
  return sessionInitialization;
}

function requestHeaders(options?: RequestInit, json = true): Headers {
  const headers = new Headers(options?.headers);
  if (API_TOKEN) headers.set('Authorization', `Bearer ${API_TOKEN}`);
  if (json && options?.body != null && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return headers;
}

export async function parseError(res: Response): Promise<BackendError> {
  let payload: any = null;
  try {
    payload = await res.json();
  } catch {
    /* handled below */
  }
  const error = payload?.error || (typeof payload?.detail === 'object' ? payload.detail : null);
  if (error) {
    const { code, message, suggestion, recoverable, details, ...extraDetails } = error;
    return new BackendError(res.status, {
      code,
      message,
      suggestion,
      recoverable,
      details: { ...extraDetails, ...(details || {}) },
    });
  }
  return new BackendError(res.status, {
    code: `HTTP_${res.status}`,
    message: typeof payload?.detail === 'string' ? payload.detail : `请求失败 (${res.status})`,
    details: {},
    recoverable: res.status < 500,
  });
}

export async function backendFetch(
  url: string,
  options?: RequestInit,
  json = true,
): Promise<Response> {
  await initializeBackendSession();
  const method = String(options?.method || 'GET').toUpperCase();
  return fetch(`${BASE_URL}${url}`, {
    ...options,
    // Project state and task progress are dynamic local API responses. WebKit
    // may otherwise reuse the first successful GET while a background task is
    // advancing, leaving the UI permanently stuck at its initial progress.
    ...(method === 'GET' ? { cache: 'no-store' as const } : {}),
    headers: requestHeaders(options, json),
  });
}

export async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await backendFetch(url, options);
  if (!res.ok) {
    throw await parseError(res);
  }
  return res.json();
}

export async function downloadBackendFile(
  endpoint: string,
  suggestedName: string,
  sourcePath?: string,
  options?: RequestInit,
): Promise<boolean> {
  if (isTauriDesktop()) {
    if (!sourcePath) {
      throw new BackendError(500, {
        code: 'MANAGED_EXPORT_PATH_MISSING',
        message: '交付文件路径缺失，请重新生成后再保存',
        details: {},
        recoverable: true,
      });
    }
    return saveManagedFile(sourcePath, suggestedName);
  }
  const response = await backendFetch(endpoint, options);
  if (!response.ok) throw await parseError(response);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const disposition = response.headers.get('Content-Disposition') || '';
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  try {
    anchor.download = suggestedName || (encoded ? decodeURIComponent(encoded) : plain || 'export');
  } catch {
    anchor.download = suggestedName;
  }
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  return true;
}

export function getBackendMediaUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  if (/^(?:https?:|data:|blob:)/i.test(value)) return value;
  return `${BASE_URL}${value.startsWith('/') ? '' : '/'}${value}`;
}
