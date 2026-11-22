// 项目 CRUD、媒体下载/导入、音轨选择、波形与字幕全文检索 API

import type { Project, ProjectCreate, SegmentSearchHit } from '../types';
import {
  BackendError,
  backendFetch,
  getBackendMediaUrl,
  getBaseUrl,
  parseError,
  request,
} from './client';
import { getTaskStatus } from './tasks';

export async function listProjects(options?: {
  deleted?: boolean;
  search?: string;
  sort?: string;
  page?: number;
  page_size?: number;
  readiness?: string;
}): Promise<{ projects: Project[]; total?: number; pages?: number }> {
  const params = new URLSearchParams();
  if (options?.readiness) params.set('readiness', options.readiness);
  if (options?.deleted) params.set('deleted', 'true');
  if (options?.search) params.set('search', options.search);
  if (options?.sort) params.set('sort', options.sort);
  if (options?.page) params.set('page', String(options.page));
  if (options?.page_size) params.set('page_size', String(options.page_size));
  return request(`/api/projects${params.size ? `?${params}` : ''}`);
}

export async function createProject(
  data: ProjectCreate,
): Promise<{ project_id: string; message: string }> {
  return request('/api/projects', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getProject(projectId: string): Promise<Project> {
  return request(`/api/projects/${projectId}`);
}

export async function updateProjectGroup(
  projectId: string,
  groupName: string | null,
): Promise<Project> {
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

export async function updateProjectTargetLanguage(
  projectId: string,
  target_language: string,
): Promise<Project> {
  return request(`/api/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify({ target_language }),
  });
}

export async function trashProject(
  projectId: string,
  terminate = false,
): Promise<{ project?: Project; message?: string }> {
  return request(`/api/projects/${projectId}/trash${terminate ? '?terminate=true' : ''}`, {
    method: 'POST',
  });
}

export async function restoreProject(
  projectId: string,
): Promise<{ project?: Project; message?: string }> {
  return request(`/api/projects/${projectId}/restore`, { method: 'POST' });
}

export async function permanentlyDeleteProject(
  projectId: string,
): Promise<{ message?: string }> {
  return request(`/api/projects/${projectId}?permanent=true`, { method: 'DELETE' });
}

export async function emptyTrash(): Promise<{ message?: string; deleted_count?: number }> {
  return request('/api/projects/trash?confirm=true', { method: 'DELETE' });
}

export async function startDownload(
  projectId: string,
  url: string,
): Promise<{ task_id: string; message: string }> {
  const form = new FormData();
  form.append('url', url);
  const res = await backendFetch(
    `/api/projects/${projectId}/download`,
    { method: 'POST', body: form },
    false,
  );
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export async function prepareProjectAudio(
  projectId: string,
): Promise<{ task_id: string; message: string }> {
  return request(`/api/projects/${projectId}/prepare-audio`, { method: 'POST' });
}

export async function updateProjectMediaMode(
  projectId: string,
  mediaMode: 'local' | 'web',
): Promise<{ project: Project; task_id?: string; message: string }> {
  return request(`/api/projects/${projectId}/media-mode`, {
    method: 'PATCH',
    body: JSON.stringify({ media_mode: mediaMode }),
  });
}

export async function materializeProjectVideo(
  projectId: string,
  reason: 'manual' | 'player_fallback' | 'offline' = 'manual',
): Promise<{ project?: Project; task_id?: string; message: string }> {
  const endpoint = `/api/projects/${projectId}/materialize-video?reason=${reason}`;
  let conflictRetries = 0;
  while (true) {
    try {
      return await request(endpoint, { method: 'POST' });
    } catch (error) {
      const taskIds =
        error instanceof BackendError && Array.isArray(error.details.task_ids)
          ? error.details.task_ids.filter((item): item is string => typeof item === 'string')
          : [];
      const shouldWaitForAudio =
        reason === 'player_fallback' &&
        error instanceof BackendError &&
        error.status === 409 &&
        error.code === 'MEDIA_TASK_ACTIVE' &&
        taskIds.length > 0 &&
        conflictRetries < 2;
      if (!shouldWaitForAudio) throw error;

      conflictRetries += 1;
      for (let attempt = 0; attempt < 600; attempt += 1) {
        const statuses = await Promise.all(taskIds.map(taskId => getTaskStatus(taskId)));
        if (
          statuses.every(status =>
            ['success', 'failed', 'cancelled', 'partial'].includes(status.status),
          )
        ) {
          break;
        }
        await new Promise(resolve => window.setTimeout(resolve, 1000));
        if (attempt === 599) {
          throw new BackendError(409, {
            code: 'MEDIA_TASK_WAIT_TIMEOUT',
            message: '等待当前媒体任务结束超时，请稍后手动下载本地副本',
            details: { task_ids: taskIds },
          });
        }
      }
    }
  }
}

export async function importLocalVideo(
  projectId: string,
  file: File,
  options?: {
    autostart?: boolean;
    model?: string;
    language?: string;
    runtime?: string;
    onProgress?: (percent: number) => void;
  },
): Promise<{ message: string; video_path: string; task_id?: string }> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file);
    form.append('autostart', String(options?.autostart ?? false));
    form.append('model', options?.model || 'auto');
    form.append('language', options?.language || 'auto');
    if (options?.runtime) form.append('runtime', options.runtime);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${getBaseUrl()}/api/projects/${projectId}/import-local`);
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) {
        options?.onProgress?.(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onerror = () => reject(new Error('视频上传失败，请检查本地后端连接'));
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        return reject(new Error(xhr.responseText || `导入失败 (${xhr.status})`));
      }
      try {
        resolve(JSON.parse(xhr.responseText));
      } catch {
        reject(new Error('导入响应格式无效'));
      }
    };
    xhr.send(form);
  });
}

