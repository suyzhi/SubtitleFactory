import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DEFAULT_SUBTITLE_STYLE } from '../subtitleStyle';
import { createYoutubePlayerSession } from '../api/backend';
import SubtitlePlayer from './SubtitlePlayer';

vi.mock('../api/backend', () => ({
  createYoutubePlayerSession: vi.fn(async () => 'http://127.0.0.1:43123/api/player/youtube/dQw4w9WgXcQ?signed=1'),
}));

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
  it('uses the signed web bridge while keeping subtitle overlay and player controls', async () => {
    render(
      <SubtitlePlayer
        youtubeVideoId="dQw4w9WgXcQ"
        segments={[{
          id: 'segment-1', project_id: 'project-1', index: 1,
          start: 0, end: 3, raw_text: '网页字幕', clean_text: '网页字幕',
          translated_text: '', speaker: '', locked: false,
          is_draft: false, source_stage: 'final',
        }]}
        style={DEFAULT_SUBTITLE_STYLE}
        activeIdx={0}
        presentationMode="normal"
        onTimeUpdate={() => undefined}
        onStyleChange={() => undefined}
        onPresentationModeChange={() => undefined}
      />,
    );

    await waitFor(() => expect(createYoutubePlayerSession).toHaveBeenCalledWith(
      'dQw4w9WgXcQ', expect.any(String),
    ));
    expect(await screen.findByTitle('YouTube 网页播放器')).toHaveAttribute(
      'src', expect.stringContaining('/api/player/youtube/'),
    );
    expect(screen.getByText('网页字幕')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '循环当前字幕' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '前进一帧' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '全屏' })).toBeInTheDocument();
  });

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
