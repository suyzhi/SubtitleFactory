// 质检、术语库、说话人分离、OCR、样式模板、监听文件夹与播放列表批处理 API

import type {
  EditorOperationResponse,
  PlaylistBatchDetail,
  PlaylistPreview,
  PlaylistStageName,
  QualityIssue,
} from '../types';
import {
  downloadBackendFile,
  isTauriDesktop,
  request,
  type PreparedFile,
} from './client';

export async function scanProjectQuality(
  projectId: string,
  rules: Record<string, number> = {},
): Promise<{ issues: QualityIssue[]; total: number }> {
  return request(`/api/projects/${projectId}/quality/scan`, {
    method: 'POST',
    body: JSON.stringify({ rules }),
  });
}

export async function getProjectQualityIssues(
  projectId: string,
): Promise<{ issues: QualityIssue[]; total: number }> {
  return request(`/api/projects/${projectId}/quality/issues`);
}

export async function updateQualityIssue(
  projectId: string,
  issueId: string,
  status: QualityIssue['status'],
): Promise<void> {
  await request(`/api/projects/${projectId}/quality/issues/${issueId}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  });
}

export async function fixQualityIssue(
  projectId: string,
  issueId: string,
  expectedRevision: number,
  apply: boolean,
): Promise<{
  preview: { before: string; after: string };
  applied: boolean;
  editor?: EditorOperationResponse;
}> {
  return request(`/api/projects/${projectId}/quality/issues/${issueId}/fix`, {
    method: 'POST',
    body: JSON.stringify({ expected_revision: expectedRevision, apply }),
  });
}

export async function aiFixQualityIssue(
  projectId: string,
  issueId: string,
  expectedRevision: number,
  apply: boolean,
): Promise<{
  preview: {
    before: { clean_text: string; translated_text: string };
    after: { clean_text: string; translated_text: string };
  };
  applied: boolean;
  editor?: EditorOperationResponse;
}> {
  return request(`/api/projects/${projectId}/quality/issues/${issueId}/ai-fix`, {
    method: 'POST',
    body: JSON.stringify({ expected_revision: expectedRevision, apply }),
  });
}

export interface Glossary {
  id: string;
  name: string;
  project_id: string | null;
  term_count: number;
  source_language: string;
  target_language: string;
}

export interface GlossaryTerm {
  id: string;
  source_text: string;
  target_text: string;
  case_sensitive: boolean;
  whole_word: boolean;
  do_not_translate: boolean;
  note: string;
}

export interface GlossaryImportPreview {
  rows: Record<string, string>[];
  new_count: number;
  conflicts: Array<{ incoming: Record<string, string>; existing: GlossaryTerm }>;
  committed: boolean;
}

export async function getGlossaries(projectId: string): Promise<{ glossaries: Glossary[] }> {
  return request(`/api/glossaries?project_id=${projectId}`);
}

export async function createGlossary(projectId: string, name: string): Promise<Glossary> {
  return request('/api/glossaries', {
    method: 'POST',
    body: JSON.stringify({ project_id: projectId, name }),
  });
}

export async function getGlossaryTerms(glossaryId: string): Promise<{ terms: GlossaryTerm[] }> {
  return request(`/api/glossaries/${glossaryId}/terms`);
}

export async function addGlossaryTerm(
  glossaryId: string,
  term: Omit<GlossaryTerm, 'id'>,
): Promise<GlossaryTerm> {
  return request(`/api/glossaries/${glossaryId}/terms`, {
    method: 'POST',
    body: JSON.stringify(term),
  });
}

export async function deleteGlossaryTerm(glossaryId: string, termId: string): Promise<void> {
  await request(`/api/glossaries/${glossaryId}/terms/${termId}`, { method: 'DELETE' });
}

export async function importGlossaryTerms(
  glossaryId: string,
  content: string,
  delimiter: ',' | '\t',
  commit = false,
): Promise<GlossaryImportPreview> {
  return request(`/api/glossaries/${glossaryId}/import`, {
    method: 'POST',
    body: JSON.stringify({ content, delimiter, commit }),
  });
}

export async function downloadGlossary(
  glossaryId: string,
  format: 'csv' | 'tsv',
  suggestedName = `glossary.${format}`,
): Promise<boolean> {
  let prepared: PreparedFile | undefined;
  if (isTauriDesktop()) {
    prepared = await request(`/api/glossaries/${glossaryId}/export-file?format=${format}`, {
      method: 'POST',
    });
  }
  return downloadBackendFile(
    `/api/glossaries/${glossaryId}/export?format=${format}`,
    suggestedName,
    prepared?.path,
  );
}

export interface Speaker {
  id: string;
  project_id: string;
  name: string;
  color: string;
  external_key?: string | null;
}

export interface SpeakerModelStatus {
  ready: boolean;
  segmentation_model: string | null;
  embedding_model: string | null;
  managed_directory: string;
  task_id?: string;
}

export async function getSpeakers(projectId: string): Promise<{ speakers: Speaker[] }> {
  return request(`/api/projects/${projectId}/speakers`);
}

export async function createSpeaker(
  projectId: string,
  name: string,
  color: string,
): Promise<Speaker> {
  return request(`/api/projects/${projectId}/speakers`, {
    method: 'POST',
    body: JSON.stringify({ name, color }),
  });
}

export async function updateSpeaker(
  projectId: string,
  speakerId: string,
  name: string,
  color: string,
): Promise<Speaker> {
  return request(`/api/projects/${projectId}/speakers/${speakerId}`, {
    method: 'PUT',
    body: JSON.stringify({ name, color }),
  });
}

export async function mergeSpeakers(
  projectId: string,
  sourceId: string,
  targetId: string,
): Promise<void> {
  await request(`/api/projects/${projectId}/speakers/${sourceId}/merge/${targetId}`, {
    method: 'POST',
  });
}

export async function startSpeakerDiarization(
  projectId: string,
  segmentationModel: string,
  embeddingModel: string,
  numSpeakers?: number,
): Promise<{ task_id: string }> {
  return request(`/api/projects/${projectId}/speakers/diarize`, {
    method: 'POST',
    body: JSON.stringify({
      segmentation_model: segmentationModel,
      embedding_model: embeddingModel,
      num_speakers: numSpeakers || null,
    }),
  });
}

export async function getSpeakerModelStatus(): Promise<SpeakerModelStatus> {
  return request('/api/speaker-models');
}

export async function prepareSpeakerModels(): Promise<SpeakerModelStatus> {
  return request('/api/speaker-models/prepare', { method: 'POST' });
}

export interface OCRCue {
  start: number;
  end: number;
  text: string;
  confidence?: number;
}

export async function startOCR(
  projectId: string,
  input: {
    region: { x: number; y: number; width: number; height: number };
    start: number;
    end: number;
    interval: number;
  },
): Promise<{ task_id: string }> {
  return request(`/api/projects/${projectId}/ocr`, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function commitOCR(
  projectId: string,
  expectedRevision: number,
  cues: OCRCue[],
): Promise<EditorOperationResponse> {
  return request(`/api/projects/${projectId}/ocr/commit`, {
    method: 'POST',
    body: JSON.stringify({ expected_revision: expectedRevision, cues }),
  });
}

export interface WatchFolder {
  id: string;
  path: string;
  enabled: boolean;
  workflow: Record<string, unknown>;
  last_scan_at?: string | null;
}

export interface WatchReadyFile {
  watch_folder_id: string;
  path: string;
  fingerprint: string;
  workflow: Record<string, unknown>;
}

export async function getWatchFolders(): Promise<{ watch_folders: WatchFolder[] }> {
  return request('/api/watch-folders');
}

export async function addWatchFolder(
  path: string,
  workflow: Record<string, unknown>,
): Promise<WatchFolder> {
  return request('/api/watch-folders', {
    method: 'POST',
    body: JSON.stringify({ path, enabled: true, workflow }),
  });
}

export async function removeWatchFolder(id: string): Promise<void> {
  await request(`/api/watch-folders/${id}`, { method: 'DELETE' });
}

export async function scanWatchFolders(): Promise<{ ready: WatchReadyFile[]; count: number }> {
  return request('/api/watch-folders/scan', { method: 'POST' });
}

export async function markWatchImported(
  folderId: string,
  path: string,
  projectId: string,
): Promise<void> {
  await request(`/api/watch-folders/${folderId}/mark-imported`, {
    method: 'POST',
    body: JSON.stringify({ path, project_id: projectId }),
  });
}

export interface BatchItem {
  item_id: string;
  project_id: string;
  title: string;
}

export async function createBatch(
  paths: string[],
  configuration: Record<string, unknown>,
  name = '批量导入',
): Promise<{ batch_id: string; items: BatchItem[] }> {
  return request('/api/batches', {
    method: 'POST',
    body: JSON.stringify({ name, paths, configuration }),
  });
}

export async function previewPlaylist(url: string): Promise<PlaylistPreview> {
  return request('/api/batches/playlist/preview', {
    method: 'POST',
    body: JSON.stringify({ url }),
  });
}

export async function createPlaylistBatch(
  url: string,
  configuration: Record<string, unknown>,
): Promise<{
  action: 'created' | 'synced';
  batch_id: string;
  added_count: number;
  existing_count: number;
  batch: PlaylistBatchDetail['batch'];
}> {
  return request('/api/batches/playlist', {
    method: 'POST',
    body: JSON.stringify({ url, configuration }),
  });
}

export async function getPlaylistBatches(): Promise<{ batches: PlaylistBatchDetail[] }> {
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

export async function syncPlaylistBatch(batchId: string): Promise<{
  action: 'created' | 'synced';
  batch_id: string;
  added_count: number;
  existing_count: number;
}> {
  return request(`/api/batches/${batchId}/sync`, { method: 'POST' });
}

export async function deletePlaylistBatch(
  batchId: string,
): Promise<{ batch_id: string; deleted_projects: number; message: string }> {
  return request(`/api/batches/${batchId}?confirm=true&terminate=true`, { method: 'DELETE' });
}

export async function retryPlaylistItem(
  batchId: string,
  itemId: string,
): Promise<PlaylistBatchDetail> {
  return request(`/api/batches/${batchId}/items/${itemId}/retry`, { method: 'POST' });
}

export async function runPlaylistStage(
  batchId: string,
  stage: Extract<PlaylistStageName, 'transcribe' | 'clean' | 'translate'>,
  configuration: Record<string, unknown>,
): Promise<PlaylistBatchDetail> {
  return request(`/api/batches/${batchId}/stages/${stage}/run`, {
    method: 'POST',
    body: JSON.stringify({ configuration }),
  });
}

export interface StyleTemplate {
  id: string;
  name: string;
  builtin: boolean;
  settings: Record<string, unknown>;
}

export async function getStyleTemplates(): Promise<{ templates: StyleTemplate[] }> {
  return request('/api/style-templates');
}

export async function createStyleTemplate(
  name: string,
  settings: Record<string, unknown>,
): Promise<StyleTemplate> {
  return request('/api/style-templates', {
    method: 'POST',
    body: JSON.stringify({ name, settings }),
  });
}

export async function getProjectStyle(
  projectId: string,
): Promise<{ settings: Record<string, unknown> | null; updated_at: string | null }> {
  return request(`/api/projects/${projectId}/style`);
}

export async function saveProjectStyle(
  projectId: string,
  settings: Record<string, unknown>,
): Promise<void> {
  await request(`/api/projects/${projectId}/style`, {
    method: 'PUT',
    body: JSON.stringify({ settings }),
  });
}
