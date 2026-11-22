// 全文字幕检索、内容发布包与短视频切片 API

import type {
  ClipAspectRatio,
  ClipRender,
  ClipSet,
  ContentPack,
  ContentPackInputMode,
  SegmentSearchHit,
} from '../types';
import { downloadBackendFile, isTauriDesktop, request } from './client';

export interface SegmentSearchFilters {
  page?: number;
  page_size?: number;
  project_id?: string;
  group_name?: string;
  speaker_id?: string;
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
  speakers?: Array<{ id: string; name: string; project_id: string; project_title: string }>;
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

export async function rebuildSearchIndex(): Promise<{ indexed: number; source: number }> {
  return request('/api/maintenance/search-index/rebuild', { method: 'POST' });
}

export async function getContentPacks(projectId: string): Promise<{ packs: ContentPack[] }> {
  return request(`/api/projects/${projectId}/content-packs`);
}

export async function createContentPack(
  projectId: string,
  data: {
    name: string;
    input_mode: ContentPackInputMode;
    output_language: string;
    allow_translation_fallback: boolean;
  },
): Promise<{ pack: ContentPack; task_id: string }> {
  return request(`/api/projects/${projectId}/content-packs`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getContentPack(packId: string): Promise<ContentPack> {
  return request(`/api/content-packs/${packId}`);
}

export async function updateContentPack(
  packId: string,
  name: string,
  expectedRevision: number,
): Promise<ContentPack> {
  return request(`/api/content-packs/${packId}`, {
    method: 'PATCH',
    body: JSON.stringify({ name, expected_revision: expectedRevision }),
  });
}

export async function updateContentSection(
  packId: string,
  kind: string,
  content: Record<string, unknown>,
  expectedRevision: number,
): Promise<ContentPack> {
  return request(`/api/content-packs/${packId}/sections/${kind}`, {
    method: 'PUT',
    body: JSON.stringify({ content, expected_revision: expectedRevision }),
  });
}

export async function regenerateContentSection(
  packId: string,
  kind: string,
): Promise<{ task_id: string }> {
  return request(`/api/content-packs/${packId}/sections/${kind}/regenerate`, { method: 'POST' });
}

export async function deleteContentPack(packId: string): Promise<void> {
  await request(`/api/content-packs/${packId}?confirm=true`, { method: 'DELETE' });
}

export async function exportContentPack(
  packId: string,
): Promise<{ export_id: string; filename: string; path: string; size: number }> {
  return request(`/api/content-packs/${packId}/export`, { method: 'POST' });
}

export async function downloadContentPack(
  exportId: string,
  filename: string,
  sourcePath?: string,
): Promise<boolean> {
  return downloadBackendFile(
    `/api/content-pack-exports/${encodeURIComponent(exportId)}/download`,
    filename,
    sourcePath,
  );
}

export async function getClipSets(projectId: string): Promise<{ clip_sets: ClipSet[] }> {
  return request(`/api/projects/${projectId}/clip-sets`);
}

export async function createClipSet(
  projectId: string,
  data: {
    name: string;
    desired_count: 3 | 5 | 10;
    min_duration: number;
    max_duration: number;
  },
): Promise<{ clip_set_id: string; task_id: string }> {
  return request(`/api/projects/${projectId}/clip-sets`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getClipSet(clipSetId: string): Promise<ClipSet> {
  return request(`/api/clip-sets/${clipSetId}`);
}

export async function updateClipCandidate(
  candidateId: string,
  data: {
    title: string;
    start: number;
    end: number;
    selected: boolean;
    expected_revision: number;
    confirm_current_source?: boolean;
  },
): Promise<ClipSet> {
  return request(`/api/clip-candidates/${candidateId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function updateClipLayout(
  candidateId: string,
  aspectRatio: ClipAspectRatio,
  data: {
    enabled: boolean;
    composition: 'blur' | 'crop';
    focal_x: number;
    focal_y: number;
    subtitle_mode: 'off' | 'original' | 'translated' | 'bilingual';
    style: Record<string, unknown>;
    expected_revision: number;
  },
): Promise<ClipSet> {
  return request(
    `/api/clip-candidates/${candidateId}/layouts/${encodeURIComponent(aspectRatio)}`,
    {
      method: 'PUT',
      body: JSON.stringify(data),
    },
  );
}

export async function renderClips(
  projectId: string,
  data: {
    items: Array<{ candidate_id: string; aspect_ratio: ClipAspectRatio }>;
    confirm_stale?: boolean;
  },
): Promise<{ task_id: string | null; render_ids: string[]; reused: boolean }> {
  return request(`/api/projects/${projectId}/clip-renders`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getClipRender(renderId: string): Promise<ClipRender> {
  return request(`/api/clip-renders/${renderId}`);
}

export async function downloadClipRender(
  renderId: string,
  filename = 'short-clip.mp4',
): Promise<boolean> {
  const render = isTauriDesktop() ? await getClipRender(renderId) : undefined;
  return downloadBackendFile(
    `/api/clip-renders/${renderId}/download`,
    filename,
    render?.path || undefined,
  );
}

export async function deleteClipRender(renderId: string): Promise<void> {
  await request(`/api/clip-renders/${renderId}?confirm=true`, { method: 'DELETE' });
}
