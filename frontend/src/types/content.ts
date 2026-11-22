// 质检、全文检索、内容发布包与短视频切片类型

export interface QualityIssue {
  id: string;
  rule_id: string;
  segment_id?: string | null;
  segment_index?: number | null;
  severity: 'error' | 'warning' | 'info';
  message: string;
  suggestion: string;
  status: 'open' | 'ignored' | 'resolved';
  details?: Record<string, unknown>;
}

export interface SegmentSearchHit {
  segment_id: string;
  project_id: string;
  project_title: string;
  playlist_title?: string | null;
  group_name?: string | null;
  source_type: 'youtube' | 'local';
  source_language: string;
  target_language: string;
  created_at: string;
  updated_at: string;
  segment_index: number;
  start: number;
  end: number;
  speaker_id?: string | null;
  speaker_name: string;
  snippet: string;
  match_fields: string[];
  rank: number;
}

export type ContentPackInputMode = 'original' | 'translated' | 'bilingual';

export interface ContentSection {
  id: string;
  pack_id: string;
  kind: 'chapters' | 'summary' | 'quotes' | 'youtube' | 'podcast' | 'social';
  title: string;
  content: Record<string, any>;
  status: 'pending' | 'generating' | 'ready' | 'failed';
  error?: string | null;
  sort_order: number;
  revision: number;
  generated_at?: string | null;
  updated_at: string;
}

export interface ContentPack {
  id: string;
  project_id: string;
  name: string;
  input_mode: ContentPackInputMode;
  output_language: string;
  allow_translation_fallback?: boolean | number;
  source_revision: number;
  current_project_revision: number;
  source_fingerprint: string;
  provider_id?: string | null;
  model?: string | null;
  status: 'pending' | 'generating' | 'ready' | 'partial' | 'failed';
  revision: number;
  stale: boolean;
  failed_sections?: number;
  created_at: string;
  updated_at: string;
  sections?: ContentSection[];
}

export type ClipAspectRatio = '9:16' | '1:1' | '16:9';

export interface ClipLayout {
  candidate_id: string;
  aspect_ratio: ClipAspectRatio;
  enabled: boolean;
  composition: 'blur' | 'crop';
  focal_x: number;
  focal_y: number;
  subtitle_mode: 'off' | 'original' | 'translated' | 'bilingual';
  style: Record<string, any>;
  revision: number;
  updated_at: string;
}

export interface ClipRender {
  id: string;
  project_id: string;
  candidate_id: string;
  aspect_ratio: ClipAspectRatio;
  configuration_fingerprint: string;
  task_id?: string | null;
  path?: string | null;
  status: 'pending' | 'running' | 'success' | 'failed' | 'cancelled';
  error?: string | null;
  width?: number | null;
  height?: number | null;
  duration?: number | null;
  size?: number | null;
  checksum?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ClipCandidate {
  id: string;
  clip_set_id: string;
  title: string;
  hook: string;
  reason: string;
  score: number;
  start: number;
  end: number;
  start_segment_index: number;
  end_segment_index: number;
  selected: boolean;
  revision: number;
  source_confirmed_revision?: number | null;
  stale: boolean;
  layouts: ClipLayout[];
  renders: ClipRender[];
}

export interface ClipSet {
  id: string;
  project_id: string;
  project_title?: string;
  name: string;
  source_revision: number;
  current_project_revision: number;
  source_fingerprint: string;
  provider_id?: string | null;
  model?: string | null;
  desired_count: 3 | 5 | 10;
  min_duration: number;
  max_duration: number;
  status: 'pending' | 'ready' | 'failed';
  stale: boolean;
  candidate_count?: number;
  candidates?: ClipCandidate[];
  created_at: string;
  updated_at: string;
}