export async function startExtractAudio(
  projectId: string,
): Promise<{ task_id: string; message: string }> {
  return request(`/api/projects/${projectId}/extract-audio`, { method: 'POST' });
}

export interface MediaInfo {
  duration: number | null;
  audio_tracks: Array<{
    index: number;
    title: string;
    language: string;
    codec: string;
    channels: number;
    sample_rate: number;
    duration: number | null;
  }>;
  selection: {
    audio_track_index: number;
    range_start: number | null;
    range_end: number | null;
  };
}

export async function getMediaInfo(projectId: string): Promise<MediaInfo> {
  return request(`/api/projects/${projectId}/media-info`);
}

export async function updateMediaSelection(
  projectId: string,
  selection: MediaInfo['selection'],
): Promise<{ audio_reextract_required: boolean }> {
  return request(`/api/projects/${projectId}/media-selection`, {
    method: 'PUT',
    body: JSON.stringify(selection),
  });
}

export async function getMediaTrackPreview(
  projectId: string,
  track: number,
  start = 0,
): Promise<string> {
  const response = await backendFetch(
    `/api/projects/${projectId}/media-track-preview?track=${track}&start=${start}`,
  );
  if (!response.ok) throw await parseError(response);
  return URL.createObjectURL(await response.blob());
}

export function getProjectThumbnailUrl(
  project: Pick<Project, 'thumbnail_url' | 'thumbnail_access_url'>,
): string | null {
  const value = project.thumbnail_access_url || project.thumbnail_url;
  return getBackendMediaUrl(value);
}

export interface PlaybackInfo {
  frame_rate: number;
  frame_duration: number;
  duration: number | null;
  frame_rate_reliable: boolean;
  frame_rate_source: 'average_rate' | 'fallback_30fps';
}

export async function getPlaybackInfo(projectId: string): Promise<PlaybackInfo> {
  return request(`/api/projects/${projectId}/playback-info`);
}

export interface WaveformData {
  fingerprint: string;
  duration: number;
  offset: number;
  sample_rate: number;
  points: number;
  peaks: number[];
}

export async function getProjectWaveform(
  projectId: string,
  points = 4000,
): Promise<WaveformData> {
  return request(`/api/projects/${projectId}/waveform?points=${points}`);
}

export interface SegmentSearchFilters {
  page?: number;
  page_size?: number;
  project_id?: string;
  group_name?: string;
  source_language?: string;
  target_language?: string;
  created_from?: string;
  created_to?: string;
}

export interface SegmentSearchFacets {
  projects?: Array<{
    id: string;
    title: string;
    group_name?: string | null;
    source_language: string;
    target_language: string;
  }>;
  groups?: string[];
}

export async function searchSegments(
  query: string,
  filters: SegmentSearchFilters = {},
): Promise<{
  query: string;
  hits: SegmentSearchHit[];
  total: number;
  page: number;
  page_size: number;
  facets: SegmentSearchFacets;
}> {
  const params = new URLSearchParams({ q: query });
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  });
  return request(`/api/search/segments?${params}`);
}


