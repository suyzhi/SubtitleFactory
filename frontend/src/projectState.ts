import type { Project, TaskStatus } from './types';

export function taskIsActive(task: TaskStatus | null | undefined): boolean {
  return !!task && (['pending','running','paused'].includes(task.status)
    || (task.status === 'cancelled' && !!task.details?.cancel_requested_at && !task.details?.worker_stopped));
}

export function projectReadiness(project: Project): string {
  if (project.segments_count > 0) return project.video_available ? '可校对 · 可导出' : '字幕可导出 · 缺少画面素材';
  if (!project.video_available && !project.audio_available) return '待准备素材';
  return '待转写';
}
export function taskLabel(status?: string | null): string {
  return ({pending:'排队中',running:'处理中',paused:'已暂停',success:'已完成',failed:'失败，可查看原因',partial:'部分完成',cancelled:'已取消'} as Record<string,string>)[status || ''] || '';
}
export function taskProgressLabel(task: TaskStatus): string {
  if (task.status === 'cancelled' && taskIsActive(task)) return '正在停止识别并保存草稿，请稍候…';
  if (task.step === 'loading_model' && task.status === 'running') return '正在加载模型';
  const seconds = Number(task.details?.current_time);
  const duration = Number(task.details?.audio_duration);
  const format = (value:number) => `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2,'0')}`;
  if (Number.isFinite(seconds) && duration > 0 && task.status === 'running' && task.step === 'transcribing') return `已识别 ${format(seconds)} / ${format(duration)}`;
  return task.message || taskLabel(task.status);
}
