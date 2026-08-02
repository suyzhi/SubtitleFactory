// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import SmartToolsPanel from './SmartToolsPanel';
import * as api from '../api/backend';

vi.mock('../api/backend', async importOriginal => {
  const actual = await importOriginal<typeof import('../api/backend')>();
  return {
    ...actual,
    getSpeakers: vi.fn().mockResolvedValue({ speakers: [] }),
    getCloudAuthorizations: vi.fn().mockResolvedValue({ authorizations: [] }),
    getSpeakerModelStatus: vi.fn().mockResolvedValue({
      ready: false, segmentation_model: null, embedding_model: null, managed_directory: '',
    }),
    startOCR: vi.fn().mockResolvedValue({ task_id: 'ocr-task' }),
    getTaskStatus: vi.fn()
      .mockResolvedValueOnce({
        id: 'ocr-task', project_id: 'project', type: 'ocr', status: 'running',
        step: 'ocr', progress: 0, message: '准备开始...', error: null,
        created_at: '', updated_at: '', details: {}, logs: [],
      })
      .mockResolvedValueOnce({
        id: 'ocr-task', project_id: 'project', type: 'ocr', status: 'success',
        step: 'ocr_preview', progress: 100, message: '完成', error: null,
        created_at: '', updated_at: '',
        details: { ocr_preview: [{ start: 0, end: 1, text: '识别成功', confidence: .92 }] },
        logs: [],
      }),
  };
});

describe('SmartToolsPanel task polling', () => {
  it('keeps polling OCR until the completed preview is rendered', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <SmartToolsPanel
          projectId="project"
          revision={1}
          duration={2}
          onEditorResult={vi.fn()}
          onProjectChanged={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '生成 OCR 预览' }));

    await waitFor(() => expect(screen.getByText('1 条预览')).toBeInTheDocument(), {
      timeout: 2_500,
    });
    expect(screen.getByText('识别成功')).toBeInTheDocument();
    expect(api.getTaskStatus).toHaveBeenCalledTimes(2);
  });
});
