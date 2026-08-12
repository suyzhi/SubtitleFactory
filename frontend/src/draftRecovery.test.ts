import { beforeEach, describe, expect, it } from 'vitest';

import {
  clearRecoveredSegmentDraft,
  readRecoveredSegmentDraft,
  writeRecoveredSegmentDraft,
} from './draftRecovery';

describe('local subtitle draft recovery', () => {
  beforeEach(() => localStorage.clear());

  it('round-trips an isolated project draft and clears it explicitly', () => {
    expect(writeRecoveredSegmentDraft('project/a', 7, {
      2: { clean_text: '保留我', speaker_id: null },
      4: { start: 3.25, locked: true },
    })).toBe(true);
    expect(readRecoveredSegmentDraft('another-project')).toBeNull();
    expect(readRecoveredSegmentDraft('project/a')).toMatchObject({
      baseRevision: 7,
      items: {
        2: { clean_text: '保留我', speaker_id: null },
        4: { start: 3.25, locked: true },
      },
    });

    clearRecoveredSegmentDraft('project/a');
    expect(readRecoveredSegmentDraft('project/a')).toBeNull();
  });

  it('refuses malformed recovery payloads', () => {
    localStorage.setItem(
      'subtitle_factory_segment_draft_v1:broken',
      JSON.stringify({ version: 1, baseRevision: 0, updatedAt: Date.now(), items: [{ index: 0 }] }),
    );
    expect(readRecoveredSegmentDraft('broken')).toBeNull();
    expect(localStorage.getItem('subtitle_factory_segment_draft_v1:broken')).toBeNull();
  });
});
