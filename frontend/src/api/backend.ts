// 字幕工厂 - Backend API Client

import { invoke } from '@tauri-apps/api/core';

let BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';
let API_TOKEN = import.meta.env.VITE_API_TOKEN || '';
let sessionInitialization: Promise<void> | null = null;

import type {
  Project, SubtitleSegment, TaskStatus,
  ProjectCreate, SegmentUpdate, ExportRequest, AIProviderPreset, AISettings,
  AppSettings, AppSettingsResponse, HealthStatus, PathValidationResult,
  EditorOperationResponse, SegmentOperationRequest, QualityIssue,
  PlaylistPreview, PlaylistBatchDetail, PlaylistStageName
} from '../types';

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

export function initializeBackendSession(): Promise<void> {
  if (sessionInitialization) return sessionInitialization;
  sessionInitialization = (async () => {
    if (!(window as any).__TAURI_INTERNALS__) return;
    const session = await invoke<{ baseUrl: string; token: string }>('backend_session');
    BASE_URL = session.baseUrl;
    API_TOKEN = session.token;
  })();
  return sessionInitialization;
}

function authorizedHeaders(options?: RequestInit, json = true): Headers {
  const headers = new Headers(options?.headers);
  headers.set('Authorization', `Bearer ${API_TOKEN}`);
  if (json && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  return headers;
}

async function parseError(res: Response): Promise<BackendError> {
  let payload: any = null;
  try { payload = await res.json(); } catch { /* handled below */ }
  const error = payload?.error || (typeof payload?.detail === 'object' ? payload.detail : null);
  return new BackendError(res.status, error || {
    code: `HTTP_${res.status}`,
    message: typeof payload?.detail === 'string' ? payload.detail : `请求失败 (${res.status})`,
    details: {}, recoverable: res.status < 500,
  });
}

async function authorizedFetch(url: string, options?: RequestInit, json = true): Promise<Response> {
  await initializeBackendSession();
  return fetch(`${BASE_URL}${url}`, { ...options, headers: authorizedHeaders(options, json) });
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await authorizedFetch(url, options);
  if (!res.ok) {
    throw await parseError(res);
  }
  return res.json();
}

// ── Projects ──

export async function listProjects(options?: { deleted?: boolean; search?: string; sort?: string; page?: number; page_size?: number }): Promise<{ projects: Project[]; total?: number; pages?: number }> {
  const params = new URLSearchParams();
  if (options?.deleted) params.set('deleted', 'true');
  if (options?.search) params.set('search', options.search);
  if (options?.sort) params.set('sort', options.sort);
  if (options?.page) params.set('page', String(options.page));
  if (options?.page_size) params.set('page_size', String(options.page_size));
  return request(`/api/projects${params.size ? `?${params}` : ''}`);
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
  const res = await authorizedFetch(`/api/projects/${projectId}/download`, { method: 'POST', body: form }, false);
  if (!res.ok) throw await parseError(res);
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
    xhr.setRequestHeader('Authorization', `Bearer ${API_TOKEN}`);
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

export async function importSubtitleFile(
  projectId: string, file: File, expectedRevision: number,
): Promise<EditorOperationResponse> {
  const form = new FormData();
  form.append('file', file);
  form.append('expected_revision', String(expectedRevision));
  const response = await authorizedFetch(`/api/projects/${projectId}/subtitles/import`, {
    method: 'POST', body: form,
  }, false);
  if (!response.ok) throw await parseError(response);
  return response.json();
}

// ── Audio Extract ──

export async function startExtractAudio(projectId: string): Promise<{ task_id: string; message: string }> {
  return request(`/api/projects/${projectId}/extract-audio`, { method: 'POST' });
}

export interface MediaInfo {
  duration: number | null;
  audio_tracks: Array<{index: number; title: string; language: string; codec: string; channels: number; sample_rate: number; duration: number | null}>;
  selection: {audio_track_index: number; range_start: number | null; range_end: number | null};
}
export async function getMediaInfo(projectId: string): Promise<MediaInfo> { return request(`/api/projects/${projectId}/media-info`); }
export async function updateMediaSelection(projectId: string, selection: MediaInfo['selection']): Promise<void> {
  await request(`/api/projects/${projectId}/media-selection`, { method: 'PUT', body: JSON.stringify(selection) });
}
export async function getMediaTrackPreview(projectId: string, track: number, start = 0): Promise<string> {
  const response = await authorizedFetch(`/api/projects/${projectId}/media-track-preview?track=${track}&start=${start}`);
  if (!response.ok) throw await parseError(response);
  return URL.createObjectURL(await response.blob());
}

// ── Transcribe ──

export async function startTranscribe(projectId: string, language: string = 'auto', model: string = 'small', runtime?: string): Promise<{ task_id: string; message: string }> {
  const form = new FormData();
  form.append('language', language);
  form.append('model', model);
  if (runtime) form.append('runtime', runtime);
  const res = await authorizedFetch(`/api/projects/${projectId}/transcribe`, { method: 'POST', body: form }, false);
  if (!res.ok) throw await parseError(res);
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
  const res = await authorizedFetch(`/api/projects/${projectId}/clean`, { method: 'POST', body: form }, false);
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export async function undoClean(projectId: string): Promise<{ message: string; segments_count: number }> {
  return request(`/api/projects/${projectId}/clean/undo`, { method: 'POST' });
}

// ── AI Translate ──

export async function startTranslate(projectId: string, targetLanguage: string = 'zh'): Promise<{ task_id: string; message: string }> {
  const form = new FormData();
  form.append('target_language', targetLanguage);
  const res = await authorizedFetch(`/api/projects/${projectId}/translate`, { method: 'POST', body: form }, false);
  if (!res.ok) throw await parseError(res);
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

export async function applySegmentOperation(
  projectId: string, data: SegmentOperationRequest,
): Promise<EditorOperationResponse> {
  return request(`/api/projects/${projectId}/segment-operations`, {
    method: 'POST', body: JSON.stringify(data),
  });
}

export async function undoEditorOperation(projectId: string, expectedRevision: number): Promise<EditorOperationResponse> {
  return request(`/api/projects/${projectId}/editor/undo`, {
    method: 'POST', body: JSON.stringify({ expected_revision: expectedRevision }),
  });
}

export async function redoEditorOperation(projectId: string, expectedRevision: number): Promise<EditorOperationResponse> {
  return request(`/api/projects/${projectId}/editor/redo`, {
    method: 'POST', body: JSON.stringify({ expected_revision: expectedRevision }),
  });
}

export async function getSegmentDraft(projectId: string): Promise<{
  draft: { base_revision: number; items: Array<SegmentUpdate & { index: number }>; updated_at: string } | null;
}> {
  return request(`/api/projects/${projectId}/draft`);
}

export async function saveSegmentDraft(
  projectId: string, baseRevision: number, items: Array<SegmentUpdate & { index: number }>,
): Promise<void> {
  await request(`/api/projects/${projectId}/draft`, {
    method: 'PUT', body: JSON.stringify({ base_revision: baseRevision, items }),
  });
}

export async function commitSegmentDraft(projectId: string): Promise<EditorOperationResponse> {
  return request(`/api/projects/${projectId}/draft/commit`, { method: 'POST' });
}

export async function discardSegmentDraft(projectId: string): Promise<void> {
  await request(`/api/projects/${projectId}/draft`, { method: 'DELETE' });
}

// ── Export ──

export async function exportSubtitles(projectId: string, data: ExportRequest): Promise<{ path?: string; task_id?: string; message: string }> {
  return request(`/api/projects/${projectId}/export`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function downloadExport(projectId: string, fmt: string): Promise<void> {
  const response = await authorizedFetch(`/api/projects/${projectId}/export/download?fmt=${encodeURIComponent(fmt)}`);
  if (!response.ok) throw await parseError(response);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const disposition = response.headers.get('Content-Disposition') || '';
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = encoded ? decodeURIComponent(encoded) : plain || `subtitle-export.${fmt}`;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

export function getProjectThumbnailUrl(project: Pick<Project, 'thumbnail_url' | 'thumbnail_access_url'>): string | null {
  const value = project.thumbnail_access_url || project.thumbnail_url;
  return getBackendMediaUrl(value);
}

export function getBackendMediaUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  if (/^(?:https?:|data:|blob:)/i.test(value)) return value;
  return `${BASE_URL}${value.startsWith('/') ? '' : '/'}${value}`;
}

export interface WaveformData {
  fingerprint: string;
  duration: number;
  offset: number;
  sample_rate: number;
  points: number;
  peaks: number[];
}

export async function getProjectWaveform(projectId: string, points = 4000): Promise<WaveformData> {
  return request(`/api/projects/${projectId}/waveform?points=${points}`);
}

export async function scanProjectQuality(projectId: string, rules: Record<string, number> = {}): Promise<{issues: QualityIssue[]; total: number}> {
  return request(`/api/projects/${projectId}/quality/scan`, {
    method: 'POST', body: JSON.stringify({ rules }),
  });
}

export async function getProjectQualityIssues(projectId: string): Promise<{issues: QualityIssue[]; total: number}> {
  return request(`/api/projects/${projectId}/quality/issues`);
}

export async function updateQualityIssue(projectId: string, issueId: string, status: QualityIssue['status']): Promise<void> {
  await request(`/api/projects/${projectId}/quality/issues/${issueId}`, {
    method: 'PATCH', body: JSON.stringify({ status }),
  });
}
export async function fixQualityIssue(projectId: string, issueId: string, expectedRevision: number, apply: boolean): Promise<{preview: {before: string; after: string}; applied: boolean; editor?: EditorOperationResponse}> {
  return request(`/api/projects/${projectId}/quality/issues/${issueId}/fix`, { method: 'POST', body: JSON.stringify({ expected_revision: expectedRevision, apply }) });
}
export async function aiFixQualityIssue(projectId: string, issueId: string, expectedRevision: number, apply: boolean): Promise<{preview: {before: {clean_text: string; translated_text: string}; after: {clean_text: string; translated_text: string}}; applied: boolean; editor?: EditorOperationResponse}> {
  return request(`/api/projects/${projectId}/quality/issues/${issueId}/ai-fix`, { method: 'POST', body: JSON.stringify({ expected_revision: expectedRevision, apply }) });
}
export interface Glossary { id: string; name: string; project_id: string | null; term_count: number; source_language: string; target_language: string; }
export interface GlossaryTerm { id: string; source_text: string; target_text: string; case_sensitive: boolean; whole_word: boolean; do_not_translate: boolean; note: string; }
export async function getGlossaries(projectId: string): Promise<{glossaries: Glossary[]}> { return request(`/api/glossaries?project_id=${projectId}`); }
export async function createGlossary(projectId: string, name: string): Promise<Glossary> { return request('/api/glossaries', { method: 'POST', body: JSON.stringify({ project_id: projectId, name }) }); }
export async function getGlossaryTerms(glossaryId: string): Promise<{terms: GlossaryTerm[]}> { return request(`/api/glossaries/${glossaryId}/terms`); }
export async function addGlossaryTerm(glossaryId: string, term: Omit<GlossaryTerm, 'id'>): Promise<GlossaryTerm> { return request(`/api/glossaries/${glossaryId}/terms`, { method: 'POST', body: JSON.stringify(term) }); }
export async function deleteGlossaryTerm(glossaryId: string, termId: string): Promise<void> { await request(`/api/glossaries/${glossaryId}/terms/${termId}`, { method: 'DELETE' }); }
export interface GlossaryImportPreview { rows: Record<string, string>[]; new_count: number; conflicts: Array<{incoming: Record<string, string>; existing: GlossaryTerm}>; committed: boolean; }
export async function importGlossaryTerms(glossaryId: string, content: string, delimiter: ',' | '\t', commit = false): Promise<GlossaryImportPreview> {
  return request(`/api/glossaries/${glossaryId}/import`, { method: 'POST', body: JSON.stringify({ content, delimiter, commit }) });
}
export async function downloadGlossary(glossaryId: string, format: 'csv' | 'tsv'): Promise<void> {
  const response = await authorizedFetch(`/api/glossaries/${glossaryId}/export?format=${format}`);
  if (!response.ok) throw await parseError(response);
  const blob = await response.blob(); const url = URL.createObjectURL(blob); const anchor = document.createElement('a');
  anchor.href = url; anchor.download = `glossary.${format}`; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export interface Speaker { id: string; project_id: string; name: string; color: string; external_key?: string | null; }
export async function getSpeakers(projectId: string): Promise<{speakers: Speaker[]}> { return request(`/api/projects/${projectId}/speakers`); }
export async function createSpeaker(projectId: string, name: string, color: string): Promise<Speaker> {
  return request(`/api/projects/${projectId}/speakers`, { method: 'POST', body: JSON.stringify({ name, color }) });
}
export async function updateSpeaker(projectId: string, speakerId: string, name: string, color: string): Promise<Speaker> {
  return request(`/api/projects/${projectId}/speakers/${speakerId}`, { method: 'PUT', body: JSON.stringify({ name, color }) });
}
export async function mergeSpeakers(projectId: string, sourceId: string, targetId: string): Promise<void> {
  await request(`/api/projects/${projectId}/speakers/${sourceId}/merge/${targetId}`, { method: 'POST' });
}
export async function startSpeakerDiarization(projectId: string, segmentationModel: string, embeddingModel: string, numSpeakers?: number): Promise<{task_id: string}> {
  return request(`/api/projects/${projectId}/speakers/diarize`, { method: 'POST', body: JSON.stringify({ segmentation_model: segmentationModel, embedding_model: embeddingModel, num_speakers: numSpeakers || null }) });
}
export interface SpeakerModelStatus { ready: boolean; segmentation_model: string | null; embedding_model: string | null; managed_directory: string; task_id?: string; }
export async function getSpeakerModelStatus(): Promise<SpeakerModelStatus> { return request('/api/speaker-models'); }
export async function prepareSpeakerModels(): Promise<SpeakerModelStatus> { return request('/api/speaker-models/prepare', { method: 'POST' }); }
export interface OCRCue { start: number; end: number; text: string; confidence?: number; }
export async function startOCR(projectId: string, input: {region: {x: number; y: number; width: number; height: number}; start: number; end: number; interval: number}): Promise<{task_id: string}> {
  return request(`/api/projects/${projectId}/ocr`, { method: 'POST', body: JSON.stringify(input) });
}
export async function commitOCR(projectId: string, expectedRevision: number, cues: OCRCue[]): Promise<EditorOperationResponse> {
  return request(`/api/projects/${projectId}/ocr/commit`, { method: 'POST', body: JSON.stringify({ expected_revision: expectedRevision, cues }) });
}
export interface CloudAuthorization { capability: 'ocr' | 'speaker' | 'quality'; provider_id: string | null; granted: boolean | number; disclosure_version: string; granted_at: string | null; revoked_at: string | null; }
export async function getCloudAuthorizations(): Promise<{authorizations: CloudAuthorization[]}> { return request('/api/cloud-authorizations'); }
export async function setCloudAuthorization(capability: CloudAuthorization['capability'], granted: boolean, providerId?: string): Promise<void> {
  await request(`/api/cloud-authorizations/${capability}`, { method: 'PUT', body: JSON.stringify({ granted, provider_id: providerId || null, disclosure_version: '1.0' }) });
}

export interface WatchFolder { id: string; path: string; enabled: boolean; workflow: Record<string, unknown>; last_scan_at?: string | null; }
export interface WatchReadyFile { watch_folder_id: string; path: string; fingerprint: string; workflow: Record<string, unknown>; }
export async function getWatchFolders(): Promise<{watch_folders: WatchFolder[]}> { return request('/api/watch-folders'); }
export async function addWatchFolder(path: string, workflow: Record<string, unknown>): Promise<WatchFolder> {
  return request('/api/watch-folders', { method: 'POST', body: JSON.stringify({ path, enabled: true, workflow }) });
}
export async function removeWatchFolder(id: string): Promise<void> { await request(`/api/watch-folders/${id}`, { method: 'DELETE' }); }
export async function scanWatchFolders(): Promise<{ready: WatchReadyFile[]; count: number}> { return request('/api/watch-folders/scan', { method: 'POST' }); }
export async function markWatchImported(folderId: string, path: string, projectId: string): Promise<void> {
  await request(`/api/watch-folders/${folderId}/mark-imported`, { method: 'POST', body: JSON.stringify({ path, project_id: projectId }) });
}
export interface BatchItem { item_id: string; project_id: string; title: string; }
export async function createBatch(paths: string[], configuration: Record<string, unknown>, name = '批量导入'): Promise<{batch_id: string; items: BatchItem[]}> {
  return request('/api/batches', { method: 'POST', body: JSON.stringify({ name, paths, configuration }) });
}

export async function previewPlaylist(url: string): Promise<PlaylistPreview> {
  return request('/api/batches/playlist/preview', { method: 'POST', body: JSON.stringify({ url }) });
}

export async function createPlaylistBatch(url: string, configuration: Record<string, unknown>): Promise<{action:'created'|'synced';batch_id:string;added_count:number;existing_count:number;batch:PlaylistBatchDetail['batch']}> {
  return request('/api/batches/playlist', { method: 'POST', body: JSON.stringify({ url, configuration }) });
}

export async function getPlaylistBatches(): Promise<{batches: PlaylistBatchDetail[]}> {
  return request('/api/batches?kind=youtube_playlist');
}

export async function getPlaylistBatch(batchId: string): Promise<PlaylistBatchDetail> {
  return request(`/api/batches/${batchId}`);
}

export async function pausePlaylistBatch(batchId: string): Promise<PlaylistBatchDetail> {
  return request(`/api/batches/${batchId}/pause`, { method: 'POST' });
}

export async function resumePlaylistBatch(batchId: string): Promise<PlaylistBatchDetail> {
  return request(`/api/batches/${batchId}/resume`, { method: 'POST' });
}

export async function cancelPlaylistBatch(batchId: string): Promise<PlaylistBatchDetail> {
  return request(`/api/batches/${batchId}/cancel-pending`, { method: 'POST' });
}

export async function retryPlaylistBatch(batchId: string): Promise<PlaylistBatchDetail> {
  return request(`/api/batches/${batchId}/retry-failed`, { method: 'POST' });
}

export async function syncPlaylistBatch(batchId: string): Promise<{action:'created'|'synced';batch_id:string;added_count:number;existing_count:number}> {
  return request(`/api/batches/${batchId}/sync`, { method: 'POST' });
}

export async function deletePlaylistBatch(batchId: string): Promise<{batch_id:string;deleted_projects:number;message:string}> {
  return request(`/api/batches/${batchId}?confirm=true&terminate=true`, { method: 'DELETE' });
}

export async function retryPlaylistItem(batchId: string, itemId: string): Promise<PlaylistBatchDetail> {
  return request(`/api/batches/${batchId}/items/${itemId}/retry`, { method: 'POST' });
}

export async function runPlaylistStage(batchId: string, stage: Extract<PlaylistStageName,'transcribe'|'clean'|'translate'>, configuration: Record<string, unknown>): Promise<PlaylistBatchDetail> {
  return request(`/api/batches/${batchId}/stages/${stage}/run`, { method: 'POST', body: JSON.stringify({ configuration }) });
}

export interface StyleTemplate { id: string; name: string; builtin: boolean; settings: Record<string, unknown>; }
export async function getStyleTemplates(): Promise<{templates: StyleTemplate[]}> { return request('/api/style-templates'); }
export async function createStyleTemplate(name: string, settings: Record<string, unknown>): Promise<StyleTemplate> {
  return request('/api/style-templates', { method: 'POST', body: JSON.stringify({ name, settings }) });
}
export async function getProjectStyle(projectId: string): Promise<{settings: Record<string, unknown> | null; updated_at: string | null}> {
  return request(`/api/projects/${projectId}/style`);
}
export async function saveProjectStyle(projectId: string, settings: Record<string, unknown>): Promise<void> {
  await request(`/api/projects/${projectId}/style`, { method: 'PUT', body: JSON.stringify({ settings }) });
}

export interface BackupRecord { name: string; path: string; size: number; modified_at: string; }
export async function getBackups(): Promise<{directory: string; backups: BackupRecord[]}> { return request('/api/maintenance/backups'); }
export async function createBackup(): Promise<BackupRecord> { return request('/api/maintenance/backups', { method: 'POST' }); }
export async function restoreBackup(name: string): Promise<void> {
  await request('/api/maintenance/backups/restore', { method: 'POST', body: JSON.stringify({ name, confirm: true }) });
}
export async function revealLocalPath(path: string): Promise<void> {
  if ((window as any).__TAURI_INTERNALS__) await invoke('reveal_path', { path });
}
export async function downloadDiagnostics(): Promise<void> {
  const response = await authorizedFetch('/api/maintenance/diagnostics', { method: 'POST' });
  if (!response.ok) throw await parseError(response);
  const blob = await response.blob(); const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'subtitle-factory-diagnostics.zip'; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function createProjectPackage(projectId: string, includeMedia: boolean): Promise<{package_id: string; filename: string; size: number}> {
  return request(`/api/projects/${projectId}/package?include_media=${includeMedia}`, { method: 'POST' });
}
export async function downloadProjectPackage(packageId: string, filename: string): Promise<void> {
  const response = await authorizedFetch(`/api/project-packages/${packageId}/download`);
  if (!response.ok) throw await parseError(response);
  const blob = await response.blob(); const url = URL.createObjectURL(blob); const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export async function importProjectPackage(file: File): Promise<{project_id: string; media_status: string}> {
  const form = new FormData(); form.append('file', file);
  const response = await authorizedFetch('/api/project-packages/import', { method: 'POST', body: form }, false);
  if (!response.ok) throw await parseError(response); return response.json();
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

export async function getGlobalTasks(limit = 100): Promise<{tasks: TaskStatus[]}> {
  return request(`/api/tasks?limit=${limit}`);
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

export async function getFailedCleanBatches(taskId: string): Promise<{task_id: string; batches: import('../types').FailedCleanBatch[]}> {
  return request(`/api/tasks/${taskId}/failed-batches`);
}

export async function retryFailedCleanBatch(taskId: string, batchIndex: number): Promise<{task_id: string; retry_of: string; batch_index: number}> {
  return request(`/api/tasks/${taskId}/retry-failed-batches/${batchIndex}`, { method: 'POST' });
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
  const res = await authorizedFetch('/api/health', { method: 'GET' });
  if (!res.ok) throw await parseError(res);
  return res.json();
}

// ── Incremental Segments ──

export async function getSegmentsAfter(projectId: string, afterIdx: number): Promise<{
  segments: SubtitleSegment[];
  total: number;
  latest_idx: number;
  has_more: boolean;
}> {
  const res = await authorizedFetch(`/api/projects/${projectId}/segments?after_idx=${afterIdx}`);
  if (!res.ok) throw await parseError(res);
  return res.json();
}
