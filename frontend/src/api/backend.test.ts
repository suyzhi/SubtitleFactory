import { beforeEach, describe, expect, it, vi } from 'vitest';

const invokeMock = vi.hoisted(() => vi.fn());

vi.mock('@tauri-apps/api/core', () => ({ invoke: invokeMock }));

import {
  getTaskStatus, materializeProjectVideo, saveManagedFile, saveTextFile,
} from './backend';

beforeEach(() => {
  invokeMock.mockReset();
  delete (window as any).__TAURI_INTERNALS__;
});

describe('native file delivery', () => {
  it('sends only the managed source and suggested name to the Rust save command', async () => {
    (window as any).__TAURI_INTERNALS__ = {};
    invokeMock.mockResolvedValue('/Users/test/Desktop/episode.mp4');

    await expect(saveManagedFile('/managed/exports/render.mp4', 'episode.mp4')).resolves.toBe(true);
    expect(invokeMock).toHaveBeenCalledWith('save_managed_file', {
      sourcePath: '/managed/exports/render.mp4',
      suggestedName: 'episode.mp4',
    });
  });

  it('distinguishes an explicit save-panel cancellation from success', async () => {
    (window as any).__TAURI_INTERNALS__ = {};
    invokeMock.mockResolvedValue(null);

    await expect(saveTextFile('subtitle-style.json', '{"version":1}')).resolves.toBe(false);
    expect(invokeMock).toHaveBeenCalledWith('save_text_file', {
      suggestedName: 'subtitle-style.json',
      contents: '{"version":1}',
    });
  });
});

describe('local API caching', () => {
  it('disables WebKit caching for task polling GET requests', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'ocr-task',
      project_id: 'project-id',
      type: 'ocr',
      status: 'success',
      progress: 100,
      details: { ocr_preview: [] },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await getTaskStatus('ocr-task');

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/tasks/ocr-task'),
      expect.objectContaining({ cache: 'no-store' }),
    );
  });
});

describe('materializeProjectVideo', () => {
  it('waits for a concurrent audio task before retrying automatic player fallback', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: {
          code: 'MEDIA_TASK_ACTIVE',
          message: '项目已有媒体或处理任务正在运行',
          task_ids: ['audio-task'],
        },
      }), { status: 409, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: 'audio-task',
        project_id: 'project-id',
        type: 'prepare_audio',
        status: 'success',
        progress: 100,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        task_id: 'video-task',
        message: '正在下载并保留本地视频',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(materializeProjectVideo('project-id', 'player_fallback')).resolves.toEqual({
      task_id: 'video-task',
      message: '正在下载并保留本地视频',
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[0]?.[0]).toContain(
      '/api/projects/project-id/materialize-video?reason=player_fallback',
    );
    expect(fetchMock.mock.calls[1]?.[0]).toContain('/api/tasks/audio-task');
    expect(fetchMock.mock.calls[2]?.[0]).toContain(
      '/api/projects/project-id/materialize-video?reason=player_fallback',
    );
  });
});
