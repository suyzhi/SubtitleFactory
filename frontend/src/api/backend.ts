// 字幕工厂（简洁版）- 本地后端 API 客户端

import { invoke } from '@tauri-apps/api/core';
import type {
  AIProviderCard, AIProvidersResponse, ExportFormat, Project, SubtitleSegment,
  TaskStatus, TranscriptionModel,
} from '../types';

let BASE_URL = import.meta.env.VITE_API_BASE_URL === 'same-origin' ? '' : import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';
let API_TOKEN = '';
let YOUTUBE_ENABLED = import.meta.env.VITE_DISTRIBUTION_CHANNEL !== 'app_store';
let sessionInitialization: Promise<void> | null = null;

export class BackendError extends Error {
  code: string;
  suggestion: string;
  status: number;

  constructor(status: number, code: string, message: string, suggestion = '') {
    super(message);
    this.name = 'BackendError';
    this.status = status;
    this.code = code;
    this.suggestion = suggestion;
  }
}

function isTauriDesktop(): boolean {
  return typeof window !== 'undefined' && Boolean((window as any).__TAURI_INTERNALS__);
}

export function youtubeFeaturesEnabled(): boolean {
  return YOUTUBE_ENABLED;
}

export function initializeBackendSession(): Promise<void> {
  if (sessionInitialization) return sessionInitialization;
  sessionInitialization = (async () => {
    // 浏览器开发模式直接访问本地后端，无需会话令牌
    if (!isTauriDesktop()) return;
    const session = await invoke<{ baseUrl: string; token: string; youtubeEnabled: boolean }>('backend_session');
    BASE_URL = session.baseUrl;
    API_TOKEN = session.token;
    YOUTUBE_ENABLED = session.youtubeEnabled;
  })();
  return sessionInitialization;
}

async function parseError(res: Response): Promise<BackendError> {
  let payload: any = null;
  try { payload = await res.json(); } catch { /* handled below */ }
  const error = payload?.error || (typeof payload?.detail === 'object' ? payload.detail : null);
  if (error) return new BackendError(res.status, error.code || `HTTP_${res.status}`, error.message || '请求失败', error.suggestion || '');
  const message = typeof payload?.detail === 'string' ? payload.detail : `请求失败 (${res.status})`;
  return new BackendError(res.status, `HTTP_${res.status}`, message);
}

async function backendFetch(url: string, options: RequestInit = {}): Promise<Response> {
  await initializeBackendSession();
  const headers = new Headers(options.headers);
  if (API_TOKEN) headers.set('Authorization', `Bearer ${API_TOKEN}`);
  if (typeof options.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const method = String(options.method || 'GET').toUpperCase();
  // 任务进度是动态数据；WebKit 可能缓存首个 GET 结果，导致进度停住。
  return fetch(`${BASE_URL}${url}`, { ...options, ...(method === 'GET' ? { cache: 'no-store' as const } : {}), headers });
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await backendFetch(url, options);
  if (!res.ok) throw await parseError(res);
  return res.json();
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

// ── 健康检查 ──

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await backendFetch('/api/health');
    return res.ok;
  } catch {
    return false;
  }
}

// ── 项目 ──

export async function listProjects(): Promise<Project[]> {
  return (await request<{ projects: Project[] }>('/api/projects?page_size=200')).projects;
}

export function getProject(projectId: string): Promise<Project> {
  return request(`/api/projects/${projectId}`);
}

export function createProject(data: {
  source_type: 'youtube' | 'local'; source_url?: string; title: string; language: string; target_language: string;
}): Promise<{ project_id: string }> {
  return request('/api/projects', json('POST', data));
}

export function deleteProject(projectId: string): Promise<unknown> {
  return request(`/api/projects/${projectId}?permanent=true`, { method: 'DELETE' });
}

