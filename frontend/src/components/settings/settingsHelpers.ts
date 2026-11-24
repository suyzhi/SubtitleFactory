import type * as api from '../../api/backend';

export type SettingsCategory =
  | 'general'
  | 'transcription'
  | 'ai'
  | 'translation'
  | 'storage'
  | 'appearance'
  | 'about';

export const SETTINGS_CATEGORIES: { id: SettingsCategory; icon: string; label: string }[] = [
  { id: 'general', icon: '⌂', label: '通用' },
  { id: 'transcription', icon: '⌁', label: '转写' },
  { id: 'ai', icon: '✦', label: 'AI 服务' },
  { id: 'translation', icon: '文', label: '翻译' },
  { id: 'storage', icon: '⇩', label: '下载与存储' },
  { id: 'appearance', icon: '◐', label: '外观与动画' },
  { id: 'about', icon: 'ⓘ', label: '快捷键与关于' },
];

export interface ModelStatusResult {
  recommended_model: string;
  recommendation_reason?: string;
  category_order?: string[];
  models: api.TranscriptionModelStatus[];
}

export function bytes(value?: number) {
  if (!value && value !== 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let current = value;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current >= 10 || index === 0 ? current.toFixed(0) : current.toFixed(1)} ${units[index]}`;
}

export function backupKind(kind: string) {
  return (
    (
      {
        manual: '手动',
        daily: '每日',
        weekly: '每周',
        pre_restore: '恢复前安全',
        schema: '数据迁移',
      } as Record<string, string>
    )[kind] || '系统'
  );
}

export function runtimeCopy(item: Record<string, unknown> | undefined) {
  if (!item) return { ok: false, title: '尚未检查', detail: '打开设置后刷新运行状态' };
  const ok = Boolean(item.ok ?? item.available ?? item.status === 'ready');
  const title = String(item.message || item.status || (ok ? '可用' : '不可用'));
  const detail =
    item.source === 'bundled'
      ? 'App 内置'
      : String(item.path || item.resolved_path || item.source || '');
  return { ok, title, detail };
}
