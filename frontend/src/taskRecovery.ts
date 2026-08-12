import type { TaskStatus } from './types';

export type TaskRecoveryAction =
  | 'download'
  | 'workflow'
  | 'transcription'
  | 'extract_audio'
  | 'clean'
  | 'translate'
  | 'render'
  | 'smart_tools'
  | 'content'
  | 'settings'
  | 'process';

const DOWNLOAD_CODES = new Set([
  'AUTH_REQUIRED', 'MEMBERSHIP_REQUIRED', 'PRIVATE_VIDEO', 'AGE_RESTRICTED',
  'GEO_RESTRICTED', 'VIDEO_REMOVED', 'DRM_PROTECTED', 'RATE_LIMITED',
  'COOKIE_ACCESS_FAILED', 'PO_TOKEN_REQUIRED', 'MEDIA_ACCESS_DENIED',
  'NETWORK_TEMPORARY', 'DISK_FULL', 'OUTPUT_PERMISSION_DENIED',
  'FORMAT_UNAVAILABLE', 'MERGE_FAILED', 'DOWNLOAD_RUNTIME_MISSING',
  'DOWNLOAD_FAILED',
]);

export function isDownloadFailure(task: TaskStatus | null): boolean {
  if (!task) return false;
  if (['download', 'prepare_audio', 'materialize_video', 'switch_media_mode'].includes(task.type)) {
    return true;
  }
  const stage = String(task.details?.download?.failure_stage || task.step || '');
  return task.type === 'workflow' && (
    stage.includes('download') || DOWNLOAD_CODES.has(String(task.error_code || ''))
  );
}

export function isTranscriptionFailure(task: TaskStatus | null): boolean {
  if (!task || isDownloadFailure(task)) return false;
  return task.type === 'transcribe'
    || /MODEL|TRANSCRIB|MLX|COREML|RUNTIME_SELECTION/.test(String(task.error_code || ''));
}

export function recoveryAction(task: TaskStatus | null): TaskRecoveryAction {
  if (!task) return 'process';
  if (['download', 'prepare_audio', 'materialize_video', 'switch_media_mode'].includes(task.type)) {
    return 'download';
  }
  if (task.type === 'workflow') {
    if (task.error_code !== 'APP_INTERRUPTED' && isTranscriptionFailure(task)) {
      return 'transcription';
    }
    return 'workflow';
  }
  if (task.type === 'transcribe') return 'transcription';
  if (task.type === 'extract_audio') return 'extract_audio';
  if (task.type === 'clean') return 'clean';
  if (task.type === 'translate') return 'translate';
  if (task.type === 'render' || task.type === 'export') return 'render';
  if (['ocr', 'speaker_diarization'].includes(task.type)) return 'smart_tools';
  if (['content_generate', 'clip_recommend', 'clip_render_batch'].includes(task.type)) return 'content';
  if (['prepare_model', 'prepare_speaker_models'].includes(task.type)) return 'settings';
  if (isTranscriptionFailure(task)) return 'transcription';
  return 'process';
}

export function recoveryActionLabel(task: TaskStatus | null): string {
  const action = recoveryAction(task);
  if (action === 'download') {
    if (task?.type === 'prepare_audio') return '重新准备网页音频';
    if (task?.type === 'materialize_video') return '重新下载本地副本';
    if (task?.type === 'switch_media_mode') return '重新下载并切换';
    return '重新下载';
  }
  if (action === 'workflow') return '重新启动自动工作流';
  if (action === 'transcription') {
    return task?.error_code === 'APP_INTERRUPTED' ? '重新开始转写' : '使用备用模型重试';
  }
  if (action === 'extract_audio') return '重新提取音频';
  if (action === 'clean') return '重新开始 AI 整理';
  if (action === 'translate') return '重新开始翻译';
  if (action === 'render') return '重新导出成片';
  if (action === 'smart_tools') return '打开智能工具重新开始';
  if (action === 'content') return '打开内容工作区重新开始';
  if (action === 'settings') return '打开设置检查并重试';
  return '打开处理流程检查';
}
