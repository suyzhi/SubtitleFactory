import { describe, expect, it, vi } from 'vitest';

import { materializeProjectVideo } from './backend';

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
