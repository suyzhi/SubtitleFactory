// 任务系统、流程时间线与播放列表批处理类型

import type { Project } from './project';

export type PlaylistStageName = 'download' | 'extract_audio' | 'transcribe' | 'clean' | 'translate';
export type PlaylistStageStatus =
  | 'waiting'
  | 'queued'
  | 'running'
  | 'paused'
  | 'success'
  | 'partial'
  | 'failed'
  | 'blocked'
  | 'cancelled'
  | 'skipped';

export interface PlaylistPreviewItem {
  source_id: string;
  video_id: string | null;
  position: number;
  title: string;
  url: string | null;
  duration: number;
  thumbnail_url: string | null;
  availability: 'active' | 'permission_required' | 'unavailable';
  error_code?: string | null;
  suggestion?: string | null;
}

export interface PlaylistPreview {
  playlist: {
    id: string;
    title: string;
    url: string;
    channel: string;
    thumbnail_url: string | null;
    item_count: number;
    available_count: number;
    unavailable_count: number;
    total_duration: number;
  };
  items: PlaylistPreviewItem[];
  warnings: string[];
}

export interface PlaylistBatchStage {
  status: PlaylistStageStatus;
  task_id: string | null;
  attempt: number;
  error_code: string | null;
  error: string | null;
  progress: number;
}

export interface PlaylistBatchItem {
  id: string;
  project_id: string | null;
  source_id: string;
  source_url: string | null;
  position: number;
  title: string;
  duration: number;
  thumbnail_url: string | null;
  source_state: 'active' | 'removed' | 'permission_required' | 'unavailable';
  status: string;
  error: string | null;
  project: Project | null;
  stages: Partial<Record<PlaylistStageName, PlaylistBatchStage>>;
}

export interface PlaylistBatchSummary {
  id: string;
  name: string;
  title: string;
  status: string;
  source_url: string;
  source_external_id: string;
  channel: string;
  thumbnail_url: string | null;
  paused: number | boolean;
  configuration: Record<string, any>;
  item_count: number;
  completed_count: number;
  failed_count: number;
  progress: number;
  updated_at: string;
}

export interface PlaylistBatchDetail {
  batch: PlaylistBatchSummary;
  items: PlaylistBatchItem[];
}

export type TaskStepStatus =
  | 'waiting'
  | 'running'
  | 'paused'
  | 'success'
  | 'failed'
  | 'cancelled'
  | 'partial'
  | 'skipped';

export interface TaskDetails {
  current_batch?: number;
  total_batches?: number;
  processed_segments?: number;
  total_segments?: number;
  failed_batches?: number;
  retry_count?: number;
  model?: string;
  device?: string;
  audio_duration?: number;
  merged_short?: number;
  split_long?: number;
  min_duration?: number;
  max_duration?: number;
  avg_duration?: number;
  too_short_count?: number;
  too_long_count?: number;
  video_path?: string;
  subtitle_format?: string;
  output_path?: string;
  output_size?: number;
  ffmpeg_progress?: number;
  [key: string]: any;
}

export interface TaskLogEntry {
  time: string;
  level: 'info' | 'warning' | 'error';
  step: string;
  message: string;
  detail?: string;
  suggestion?: string;
}

export interface TaskStatus {
  id: string;
  project_id: string;
  type: string;
  status: 'pending' | 'running' | 'paused' | 'success' | 'failed' | 'cancelled' | 'partial';
  step: string;
  step_name?: string;
  progress: number;
  message: string;
  details?: TaskDetails;
  logs?: TaskLogEntry[];
  error: string | null;
  suggestion?: string | null;
  created_at: string;
  updated_at: string;
  error_code?: string | null;
  recoverable?: boolean;
  available_actions?: string[];
  parent_task_id?: string | null;
  attempt?: number;
}

export interface FailedCleanBatch {
  batch_index: number;
  segment_count: number;
  start: number | null;
  end: number | null;
  attempts: number;
  error: string;
  updated_at: string;
}

export interface ProcessStep {
  id: string;
  name: string;
  description: string;
  status: TaskStepStatus;
  progress: number;
  started_at?: string;
  finished_at?: string;
  error?: string;
  suggestion?: string;
  details?: Record<string, any>;
}

export interface ProcessLogEntry {
  id: string;
  time: string;
  level: 'info' | 'warning' | 'error';
  step: string;
  message: string;
  detail?: string;
  suggestion?: string;
}

export interface ProcessState {
  current_step_id: string | null;
  total_progress: number;
  steps: ProcessStep[];
  logs: ProcessLogEntry[];
}
