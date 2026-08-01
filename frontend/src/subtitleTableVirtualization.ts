import type { SubtitleSegment } from './types';

export const SUBTITLE_VIRTUALIZE_THRESHOLD = 240;
const SUBTITLE_WINDOW_OVERSCAN_PX = 720;

export function findSubtitleFocusIndex(segments: SubtitleSegment[], currentTime: number): number {
  if (!segments.length) return -1;
  const active = segments.findLast(segment => segment.start <= currentTime && segment.end >= currentTime);
  if (active) return active.index;
  return segments.find(segment => segment.start > currentTime)?.index ?? segments.at(-1)?.index ?? -1;
}

export function buildSubtitleRowOffsets(
  segments: SubtitleSegment[],
  measuredHeights: ReadonlyMap<string, number>,
  estimatedHeight: number,
): number[] {
  const offsets = new Array<number>(segments.length + 1);
  offsets[0] = 0;
  for (let position = 0; position < segments.length; position += 1) {
    offsets[position + 1] = offsets[position]
      + (measuredHeights.get(segments[position].id) ?? estimatedHeight);
  }
  return offsets;
}

function subtitleRowAtOffset(offsets: number[], target: number): number {
  const rowCount = Math.max(0, offsets.length - 1);
  if (!rowCount) return 0;
  let low = 0;
  let high = rowCount;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (offsets[middle + 1] <= target) low = middle + 1;
    else high = middle;
  }
  return Math.min(rowCount - 1, low);
}

export function getSubtitleWindowRange(
  offsets: number[],
  scrollTop: number,
  viewportHeight: number,
  overscan = SUBTITLE_WINDOW_OVERSCAN_PX,
): { start: number; end: number } {
  const rowCount = Math.max(0, offsets.length - 1);
  if (!rowCount) return { start: 0, end: 0 };
  const start = subtitleRowAtOffset(offsets, Math.max(0, scrollTop - overscan));
  const end = Math.min(
    rowCount,
    subtitleRowAtOffset(offsets, Math.max(0, scrollTop + viewportHeight + overscan)) + 1,
  );
  return { start, end: Math.max(start + 1, end) };
}
