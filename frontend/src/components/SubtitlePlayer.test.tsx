import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DEFAULT_SUBTITLE_STYLE } from '../subtitleStyle';
import SubtitlePlayer from './SubtitlePlayer';

function renderPlayer(mode: 'normal' | 'theater' | 'fullscreen' = 'normal') {
  const onPresentationModeChange = vi.fn();
  const result = render(
    <SubtitlePlayer
      videoUrl="http://127.0.0.1/video.mp4"
      segments={[]}
      style={DEFAULT_SUBTITLE_STYLE}
      activeIdx={-1}
      presentationMode={mode}
      onTimeUpdate={() => undefined}
      onStyleChange={() => undefined}
      onPresentationModeChange={onPresentationModeChange}
    />,
  );
  return { ...result, onPresentationModeChange };
}

describe('SubtitlePlayer presentation controls', () => {
  it('requests theater and fullscreen modes from the controls', async () => {
    const user = userEvent.setup();
    const { onPresentationModeChange } = renderPlayer();

    await user.click(screen.getByRole('button', { name: '剧院模式' }));
    expect(onPresentationModeChange).toHaveBeenCalledWith('theater');

    await user.click(screen.getByRole('button', { name: '全屏' }));
    expect(onPresentationModeChange).toHaveBeenCalledWith('fullscreen');
  });

  it('supports F and Escape without bubbling duplicate transitions', () => {
    const { container, onPresentationModeChange, rerender } = renderPlayer();
    const player = container.querySelector('.pro-player');
    expect(player).not.toBeNull();
    fireEvent.keyDown(player!, { key: 'f' });
    expect(onPresentationModeChange).toHaveBeenCalledTimes(1);
    expect(onPresentationModeChange).toHaveBeenLastCalledWith('fullscreen');

    rerender(
      <SubtitlePlayer
        videoUrl="http://127.0.0.1/video.mp4"
        segments={[]}
        style={DEFAULT_SUBTITLE_STYLE}
        activeIdx={-1}
        presentationMode="fullscreen"
        onTimeUpdate={() => undefined}
        onStyleChange={() => undefined}
        onPresentationModeChange={onPresentationModeChange}
      />,
    );
    fireEvent.keyDown(container.querySelector('.pro-player')!, { key: 'Escape' });
    expect(onPresentationModeChange).toHaveBeenLastCalledWith('normal');
  });
});
