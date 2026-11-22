// 应用设置、AI 服务商配置与运行时诊断类型

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

export type RuntimeSource =
  | 'bundled'
  | 'app_download'
  | 'external_detected'
  | 'custom'
  | 'environment'
  | 'path'
  | 'unavailable'
  | string;

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
  ffprobe?: RuntimeCheck;
  yt_dlp?: RuntimeCheck;
  deno?: RuntimeCheck;
  ejs?: RuntimeCheck;
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
  youtube_media_mode?: 'local' | 'web';
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

export interface AppSettingWarning {
  field: string;
  code: string;
  message: string;
  fallback?: unknown;
}

export interface AppSettingsResponse {
  settings: AppSettings;
  warnings?: AppSettingWarning[];
}

export interface PathValidationResult {
  ok: boolean;
  kind: 'ffmpeg' | 'yt_dlp' | 'model' | 'coreml_model' | 'cli' | 'download_directory';
  path: string;
  resolved_path?: string | null;
  reason?: string | null;
  details?: Record<string, unknown>;
}
