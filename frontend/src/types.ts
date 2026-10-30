// 字幕工厂（简洁版）- 类型定义

export interface Project {
  id: string;
  title: string;
  source_type: 'youtube' | 'local';
  source_url: string | null;
  language: string;
  target_language: string;
  created_at: string;
  updated_at: string;
  segments_count: number;
  latest_task_status?: string | null;
}

export interface SubtitleSegment {
  id: string;
  index: number;
  start: number;
  end: number;
  raw_text: string;
  clean_text: string;
  translated_text: string;
}

export type TaskState = 'pending' | 'running' | 'paused' | 'success' | 'failed' | 'cancelled' | 'partial';

export interface TaskStatus {
  id: string;
  project_id: string;
  type: string;
  status: TaskState;
  step: string;
  progress: number;
  message: string;
  error: string | null;
  suggestion?: string | null;
  details?: { stages?: Record<string, string>; [key: string]: unknown };
}

export interface ModelRuntime {
  id: string;
  name: string;
  available: boolean;
  model_ready?: boolean;
  download_required?: boolean;
  download_bytes?: number;
  source?: string;
}

export interface TranscriptionModel {
  id: string;
  name: string;
  ready: boolean;
  category_name?: string;
  size_label?: string;
  selected_runtime?: string | null;
  runtimes?: ModelRuntime[];
}

export interface AIProviderCard {
  provider_id: string;
  name: string;
  base_url: string;
  model: string;
  models: string[];
  has_api_key: boolean;
  supports_thinking: boolean;
  thinking_enabled: boolean;
}

export interface AIProvidersResponse {
  providers: AIProviderCard[];
  assignments: { clean_provider_id: string; translate_provider_id: string };
}

export type ExportFormat = 'original' | 'translated' | 'bilingual';
