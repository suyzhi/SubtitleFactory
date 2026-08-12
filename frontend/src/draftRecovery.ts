import type { SegmentUpdate } from './types';

const STORAGE_PREFIX = 'subtitle_factory_segment_draft_v1:';

export interface RecoveredSegmentDraft {
  baseRevision: number;
  items: Record<number, SegmentUpdate>;
  updatedAt: number;
}

function storageKey(projectId: string) {
  return `${STORAGE_PREFIX}${encodeURIComponent(projectId)}`;
}

function validUpdate(value: unknown): value is SegmentUpdate & { index: number } {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  const keys = Object.keys(item);
  const allowed = new Set([
    'index', 'start', 'end', 'clean_text', 'translated_text', 'speaker_id', 'locked',
  ]);
  if (keys.length < 2 || keys.some(key => !allowed.has(key))) return false;
  if (!Number.isInteger(item.index) || Number(item.index) < 1) return false;
  if (item.start !== undefined && (typeof item.start !== 'number' || item.start < 0)) return false;
  if (item.end !== undefined && (typeof item.end !== 'number' || item.end <= 0)) return false;
  if (item.clean_text !== undefined && typeof item.clean_text !== 'string') return false;
  if (item.translated_text !== undefined && typeof item.translated_text !== 'string') return false;
  if (item.speaker_id !== undefined && item.speaker_id !== null && typeof item.speaker_id !== 'string') return false;
  if (item.locked !== undefined && typeof item.locked !== 'boolean') return false;
  return true;
}

export function readRecoveredSegmentDraft(projectId: string): RecoveredSegmentDraft | null {
  const key = storageKey(projectId);
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (
      value.version !== 1
      || !Number.isInteger(value.baseRevision)
      || Number(value.baseRevision) < 0
      || !Number.isFinite(value.updatedAt)
      || !Array.isArray(value.items)
      || !value.items.length
      || !value.items.every(validUpdate)
    ) {
      localStorage.removeItem(key);
      return null;
    }
    return {
      baseRevision: Number(value.baseRevision),
      updatedAt: Number(value.updatedAt),
      items: Object.fromEntries(value.items.map(rawItem => {
        const { index, ...update } = rawItem as SegmentUpdate & { index: number };
        return [index, update];
      })),
    };
  } catch {
    try { localStorage.removeItem(key); } catch { /* Storage is unavailable. */ }
    return null;
  }
}

export function writeRecoveredSegmentDraft(
  projectId: string,
  baseRevision: number,
  items: Record<number, SegmentUpdate>,
): boolean {
  try {
    const serializedItems = Object.entries(items).map(([index, update]) => ({
      index: Number(index),
      ...update,
    }));
    localStorage.setItem(storageKey(projectId), JSON.stringify({
      version: 1,
      baseRevision,
      items: serializedItems,
      updatedAt: Date.now(),
    }));
    return true;
  } catch {
    return false;
  }
}

export function clearRecoveredSegmentDraft(projectId: string): void {
  try {
    localStorage.removeItem(storageKey(projectId));
  } catch { /* Browser privacy settings may make storage unavailable. */ }
}
