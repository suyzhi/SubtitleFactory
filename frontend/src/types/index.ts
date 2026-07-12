// 字幕工厂 - TypeScript 类型定义

export interface Project {
  id: string;
  title: string;
  source_type: 'youtube' | 'local';
  source_url: string | null;
  video_path: string | null;
  thumbnail_url: string | null;
  group_name: string | null;
  audio_path: string | null;
  language: string;
  target_language: string;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
  segments_count: number;
  status?: string;
}

export interface SubtitleSegment {
  id: string;
  project_id: string;
  index: number;
  start: number;
  end: number;
  raw_text: string;
  clean_text: string;
  translated_text: string;
  speaker: string;
  locked: boolean;
  is_draft: boolean;
  source_stage: string;
}

// ── Enhanced Task System ──

export type TaskStepStatus = 'waiting' | 'running' | 'paused' | 'success' | 'failed' | 'cancelled' | 'partial' | 'skipped';

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

// ── Process Timeline ──

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

export interface ProcessState {
  current_step_id: string | null;
  total_progress: number;
  steps: ProcessStep[];
  logs: ProcessLogEntry[];
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

// ── Subtitle Style ──

export type SubtitleDisplayMode =
  | 'off'
  | 'original'
  | 'translated'
  | 'bilingual_original_first'
  | 'bilingual_translated_first';

export interface SubtitleStyleSettings {
  mode: SubtitleDisplayMode;
  verticalPosition: number;
  /** @deprecated 兼容旧版持久化配置，界面改用下面两个独立字号。 */
  fontSize: number;
  originalFontSize: number;
  translatedFontSize: number;
  fontFamily: string;
  originalTextColor: string;
  translatedTextColor: string;
  /** @deprecated 兼容旧版单色配置。 */
  textColor: string;
  backgroundMode: 'none' | 'black' | 'white';
  shadow: boolean;
  maxWidth: number;
  lineGap: number;
}

// ── Subtitle Stats ──

export interface SubtitleStats {
  totalSegments: number;
  audioDuration?: number;
  averageDuration?: number;
  minDuration?: number;
  maxDuration?: number;
  mergedShortSegments?: number;
  splitLongSegments?: number;
  tooShortCount?: number;
  tooLongCount?: number;
}

// ── Original Types (unchanged) ──

export interface ProjectCreate {
  source_type: 'youtube' | 'local';
  source_url?: string;
  title?: string;
  language?: string;
  target_language?: string;
}

export interface SegmentUpdate {
  clean_text?: string;
  translated_text?: string;
  locked?: boolean;
}

export interface ExportRequest {
  format: 'srt' | 'vtt' | 'ass' | 'srt-bilingual' | 'mp4' | 'mkv';
  bilingual: boolean;
  primary_language: 'original' | 'translated';
}

export interface ProcessingConfig {
  model: ModelSize;
  language: SourceLang;
  target_language: TargetLang;
  enable_clean: boolean;
  enable_translate: boolean;
  bilingual: boolean;
  clean_target_length: number;
}

export type ModelSize = 'auto' | 'small' | 'medium' | 'large-v3' | 'parakeet-tdt-0.6b-v3-coreml' | 'parakeet-tdt-0.6b-v3-int8' | (string & {});
/** BCP-47-ish language code. Kept open so the UI can add languages without a release. */
export type SourceLang = string;
export type TargetLang = string;
export type ExportFormat = 'srt' | 'vtt' | 'ass' | 'srt-bilingual' | 'mp4' | 'mkv';

export interface AIProviderPreset {
  id: string;
  name: string;
  base_url: string;
  model: string;
  models: string[];
}

export interface AISettings {
  provider: string;
  base_url: string;
  api_key: string;
  has_api_key?: boolean;
  model: string;
  updated_at?: string;
  last_test_status?: '' | 'success' | 'failed';
  last_test_at?: string;
  last_latency_ms?: number;
}

export type RuntimeSource = 'bundled' | 'app_download' | 'external_detected' | 'custom' | 'environment' | 'path' | 'unavailable' | string;

export interface RuntimeCheck {
  ok?: boolean;
  available?: boolean;
  status?: string;
  source?: RuntimeSource;
  path?: string | null;
  resolved_path?: string | null;
  message?: string | null;
  reason?: string | null;
  version?: string | null;
  free_bytes?: number;
  total_bytes?: number;
  [key: string]: unknown;
}

export interface RuntimeHealth {
  ffmpeg?: RuntimeCheck;
  yt_dlp?: RuntimeCheck;
  disk?: RuntimeCheck;
  output_directory?: RuntimeCheck;
  models?: RuntimeCheck | RuntimeCheck[] | Record<string, RuntimeCheck>;
  data_directory?: string;
}

export interface HealthStatus {
  status: string;
  service: string;
  version: string;
  runtime?: RuntimeHealth;
}

export interface AppSettings {
  default_model?: string;
  source_language?: string;
  translation_target_language?: string;
  default_workflow?: 'automatic' | 'manual' | string;
  auto_save?: boolean;
  startup_behavior?: 'restore_last' | 'project_library';
  download_quality?: string;
  download_container?: 'mp4' | 'mkv' | 'webm';
  download_directory?: string;
  ffmpeg_path?: string;
  yt_dlp_path?: string;
  custom_model_path?: string;
  coreml_model_path?: string;
  coreml_cli_path?: string;
  bilingual_order?: 'original_first' | 'translated_first' | string;
  favorite_languages?: string[];
  clean_provider_id?: string;
  translate_provider_id?: string;
  transcription_runtime_by_model?: Record<string, string>;
  [key: string]: unknown;
}

export interface AppSettingsResponse {
  settings: AppSettings;
  warnings?: AppSettingWarning[];
}

export interface AppSettingWarning {
  field: string;
  code: string;
  message: string;
  fallback?: unknown;
}

export interface PathValidationResult {
  ok: boolean;
  kind: 'ffmpeg' | 'yt_dlp' | 'model' | 'coreml_model' | 'cli' | 'download_directory';
  path: string;
  resolved_path?: string | null;
  reason?: string | null;
  details?: Record<string, unknown>;
}
