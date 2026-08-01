// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Project, SubtitleSegment } from './types';
import { SubtitleTable } from './App';
import { deriveProcessSteps } from './processSteps';
import {
  buildSubtitleRowOffsets,
  findSubtitleFocusIndex,
  getSubtitleWindowRange,
} from './subtitleTableVirtualization';

vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: vi.fn(),
}));

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const scrollTo = vi.fn(function scrollTo(
  this: HTMLElement,
  options: ScrollToOptions,
) {
  this.scrollTop = Number(options.top || 0);
});

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', TestResizeObserver);
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
    configurable: true,
    value: scrollTo,
  });
});

beforeEach(() => {
  scrollTo.mockClear();
});

function makeSegment(index: number, overrides: Partial<SubtitleSegment> = {}): SubtitleSegment {
  return {
    id: `segment-${index}`,
    project_id: 'project-1',
    index,
    start: (index - 1) * 2,
    end: index * 2,
    raw_text: `Original subtitle ${index}`,
    clean_text: '',
    translated_text: `翻译字幕 ${index}`,
    speaker: '',
    locked: false,
    is_draft: false,
    source_stage: 'transcribe',
    ...overrides,
  };
}

function tableProps(segments: SubtitleSegment[]) {
  return {
    segments,
    currentTime: 0,
    activeIdx: -1,
    onSeek: vi.fn(),
    onUpdate: vi.fn(),
    onReplaceAll: vi.fn().mockResolvedValue(undefined),
    onSplit: vi.fn().mockResolvedValue(undefined),
    onMerge: vi.fn().mockResolvedValue(undefined),
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    saveState: 'idle' as const,
    draftCount: 0,
    onCommitDraft: vi.fn(),
    onDiscardDraft: vi.fn(),
    onAutoScrollChange: vi.fn(),
    autoScroll: false,
  };
}

describe('subtitle table layout and positioning', () => {
  it('renders complete original and translated text without the old 40-character cut-off', () => {
    const original = 'A deliberately long original subtitle that remains completely visible after forty characters.';
    const translation = '这是一条明显超过四十个字符并且需要在字幕表格单元格中完整显示和自动换行的译文字幕。';
    const unbroken = 'LONG_UNBROKEN_SUBTITLE_TEXT_ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789';
    const segments = [
      makeSegment(1, { clean_text: original, translated_text: translation }),
      makeSegment(2, { clean_text: unbroken }),
    ];

    render(<SubtitleTable {...tableProps(segments)}/>);

    expect(screen.getByText(original)).toHaveTextContent(original);
    expect(screen.getByText(translation)).toHaveTextContent(translation);
    expect(screen.getByText(unbroken)).toHaveTextContent(unbroken);
  });

  it('keeps one-, two-, and three-digit row identities in a dedicated layout wrapper', () => {
    const segments = [makeSegment(1), makeSegment(10), makeSegment(100)];
    render(<SubtitleTable {...tableProps(segments)}/>);

    for (const index of [1, 10, 100]) {
      const checkbox = screen.getByRole('checkbox', { name: `选择第 ${index} 条字幕` });
      const identity = checkbox.closest('.subtitle-row-identity');
      expect(identity).not.toBeNull();
      expect(identity).toHaveTextContent(String(index));
      expect(identity?.parentElement).toHaveClass('col-idx');
    }
  });

  it('restores transcription as complete when a reopened project already has subtitles', () => {
    const project: Project = {
      id: 'project-1',
      title: 'Existing subtitles',
      source_type: 'youtube',
      source_url: 'https://youtube.test/watch?v=1',
      video_path: '/tmp/video.mp4',
      thumbnail_url: null,
      group_name: null,
      audio_path: '/tmp/audio.wav',
      language: 'en',
      target_language: 'zh',
      created_at: 'now',
      updated_at: 'now',
      segments_count: 101,
      media_mode: 'local',
      youtube_video_id: '1',
      video_available: true,
      audio_available: true,
    };

    const steps = deriveProcessSteps(project);
    expect(steps.find(step => step.id === 'transcribe')).toMatchObject({
      status: 'success',
      progress: 100,
    });
  });

  it('selects the active, next, first, or last subtitle for entry positioning', () => {
    const segments = [
      makeSegment(1, { start: 2, end: 4 }),
      makeSegment(2, { start: 6, end: 8 }),
      makeSegment(3, { start: 10, end: 12 }),
    ];

    expect(findSubtitleFocusIndex(segments, 3)).toBe(1);
    expect(findSubtitleFocusIndex(segments, 5)).toBe(2);
    expect(findSubtitleFocusIndex(segments, 0)).toBe(1);
    expect(findSubtitleFocusIndex(segments, 20)).toBe(3);
    expect(findSubtitleFocusIndex([], 3)).toBe(-1);
  });

  it('calculates a far virtual window from measured variable row heights', () => {
    const segments = Array.from({ length: 464 }, (_, position) => makeSegment(position + 1));
    const measured = new Map(segments.map((segment, position) => [
      segment.id,
      position % 3 === 0 ? 92 : position % 3 === 1 ? 58 : 44,
    ]));
    const offsets = buildSubtitleRowOffsets(segments, measured, 58);
    const targetPosition = 399;
    const range = getSubtitleWindowRange(offsets, offsets[targetPosition], 680, 0);

    expect(offsets[400]).toBeGreaterThan(400 * 44);
    expect(range.start).toBe(targetPosition);
    expect(range.end).toBeGreaterThan(targetPosition + 1);
  });

  it('positions a far subtitle on entry even while continuous auto-scroll is locked', async () => {
    const segments = Array.from({ length: 464 }, (_, position) => makeSegment(position + 1));
    const props = tableProps(segments);
    const { container, rerender } = render(
      <SubtitleTable {...props} entryFocusIdx={320} entryFocusRequest={1}/>,
    );

    await waitFor(() => expect(screen.getByText('Original subtitle 320')).toBeInTheDocument());
    expect(scrollTo).toHaveBeenCalled();
    expect(scrollTo.mock.calls.at(-1)?.[0].top).toBeGreaterThan(10_000);

    fireEvent.scroll(container.querySelector('.subtitle-table-scroll') as HTMLElement);
    expect(props.onAutoScrollChange).not.toHaveBeenCalled();

    scrollTo.mockClear();
    rerender(<SubtitleTable {...props} entryFocusIdx={400} entryFocusRequest={2}/>);
    await waitFor(() => expect(screen.getByText('Original subtitle 400')).toBeInTheDocument());
    expect(scrollTo).toHaveBeenCalled();
  });
});
