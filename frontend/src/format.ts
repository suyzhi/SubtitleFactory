import type { ModelRuntime, TaskStatus, TranscriptionModel } from './types';

export const ACTIVE_TASK_STATES = new Set(['pending', 'running', 'paused']);

export const isTaskActive = (task: TaskStatus | null | undefined): boolean => Boolean(task && ACTIVE_TASK_STATES.has(task.status));

export const STAGE_LABELS: Record<string, string> = {
  download: '获取视频',
  transcribe: '转写',
  clean: 'AI 整理',
  translate: 'AI 翻译',
};

export const TASK_STATE_LABELS: Record<string, string> = {
  pending: '排队中',
  running: '处理中',
  paused: '已暂停',
  success: '已完成',
  failed: '失败',
  cancelled: '已停止',
  partial: '部分完成',
};

export function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(s).padStart(2, '0');
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function formatBytes(bytes?: number): string {
  if (!bytes) return '';
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  return `${Math.round(bytes / 1024 ** 2)} MB`;
}

const RUNTIME_PREFERENCE = ['mlx', 'external_coreml', 'coreml', 'cpu'];

export function localRuntimes(model: TranscriptionModel | undefined): ModelRuntime[] {
  return (model?.runtimes || []).filter(runtime => runtime.available && !runtime.id.endsWith('_cloud'));
}

/** 优先使用上次选择、已下载的设备，其次 Apple 芯片加速，最后 CPU。 */
export function defaultRuntime(model: TranscriptionModel | undefined): string {
  const runtimes = localRuntimes(model);
  const remembered = runtimes.find(runtime => runtime.id === model?.selected_runtime);
  if (remembered) return remembered.id;
  const ready = runtimes.find(runtime => runtime.model_ready);
  if (ready) return ready.id;
  const ranked = [...runtimes].sort((a, b) => rank(a.id) - rank(b.id));
  return ranked[0]?.id || '';
}

function rank(id: string): number {
  const index = RUNTIME_PREFERENCE.indexOf(id);
  return index === -1 ? RUNTIME_PREFERENCE.length : index;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
