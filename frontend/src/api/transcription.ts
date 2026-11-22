// 转写模型、语音转写、工作流、AI 整理/翻译、字幕段编辑与导出 API

import type {
  EditorOperationResponse,
  ExportRequest,
  SegmentOperationRequest,
  SegmentUpdate,
  SubtitleSegment,
} from '../types';
import { backendFetch, downloadBackendFile, parseError, request } from './client';

export interface TranscriptionModelStatus {
  id: string;
  name: string;
  ready: boolean;
  download_required: boolean;
  download_bytes?: number;
  runtime_error?: string | null;
  languages: string[];
  source?: string;
  status?: string;
  path?: string | null;
  category_id?: string;
  category_name?: string;
  purpose?: string;
  language_description?: string;
  size_label?: string;
  publisher?: string;
  tags?: string[];
  source_site?: string;
  family?: string;
  scenarios?: string[];
  strengths?: string[];
  limitations?: string[];
  speed_tier?: string;
  accuracy_tier?: string;
  memory_tier?: string;
  timestamp_mode?: 'word' | 'token' | 'segment' | string;
  punctuation_mode?: 'native' | 'none' | 'limited' | string;
  installed_bytes?: number;
  license?: string;
  removable?: boolean;
  runtimes?: {
    id: string;
    name: string;
    engine?: string;
    available: boolean;
    reason?: string;
    model_ready?: boolean;
    download_required?: boolean;
    download_bytes?: number;
    repository?: string;
    revision?: string | null;
    source_url?: string | null;
    source?: string;
    status?: string;
  }[];
  selected_runtime?: string | null;
  format?: string;
  version?: string;
}

export interface ScannedModel {
  path: string;
  display_name: string;
  family: string;
  format: string;
  version: string;
  supported: boolean;
  reason?: string;
  cli_path?: string;
  runtimes?: string[];
}

export interface TranscriptionCandidate {
  id: string;
  model: string;
  language: string;
  segments_count: number;
  finished_at: string;
  status?: string;
}

