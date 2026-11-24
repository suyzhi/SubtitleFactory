import type { TaskStatus } from '../../types';

export type ContentTab = 'packs' | 'clips';
export type SaveState = 'idle' | 'saving' | 'saved' | 'error';

export const TERMINAL_TASKS = new Set<TaskStatus['status']>([
  'success',
  'failed',
  'cancelled',
  'partial',
]);

export function timecode(value: number) {
  const seconds = Math.max(0, Number(value) || 0);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = Math.floor(seconds % 60);
  const tenths = Math.floor((seconds % 1) * 10);
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}.${tenths}`
    : `${minutes}:${String(remainder).padStart(2, '0')}.${tenths}`;
}

export function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason);
}
