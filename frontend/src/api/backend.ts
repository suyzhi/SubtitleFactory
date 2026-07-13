// 字幕工厂 - Backend API Client

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

import type {
  Project, SubtitleSegment, TaskStatus,
  ProjectCreate, SegmentUpdate, ExportRequest, AIProviderPreset, AISettings,
  AppSettings, AppSettingsResponse, HealthStatus, PathValidationResult
} from '../types';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API Error ${res.status}: ${err.slice(0, 200)}`);
  }
  return res.json();
}

// ── Projects ──

export async function listProjects(options?: { deleted?: boolean }): Promise<{ projects: Project[] }> {
  return request(options?.deleted ? '/api/projects?deleted=true' : '/api/projects');
}

export async function createProject(data: ProjectCreate): Promise<{ project_id: string; message: string }> {
  return request('/api/projects', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getProject(projectId: string): Promise<Project> {
  return request(`/api/projects/${projectId}`);
}

export async function updateProjectGroup(projectId: string, groupName: string | null): Promise<Project> {
  return request(`/api/projects/${projectId}/group`, {
    method: 'PATCH',
    body: JSON.stringify({ group_name: groupName }),
  });
}

export async function renameProject(projectId: string, title: string): Promise<Project> {
  return request(`/api/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });
}

export async function trashProject(projectId: string, terminate = false): Promise<{ project?: Project; message?: string }> {
  return request(`/api/projects/${projectId}/trash${terminate ? '?terminate=true' : ''}`, { method: 'POST' });
}

export async function restoreProject(projectId: string): Promise<{ project?: Project; message?: string }> {
  return request(`/api/projects/${projectId}/restore`, { method: 'POST' });
}

export async function permanentlyDeleteProject(projectId: string): Promise<{ message?: string }> {
  return request(`/api/projects/${projectId}?permanent=true`, { method: 'DELETE' });
}

export async function emptyTrash(): Promise<{ message?: string; deleted_count?: number }> {
  return request('/api/projects/trash?confirm=true', { method: 'DELETE' });
}

// ── Download / Import ──

