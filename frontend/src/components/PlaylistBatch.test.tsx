import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { PlaylistBatchDetail, PlaylistPreview, Project } from '../types';
import PlaylistBatchDialog from './PlaylistBatchDialog';
import PlaylistBatchGroups from './PlaylistBatchGroups';
import * as api from '../api/backend';

vi.mock('../api/backend', () => ({
  previewPlaylist: vi.fn(), createPlaylistBatch: vi.fn(), pausePlaylistBatch: vi.fn(),
  resumePlaylistBatch: vi.fn(), cancelPlaylistBatch: vi.fn(), retryPlaylistBatch: vi.fn(),
  retryPlaylistItem: vi.fn(), runPlaylistStage: vi.fn(),
  syncPlaylistBatch: vi.fn(),
}));

const preview: PlaylistPreview = {
  playlist: { id: 'PL-test', title: 'Piano course', url: 'https://www.youtube.com/playlist?list=PL-test', channel: 'Teacher', thumbnail_url: null, item_count: 2, available_count: 2, unavailable_count: 0, total_duration: 7200 },
  items: [1, 2].map(index => ({ source_id: `video-${index}`, video_id: `video-${index}`, position: index, title: `Lesson ${index}`, url: `https://youtube.test/${index}`, duration: 3600, thumbnail_url: null, availability: 'active' })),
  warnings: [],
};

const project: Project = { id: 'project-1', title: 'Lesson 1', source_type: 'youtube', source_url: 'https://youtube.test/1', video_path: null, thumbnail_url: null, group_name: null, audio_path: null, language: 'en', target_language: 'zh', created_at: 'now', updated_at: 'now', segments_count: 0 };
const detail: PlaylistBatchDetail = {
  batch: { id: 'batch-1', name: 'Piano course', title: 'Piano course', status: 'running', source_url: preview.playlist.url, source_external_id: 'PL-test', channel: 'Teacher', thumbnail_url: null, paused: false, configuration: {}, item_count: 2, completed_count: 1, failed_count: 0, progress: 50, updated_at: 'now' },
  items: [{ id: 'item-1', project_id: project.id, source_id: 'video-1', source_url: project.source_url, position: 1, title: project.title, duration: 3600, thumbnail_url: null, source_state: 'active', status: 'running', error: null, project, stages: { download: { status: 'success', task_id: null, attempt: 1, error_code: null, error: null, progress: 100 }, transcribe: { status: 'running', task_id: 'task-1', attempt: 1, error_code: null, error: null, progress: 50 } } }],
};

describe('playlist batches', () => {
  it('previews the playlist and requires explicit consent for AI stages', async () => {
    vi.mocked(api.previewPlaylist).mockResolvedValue(preview);
    vi.mocked(api.createPlaylistBatch).mockResolvedValue({ action: 'created', batch_id: 'batch-1', added_count: 2, existing_count: 0, batch: detail.batch });
    const user = userEvent.setup();
    render(<PlaylistBatchDialog url={preview.playlist.url} workflow={{ model: 'small', runtime: 'cpu', language: 'en', target_language: 'zh', clean_target_length: 42 }} appSettings={{ download_quality: 'best', download_container: 'mp4' }} health={{ runtime: { ffmpeg: { ok: true }, yt_dlp: { ok: true }, disk: { ok: true, message: '空间充足' } } } as any} aiReady onClose={() => undefined} onCreated={() => undefined}/>);
    expect(await screen.findByText('Piano course')).toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: /AI 整理/ }));
    expect(screen.getByRole('button', { name: /创建并处理/ })).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /确认批量调用 AI 服务/ }));
    expect(screen.getByRole('button', { name: /创建并处理 2 个视频/ })).toBeEnabled();
  });

  it('renders child projects inside one batch group and opens the existing editor project', async () => {
    const onOpenProject = vi.fn();
    render(<PlaylistBatchGroups batches={[detail]} search="" collapsed={new Set()} workflow={{ model: 'small', runtime: 'cpu' }} onToggle={() => undefined} onOpenProject={onOpenProject} onChanged={() => undefined} onMessage={() => undefined}/>);
    expect(screen.getByText('播放列表批量任务')).toBeInTheDocument();
    expect(screen.getByText('Piano course')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /1\. Lesson 1/ }));
    await waitFor(() => expect(onOpenProject).toHaveBeenCalledWith(project));
  });
});
