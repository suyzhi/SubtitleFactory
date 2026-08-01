import type { TaskStatus } from './types';

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

export function recoveryActionLabel(task: TaskStatus | null): string {
  if (!isDownloadFailure(task)) return '使用备用模型重试';
  if (task?.type === 'prepare_audio') return '重新准备网页音频';
  if (task?.type === 'workflow') return '重新启动自动工作流';
  return '重新下载';
}