export function uploadLocalVideo(projectId: string, file: File, onProgress?: (percent: number) => void): Promise<void> {
  return initializeBackendSession().then(() => new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${BASE_URL}/api/projects/${projectId}/import-local`);
    if (API_TOKEN) xhr.setRequestHeader('Authorization', `Bearer ${API_TOKEN}`);
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) onProgress?.(Math.round(event.loaded / event.total * 100));
    };
    xhr.onerror = () => reject(new Error('视频导入失败，请检查本地服务是否在运行'));
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`视频导入失败 (${xhr.status})`)));
    xhr.send(form);
  }));
}

// ── 处理流程 ──

export interface WorkflowOptions {
  model: string;
  language: string;
  runtime?: string;
  source_url?: string;
  enable_clean: boolean;
  enable_translate: boolean;
  target_language: string;
}

export function startWorkflow(projectId: string, options: WorkflowOptions): Promise<{ task_id: string }> {
  return request(`/api/projects/${projectId}/workflow`, json('POST', {
    ...options,
    text_processing_consent: options.enable_clean || options.enable_translate,
  }));
}

async function postForm(url: string, fields: Record<string, string>): Promise<{ task_id: string }> {
  const form = new FormData();
  Object.entries(fields).forEach(([key, value]) => form.append(key, value));
  const res = await backendFetch(url, { method: 'POST', body: form });
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export function startClean(projectId: string): Promise<{ task_id: string }> {
  return postForm(`/api/projects/${projectId}/clean`, { target_length: '42' });
}

export function startTranslate(projectId: string, targetLanguage: string): Promise<{ task_id: string }> {
  return postForm(`/api/projects/${projectId}/translate`, { target_language: targetLanguage });
}

// ── 任务 ──

export function getTask(taskId: string): Promise<TaskStatus> {
  return request(`/api/tasks/${taskId}`);
}

export async function getLatestProjectTask(projectId: string): Promise<TaskStatus | null> {
  return (await request<{ task: TaskStatus | null }>(`/api/projects/${projectId}/tasks/latest`)).task;
}

export function cancelTask(taskId: string): Promise<TaskStatus> {
  return request(`/api/tasks/${taskId}/cancel`, { method: 'POST' });
}

// ── 字幕 ──

export async function getSegments(projectId: string): Promise<SubtitleSegment[]> {
  return (await request<{ segments: SubtitleSegment[] }>(`/api/projects/${projectId}/segments`)).segments;
}

export async function exportSubtitles(projectId: string, format: ExportFormat, suggestedName: string): Promise<boolean> {
  const { fmt, bilingual, primary } = {
    original: { fmt: 'srt', bilingual: false, primary: 'original' },
    translated: { fmt: 'srt', bilingual: false, primary: 'translated' },
    bilingual: { fmt: 'srt-bilingual', bilingual: true, primary: 'translated' },
  }[format];
  const exported = await request<{ path: string }>(`/api/projects/${projectId}/export`, json('POST', {
    format: fmt, bilingual, primary_language: primary,
  }));
  if (isTauriDesktop()) {
    const destination = await invoke<string | null>('save_managed_file', { sourcePath: exported.path, suggestedName });
    return destination !== null;
  }
  const res = await backendFetch(`/api/projects/${projectId}/export/download?fmt=${fmt}`);
  if (!res.ok) throw await parseError(res);
  const objectUrl = URL.createObjectURL(await res.blob());
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = suggestedName;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  return true;
}

// ── 转写模型 ──

export async function getTranscriptionModels(language: string): Promise<{ recommended_model: string; models: TranscriptionModel[] }> {
  return request(`/api/transcription/models?language=${encodeURIComponent(language)}`);
}

export function prepareTranscriptionModel(modelId: string, runtime: string): Promise<{ task_id: string }> {
  return request(`/api/transcription/models/${encodeURIComponent(modelId)}/prepare`, json('POST', { runtime, repair: false }));
}

// ── AI 服务 ──

export function getAIProviders(): Promise<AIProvidersResponse> {
  return request('/api/settings/ai/providers');
}

export async function saveAIProvider(providerId: string, data: Pick<AIProviderCard, 'base_url' | 'model' | 'thinking_enabled'> & { api_key?: string }): Promise<AIProviderCard> {
  return (await request<{ provider: AIProviderCard }>(`/api/settings/ai/providers/${providerId}`, json('PUT', { ...data, enabled: true }))).provider;
}

export function assignAIProvider(providerId: string): Promise<unknown> {
  return request('/api/settings/ai/assignments', json('PUT', {
    clean_provider_id: providerId, translate_provider_id: providerId,
  }));
}

export function testAIProvider(providerId: string): Promise<{ ok: boolean; latency_ms: number }> {
  return request(`/api/settings/ai/providers/${providerId}/test`, { method: 'POST' });
}
