// 项目、字幕段、编辑操作与导出相关类型

export interface Project {
  id: string;
  title: string;
  source_type: 'youtube' | 'local';
  source_url: string | null;
  video_path: string | null;
  video_url?: string | null;
  thumbnail_url: string | null;
  thumbnail_access_url?: string | null;
  group_name: string | null;
  audio_path: string | null;
  language: string;
  target_language: string;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
  segments_count: number;
  edit_revision?: number;
  media_status?: string;
  media_mode: 'local' | 'web';
  youtube_video_id: string | null;
  video_available: boolean;
  audio_available: boolean;
  status?: string;
  latest_task_status?: string | null;
  latest_task_message?: string | null;
}

export interface ProjectCreate {
  source_type: 'youtube' | 'local';
  source_url?: string;
  title?: string;
  language?: string;
  target_language?: string;
  media_mode?: 'local' | 'web';
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
  speaker_id?: string | null;
  locked: boolean;
  is_draft: boolean;
  source_stage: string;
}

export interface SegmentUpdate {
  start?: number;
  end?: number;
  clean_text?: string;
  translated_text?: string;
  speaker_id?: string | null;
  locked?: boolean;
}

export type SegmentOperationKind =
  | 'update_many'
  | 'replace'
  | 'shift'
  | 'split'
  | 'merge'
  | 'assign_speaker'
  | 'delete';

export interface SegmentOperationRequest {
  expected_revision: number;
  operation: SegmentOperationKind;
  items?: Array<SegmentUpdate & { index: number }>;
  indices?: number[];
  include_locked?: boolean;
  search?: string;
  replacement?: string;
  fields?: Array<'clean_text' | 'translated_text'>;
  match_case?: boolean;
  delta?: number;
  split_index?: number;
  split_at?: number;
  text_offset?: number;
  speaker_id?: string | null;
}

export interface EditorOperationResponse {
  revision: number;
  operation_id?: string | null;
  operation: string;
  affected_count: number;
  segments: SubtitleSegment[];
}

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

export interface ExportRequest {
  format: 'srt' | 'vtt' | 'ass' | 'srt-bilingual' | 'mp4' | 'mkv';
  bilingual: boolean;
  primary_language: 'original' | 'translated';
  style?: SubtitleStyleSettings;
}

export type ModelSize =
  | 'auto'
  | 'small'
  | 'medium'
  | 'large-v3'
  | 'parakeet-tdt-0.6b-v3-coreml'
  | 'parakeet-tdt-0.6b-v3-int8'
  | (string & {});

/** BCP-47-ish language code. Kept open so the UI can add languages without a release. */
export type SourceLang = string;
export type TargetLang = string;
export type ExportFormat = 'srt' | 'vtt' | 'ass' | 'srt-bilingual' | 'mp4' | 'mkv';

export interface ProcessingConfig {
  model: ModelSize;
  language: SourceLang;
  target_language: TargetLang;
  enable_clean: boolean;
  enable_translate: boolean;
  bilingual: boolean;
  clean_target_length: number;
}
