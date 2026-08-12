// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SubtitlePlayer from './SubtitlePlayer';
import { DEFAULT_SUBTITLE_STYLE } from '../subtitleStyle';

vi.mock('../api/backend', () => ({
  getPlaybackInfo: vi.fn().mockResolvedValue({
    frame_rate: 30,
    frame_duration: 1 / 30,
    duration: 1,
    frame_rate_reliable: true,
    frame_rate_source: 'average_rate',
  }),
}));

vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    isFullscreen: vi.fn().mockResolvedValue(false),
    setFullscreen: vi.fn().mockResolvedValue(undefined),
  }),
}));

const baseProps = {
  projectId: 'project-1',
  videoUrl: 'http://127.0.0.1:8000/video',
  segments: [],
  style: DEFAULT_SUBTITLE_STYLE,
  activeIdx: -1,
  presentationMode: 'normal' as const,
  onTimeUpdate: vi.fn(),
  onDurationChange: vi.fn(),
  onStyleChange: vi.fn(),
  onPresentationModeChange: vi.fn(),
};

describe('SubtitlePlayer frame controls', () => {
  beforeEach(() => {
    // Keep the module-level resolved API mock; only clear call history.
    // Restoring the mock erases its Promise implementation and turns this
    // component's startup contract into a false failure.
    vi.clearAllMocks();
    Object.defineProperty(HTMLMediaElement.prototype, 'pause', {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value: vi.fn().mockResolvedValue(undefined),
    });
    Object.defineProperty(HTMLMediaElement.prototype, 'load', {
      configurable: true,
      value: vi.fn(),
    });
  });

  it('replays and clamps previous/next frame at video boundaries', async () => {
    const { container } = render(<SubtitlePlayer {...baseProps}/>);
    const video = container.querySelector('video') as HTMLVideoElement;
    let current = 0;
    Object.defineProperty(video, 'duration', { configurable: true, value: 1 });
    Object.defineProperty(video, 'currentTime', {
      configurable: true,
      get: () => current,
      set: value => {
        current = Number(value);
        queueMicrotask(() => video.dispatchEvent(new Event('seeked')));
      },
    });
    Object.defineProperty(video, 'requestVideoFrameCallback', {
      configurable: true,
      value: (callback: () => void) => {
        callback();
        return 1;
      },
    });

    fireEvent.click(screen.getByRole('button', { name: '后一帧' }));
    await waitFor(() => expect(current).toBeCloseTo(1 / 30, 5));

    current = 0;
    fireEvent.click(screen.getByRole('button', { name: '前一帧' }));
    await waitFor(() => expect(current).toBe(0));

    current = 1;
    fireEvent.click(screen.getByRole('button', { name: '后一帧' }));
    await waitFor(() => expect(current).toBe(1));

    current = 0.6;
    fireEvent.click(screen.getByRole('button', { name: '重播' }));
    await waitFor(() => expect(current).toBe(0));
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
  });

  it.each([0, 50, 86, 100])(
    'maps subtitle position %s to an edge-safe translate',
    position => {
      const segment = {
        id: 's1', project_id: 'p1', index: 1, start: 0, end: 2,
        raw_text: '字幕', clean_text: '字幕', translated_text: '',
        speaker: '', locked: false, is_draft: false, source_stage: 'clean',
      };
      const { container } = render(<SubtitlePlayer
        {...baseProps}
        segments={[segment]}
        activeIdx={0}
        style={{ ...DEFAULT_SUBTITLE_STYLE, mode: 'original', verticalPosition: position }}
      />);
      const overlay = container.querySelector('.pro-subtitle-overlay') as HTMLElement;
      expect(overlay).toHaveStyle({
        top: `${position}%`,
        transform: `translate(-50%, -${position}%)`,
      });
    },
  );

  it('supports R, comma and period keyboard shortcuts', async () => {
    const { container } = render(<SubtitlePlayer {...baseProps}/>);
    const wrapper = container.querySelector('.pro-player') as HTMLElement;
    const video = container.querySelector('video') as HTMLVideoElement;
    let current = 0;
    Object.defineProperty(video, 'duration', { configurable: true, value: 1 });
    Object.defineProperty(video, 'currentTime', {
      configurable: true,
      get: () => current,
      set: value => {
        current = Number(value);
        queueMicrotask(() => video.dispatchEvent(new Event('seeked')));
      },
    });
    Object.defineProperty(video, 'requestVideoFrameCallback', {
      configurable: true,
      value: (callback: () => void) => {
        callback();
        return 1;
      },
    });
    (HTMLMediaElement.prototype.pause as ReturnType<typeof vi.fn>).mockClear();
    (HTMLMediaElement.prototype.play as ReturnType<typeof vi.fn>).mockClear();
    fireEvent.keyDown(wrapper, { key: ',' });
    fireEvent.keyDown(wrapper, { key: '.' });
    fireEvent.keyDown(wrapper, { key: 'r' });
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1));
  });
});