export async function startDownload(projectId: string, url: string): Promise<{ task_id: string; message: string }> {
  const form = new FormData();
  form.append('url', url);
  const res = await fetch(`${BASE_URL}/api/projects/${projectId}/download`, { method: 'POST', body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function importLocalVideo(
  projectId: string, file: File, options?: {
    autostart?: boolean; model?: string; language?: string; runtime?:string; onProgress?: (percent: number) => void;
  }
): Promise<{ message: string; video_path: string; task_id?: string }> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file);
    form.append('autostart', String(options?.autostart ?? false));
    form.append('model', options?.model || 'auto');
    form.append('language', options?.language || 'auto');
    if(options?.runtime) form.append('runtime',options.runtime);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${BASE_URL}/api/projects/${projectId}/import-local`);
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) options?.onProgress?.(Math.round(event.loaded / event.total * 100));
    };
    xhr.onerror = () => reject(new Error('视频上传失败，请检查本地后端连接'));
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) return reject(new Error(xhr.responseText || `导入失败 (${xhr.status})`));
      try { resolve(JSON.parse(xhr.responseText)); } catch { reject(new Error('导入响应格式无效')); }
    };
    xhr.send(form);
  });
}

// ── Audio Extract ──

export async function startExtractAudio(projectId: string): Promise<{ task_id: string; message: string }> {
  return request(`/api/projects/${projectId}/extract-audio`, { method: 'POST' });
}

// ── Transcribe ──

export async function startTranscribe(projectId: string, language: string = 'auto', model: string = 'small', runtime?: string): Promise<{ task_id: string; message: string }> {
  const form = new FormData();
  form.append('language', language);
  form.append('model', model);
  if (runtime) form.append('runtime', runtime);
  const res = await fetch(`${BASE_URL}/api/projects/${projectId}/transcribe`, { method: 'POST', body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function startWorkflow(
  projectId: string, data: { model: string; language: string; runtime?:string; source_url?: string }
): Promise<{ task_id: string; message: string; model: string }> {
  return request(`/api/projects/${projectId}/workflow`, { method: 'POST', body: JSON.stringify(data) });
}

export async function retryTranscription(
  projectId: string, data: { model: string; language: string; runtime?:string }
): Promise<{ task_id: string; message: string; model: string }> {
  return request(`/api/projects/${projectId}/transcribe/retry`, { method: 'POST', body: JSON.stringify(data) });
}

export interface TranscriptionModelStatus {
  id: string; name: string; ready: boolean; download_required: boolean;
  download_bytes?: number; runtime_error?: string | null; languages: string[];
  source?: string; status?: string; path?: string | null;
  runtimes?: { id: string; name: string; engine?:string; available:boolean; reason?:string }[];
  selected_runtime?:string|null; format?:string; version?:string;
}

export async function updateProjectTargetLanguage(projectId: string, target_language: string): Promise<Project> {
  return request(`/api/projects/${projectId}`, { method: 'PATCH', body: JSON.stringify({ target_language }) });
}

export async function getTranscriptionModels(projectId?: string, language = 'auto'): Promise<{
  recommended_model: string;
  audio?: { ok: boolean; error_code?: string; message?: string; duration?: number } | null;
  models: TranscriptionModelStatus[];
}> {
  const query = new URLSearchParams({ language });
  if (projectId) query.set('project_id', projectId);
  return request(`/api/transcription/models?${query.toString()}`);
}

export async function prepareTranscriptionModel(modelId: string, repair = false): Promise<{
  task_id: string; model_id: string; message: string;
}> {
  return request(`/api/transcription/models/${encodeURIComponent(modelId)}/prepare`, {
    method: 'POST', body: JSON.stringify({ repair }),
  });
}

export async function validateTranscriptionModel(modelId: string): Promise<TranscriptionModelStatus> {
  return request(`/api/transcription/models/${encodeURIComponent(modelId)}/validate`);
}

// ── AI Clean ──

export async function startClean(projectId: string, targetLength: number = 42): Promise<{ task_id: string; message: string }> {
  const form = new FormData();
  form.append('target_length', String(targetLength));
  const res = await fetch(`${BASE_URL}/api/projects/${projectId}/clean`, { method: 'POST', body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function undoClean(projectId: string): Promise<{ message: string; segments_count: number }> {
  return request(`/api/projects/${projectId}/clean/undo`, { method: 'POST' });
}

// ── AI Translate ──

export async function startTranslate(projectId: string, targetLanguage: string = 'zh'): Promise<{ task_id: string; message: string }> {
  const form = new FormData();
  form.append('target_language', targetLanguage);
  const res = await fetch(`${BASE_URL}/api/projects/${projectId}/translate`, { method: 'POST', body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── Segments ──

export async function getSegments(projectId: string): Promise<{ segments: SubtitleSegment[]; total: number }> {
  return request(`/api/projects/${projectId}/segments`);
}

export async function updateSegment(projectId: string, segmentIndex: number, data: SegmentUpdate): Promise<SubtitleSegment> {
  return request(`/api/projects/${projectId}/segments/${segmentIndex}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

// ── Export ──

export async function exportSubtitles(projectId: string, data: ExportRequest): Promise<{ path?: string; task_id?: string; message: string }> {
  return request(`/api/projects/${projectId}/export`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function getExportDownloadUrl(projectId: string, fmt: string): string {
  return `${BASE_URL}/api/projects/${projectId}/export/download?fmt=${fmt}`;
}

export function getVideoUrl(projectId: string): string {
  return `${BASE_URL}/api/projects/${projectId}/video`;
}

export function getProjectThumbnailUrl(project: Pick<Project, 'thumbnail_url'>): string | null {
  const value = project.thumbnail_url;
  if (!value) return null;
  if (/^(?:https?:|data:|blob:)/i.test(value)) return value;
  return `${BASE_URL}${value.startsWith('/') ? '' : '/'}${value}`;
}

// ── Tasks / Health ──

export async function getTaskStatus(taskId: string): Promise<TaskStatus> {
  const data: any = await request(`/api/tasks/${taskId}`);
  return {
    ...data,
    details: data.details || {},
    logs: data.logs || [],
    suggestion: data.suggestion || null,
    step_name: data.step_name || data.step || data.type || '',
  };
}

export async function getLatestProjectTask(projectId: string): Promise<TaskStatus | null> {
  const data = await request<{ task: TaskStatus | null }>(`/api/projects/${projectId}/tasks/latest`);
  return data.task;
}

export async function pauseTask(taskId: string): Promise<TaskStatus> {
  return request(`/api/tasks/${taskId}/pause`, { method: 'POST' });
}

export async function resumeTask(taskId: string): Promise<TaskStatus> {
  return request(`/api/tasks/${taskId}/resume`, { method: 'POST' });
}

export async function cancelTask(taskId: string): Promise<TaskStatus> {
  return request(`/api/tasks/${taskId}/cancel`, { method: 'POST' });
}

export async function getAISettings(): Promise<{ settings: AISettings; presets: AIProviderPreset[] }> {
  return request('/api/settings/ai');
}

export async function saveAISettings(settings: AISettings): Promise<{ settings: AISettings }> {
  return request('/api/settings/ai', { method: 'PUT', body: JSON.stringify(settings) });
}

export async function testAISettings(settings: AISettings): Promise<{ ok: boolean; latency_ms: number; settings: AISettings }> {
  return request('/api/settings/ai/test', { method: 'POST', body: JSON.stringify(settings) });
}

export interface AIProviderCard { provider_id:string; name:string; base_url:string; api_key:string; model:string; models:string[]; enabled:boolean; has_api_key:boolean; last_test_status?:string; last_latency_ms?:number; }
export const getAIProviders=()=>request<{providers:AIProviderCard[];assignments:{clean_provider_id:string;translate_provider_id:string}}>('/api/settings/ai/providers');
export const saveAIProvider=async(id:string,data:Partial<AIProviderCard>)=>(await request<{provider:AIProviderCard}>(`/api/settings/ai/providers/${id}`,{method:'PUT',body:JSON.stringify(data)})).provider;
export const testAIProvider=(id:string)=>request<{ok:boolean;latency_ms:number}>(`/api/settings/ai/providers/${id}/test`,{method:'POST'});
export const saveAIAssignments=(data:{clean_provider_id:string;translate_provider_id:string})=>request('/api/settings/ai/assignments',{method:'PUT',body:JSON.stringify(data)});

export interface ScannedModel { path:string;display_name:string;family:string;format:string;version:string;supported:boolean;reason?:string;cli_path?:string;runtimes?:string[]; }
export const scanLocalModels=(path:string)=>request<{models:ScannedModel[]}>(`/api/transcription/models/scan`,{method:'POST',body:JSON.stringify({root_path:path})});
export const importLocalModel=(path:string,cli_path?:string)=>request('/api/transcription/models/import',{method:'POST',body:JSON.stringify({path,cli_path})});

export async function getAppSettings(): Promise<AppSettingsResponse> {
  return request('/api/settings/app');
}

export async function saveAppSettings(settings: Partial<AppSettings>): Promise<AppSettingsResponse> {
  return request('/api/settings/app', { method: 'PUT', body: JSON.stringify(settings) });
}

export async function validateAppPath(data: {
  kind: PathValidationResult['kind']; path: string;
}): Promise<PathValidationResult> {
  return request('/api/settings/app/validate-path', { method: 'POST', body: JSON.stringify(data) });
}

export async function checkHealth(): Promise<HealthStatus> {
  const res = await fetch(`${BASE_URL}/api/health`, { method: 'GET' });
  if (!res.ok) throw new Error(`Backend not available: ${res.status}`);
  return res.json();
}

// ── Incremental Segments ──

export async function getSegmentsAfter(projectId: string, afterIdx: number): Promise<{
  segments: SubtitleSegment[];
  total: number;
  latest_idx: number;
  has_more: boolean;
}> {
  const res = await fetch(`${BASE_URL}/api/projects/${projectId}/segments?after_idx=${afterIdx}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