export async function startTranscribe(
  projectId: string,
  language: string = 'auto',
  model: string = 'small',
  runtime?: string,
): Promise<{ task_id: string; message: string }> {
  const form = new FormData();
  form.append('language', language);
  form.append('model', model);
  if (runtime) form.append('runtime', runtime);
  const res = await backendFetch(
    `/api/projects/${projectId}/transcribe`,
    { method: 'POST', body: form },
    false,
  );
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export async function startWorkflow(
  projectId: string,
  data: {
    model: string;
    language: string;
    runtime?: string;
    source_url?: string;
    enable_clean?: boolean;
    enable_translate?: boolean;
    target_language?: string;
    clean_target_length?: number;
    text_processing_consent?: boolean;
    resume_task_id?: string;
  },
): Promise<{ task_id: string; message: string; model: string }> {
  return request(`/api/projects/${projectId}/workflow`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function retryTranscription(
  projectId: string,
  data: { model: string; language: string; runtime?: string },
): Promise<{ task_id: string; message: string; model: string }> {
  return request(`/api/projects/${projectId}/transcribe/retry`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getTranscriptionModels(
  projectId?: string,
  language = 'auto',
): Promise<{
  recommended_model: string;
  recommendation_reason?: string;
  audio?: { ok: boolean; error_code?: string; message?: string; duration?: number } | null;
  category_order?: string[];
  models: TranscriptionModelStatus[];
}> {
  const query = new URLSearchParams({ language });
  if (projectId) query.set('project_id', projectId);
  return request(`/api/transcription/models?${query.toString()}`);
}

export async function prepareTranscriptionModel(
  modelId: string,
  runtime: string,
  repair = false,
): Promise<{ task_id: string; model_id: string; runtime: string; message: string }> {
  return request(`/api/transcription/models/${encodeURIComponent(modelId)}/prepare`, {
    method: 'POST',
    body: JSON.stringify({ runtime, repair }),
  });
}

export async function validateTranscriptionModel(
  modelId: string,
): Promise<TranscriptionModelStatus> {
  return request(`/api/transcription/models/${encodeURIComponent(modelId)}/validate`);
}

export async function removeTranscriptionModel(
  modelId: string,
): Promise<{ model_id: string; removed: boolean; removed_bytes: number; message: string }> {
  return request(`/api/transcription/models/${encodeURIComponent(modelId)}/files`, {
    method: 'DELETE',
  });
}

export const scanLocalModels = (path: string) =>
  request<{ models: ScannedModel[] }>('/api/transcription/models/scan', {
    method: 'POST',
    body: JSON.stringify({ root_path: path }),
  });

export const importLocalModel = (path: string, cli_path?: string) =>
  request('/api/transcription/models/import', {
    method: 'POST',
    body: JSON.stringify({ path, cli_path }),
  });

export async function startClean(
  projectId: string,
  targetLength: number = 42,
): Promise<{ task_id: string; message: string }> {
  const form = new FormData();
  form.append('target_length', String(targetLength));
  const res = await backendFetch(
    `/api/projects/${projectId}/clean`,
    { method: 'POST', body: form },
    false,
  );
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export async function undoClean(
  projectId: string,
): Promise<{ message: string; segments_count: number }> {
  return request(`/api/projects/${projectId}/clean/undo`, { method: 'POST' });
}

export async function startTranslate(
  projectId: string,
  targetLanguage: string = 'zh',
): Promise<{ task_id: string; message: string }> {
  const form = new FormData();
  form.append('target_language', targetLanguage);
  const res = await backendFetch(
    `/api/projects/${projectId}/translate`,
    { method: 'POST', body: form },
    false,
  );
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export async function importSubtitleFile(
  projectId: string,
  file: File,
  expectedRevision: number,
): Promise<EditorOperationResponse> {
  const form = new FormData();
  form.append('file', file);
  form.append('expected_revision', String(expectedRevision));
  const response = await backendFetch(
    `/api/projects/${projectId}/subtitles/import`,
    { method: 'POST', body: form },
    false,
  );
  if (!response.ok) throw await parseError(response);
  return response.json();
}

export async function getSegments(
  projectId: string,
): Promise<{ segments: SubtitleSegment[]; total: number }> {
  return request(`/api/projects/${projectId}/segments`);
}

export async function getSegmentsAfter(
  projectId: string,
  afterIdx: number,
): Promise<{
  segments: SubtitleSegment[];
  total: number;
  latest_idx: number;
  has_more: boolean;
}> {
  const res = await backendFetch(`/api/projects/${projectId}/segments?after_idx=${afterIdx}`);
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export async function updateSegment(
  projectId: string,
  segmentIndex: number,
  data: SegmentUpdate,
): Promise<SubtitleSegment> {
  return request(`/api/projects/${projectId}/segments/${segmentIndex}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function applySegmentOperation(
  projectId: string,
  data: SegmentOperationRequest,
): Promise<EditorOperationResponse> {
  return request(`/api/projects/${projectId}/segment-operations`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function undoEditorOperation(
  projectId: string,
  expectedRevision: number,
): Promise<EditorOperationResponse> {
  return request(`/api/projects/${projectId}/editor/undo`, {
    method: 'POST',
    body: JSON.stringify({ expected_revision: expectedRevision }),
  });
}

export async function redoEditorOperation(
  projectId: string,
  expectedRevision: number,
): Promise<EditorOperationResponse> {
  return request(`/api/projects/${projectId}/editor/redo`, {
    method: 'POST',
    body: JSON.stringify({ expected_revision: expectedRevision }),
  });
}

export async function getSegmentDraft(projectId: string): Promise<{
  draft: {
    base_revision: number;
    items: Array<SegmentUpdate & { index: number }>;
    updated_at: string;
    invalid?: boolean;
  } | null;
}> {
  return request(`/api/projects/${projectId}/draft`);
}

export async function saveSegmentDraft(
  projectId: string,
  baseRevision: number,
  items: Array<SegmentUpdate & { index: number }>,
): Promise<void> {
  await request(`/api/projects/${projectId}/draft`, {
    method: 'PUT',
    body: JSON.stringify({ base_revision: baseRevision, items }),
  });
}

export async function commitSegmentDraft(projectId: string): Promise<EditorOperationResponse> {
  return request(`/api/projects/${projectId}/draft/commit`, { method: 'POST' });
}

export async function rebaseSegmentDraft(projectId: string): Promise<EditorOperationResponse> {
  return request(`/api/projects/${projectId}/draft/rebase`, {
    method: 'POST',
    body: JSON.stringify({ confirm: true }),
  });
}

export async function discardSegmentDraft(projectId: string): Promise<void> {
  await request(`/api/projects/${projectId}/draft`, { method: 'DELETE' });
}

export async function exportSubtitles(
  projectId: string,
  data: ExportRequest,
): Promise<{ path?: string; task_id?: string; message: string }> {
  return request(`/api/projects/${projectId}/export`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function downloadExport(
  projectId: string,
  fmt: string,
  sourcePath?: string,
  suggestedName = `subtitle-export.${fmt}`,
): Promise<boolean> {
  return downloadBackendFile(
    `/api/projects/${projectId}/export/download?fmt=${encodeURIComponent(fmt)}`,
    suggestedName,
    sourcePath,
  );
}

export function listTranscriptionCandidates(
  projectId: string,
): Promise<{ candidates: TranscriptionCandidate[] }> {
  return request(`/api/projects/${projectId}/transcription-candidates`);
}

export function getTranscriptionCandidate(
  projectId: string,
  runId: string,
): Promise<{ segments: Array<{ start: number; end: number; text: string }> }> {
  return request(`/api/projects/${projectId}/transcription-candidates/${runId}`);
}

export function acceptTranscriptionCandidate(
  projectId: string,
  runId: string,
  revision: number,
): Promise<EditorOperationResponse> {
  return request(
    `/api/projects/${projectId}/transcription-candidates/${runId}/accept?expected_revision=${revision}`,
    { method: 'POST' },
  );
}
