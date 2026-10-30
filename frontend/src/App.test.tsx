import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import * as api from './api/backend';
import type { Project, TranscriptionModel } from './types';

vi.mock('./api/backend', () => ({
  checkHealth: vi.fn(),
  listProjects: vi.fn(),
  getTranscriptionModels: vi.fn(),
  getAIProviders: vi.fn(),
  getSegments: vi.fn(),
  getLatestProjectTask: vi.fn(),
  createProject: vi.fn(),
  uploadLocalVideo: vi.fn(),
  prepareTranscriptionModel: vi.fn(),
  startWorkflow: vi.fn(),
  startClean: vi.fn(),
  startTranslate: vi.fn(),
  getTask: vi.fn(),
  cancelTask: vi.fn(),
  exportSubtitles: vi.fn(),
  deleteProject: vi.fn(),
  saveAIProvider: vi.fn(),
  assignAIProvider: vi.fn(),
  testAIProvider: vi.fn(),
  youtubeFeaturesEnabled: () => true,
}));

const mocked = vi.mocked(api);

const model = (id: string, ready: boolean): TranscriptionModel => ({
  id, name: `Whisper ${id}`, ready, category_name: '日常均衡',
  runtimes: [
    { id: 'cpu', name: 'CPU', available: true, model_ready: ready },
    { id: 'mlx', name: 'Apple GPU', available: true, model_ready: false, download_required: !ready, download_bytes: 484_000_000 },
  ],
});

const project: Project = {
  id: 'p1', title: 'Demo', source_type: 'youtube', source_url: 'https://youtu.be/demo',
  language: 'auto', target_language: 'zh', created_at: '2026-10-30 10:00:00', updated_at: '2026-10-30 10:00:00', segments_count: 2,
};

function setup({ hasKey = true, ready = true, projects = [] as Project[] } = {}) {
  localStorage.clear();
  mocked.checkHealth.mockResolvedValue(true);
  mocked.listProjects.mockResolvedValue(projects);
  mocked.getTranscriptionModels.mockResolvedValue({ recommended_model: 'small', models: [model('small', ready)] });
  mocked.getAIProviders.mockResolvedValue({
    providers: [{ provider_id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-flash', models: [], has_api_key: hasKey, supports_thinking: true, thinking_enabled: false }],
    assignments: { clean_provider_id: 'deepseek', translate_provider_id: 'deepseek' },
  });
  mocked.getSegments.mockResolvedValue([]);
  mocked.getLatestProjectTask.mockResolvedValue(null);
  mocked.createProject.mockResolvedValue({ project_id: 'p1' });
  mocked.startWorkflow.mockResolvedValue({ task_id: 't1' });
  mocked.getTask.mockResolvedValue({ id: 't1', project_id: 'p1', type: 'workflow', status: 'running', step: 'download', progress: 5, message: '正在下载视频', error: null });
}

describe('简洁版界面', () => {
  beforeEach(() => vi.clearAllMocks());

  it('carries the release markers and only enables start once a source is given', async () => {
    setup();
    const { container } = render(<App />);
    expect(container.querySelector('[data-ui-version="subtitle-factory-ui:simple-v1"]')).toBeInTheDocument();
    expect(container.querySelector('[data-ui-layout="subtitle-factory-ui:single-page-v1"]')).toBeInTheDocument();
    const start = screen.getByRole('button', { name: '开始生成字幕' });
    await waitFor(() => expect(screen.getByRole('option', { name: /Whisper small/ })).toBeInTheDocument());
    expect(start).toBeDisabled();
    await userEvent.type(screen.getByLabelText('视频链接'), 'https://youtu.be/demo');
    expect(start).toBeEnabled();
  });

  it('starts one workflow with transcription, cleanup and translation', async () => {
    setup();
    render(<App />);
    await waitFor(() => expect(screen.getByRole('option', { name: /Whisper small/ })).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText('视频链接'), 'https://youtu.be/demo');
    await userEvent.click(screen.getByRole('button', { name: '开始生成字幕' }));
    await waitFor(() => expect(mocked.startWorkflow).toHaveBeenCalledWith('p1', {
      model: 'small', runtime: 'cpu', language: 'auto', source_url: 'https://youtu.be/demo',
      enable_clean: true, enable_translate: true, target_language: 'zh',
    }));
    expect(mocked.prepareTranscriptionModel).not.toHaveBeenCalled();
  });

  it('downloads a missing model before starting the workflow', async () => {
    setup({ ready: false });
    mocked.prepareTranscriptionModel.mockResolvedValue({ task_id: 'prep' });
    mocked.getTask.mockImplementation(async id => ({
      id, project_id: id === 'prep' ? '' : 'p1', type: id === 'prep' ? 'prepare_model' : 'workflow',
      status: id === 'prep' ? 'success' : 'running', step: '', progress: 100, message: '', error: null,
    }));
    render(<App />);
    await waitFor(() => expect(screen.getByText(/首次使用会先下载模型/)).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText('视频链接'), 'https://youtu.be/demo');
    await userEvent.click(screen.getByRole('button', { name: '开始生成字幕' }));
    await waitFor(() => expect(mocked.startWorkflow).toHaveBeenCalled());
    expect(mocked.prepareTranscriptionModel).toHaveBeenCalledWith('small', 'mlx');
    expect(mocked.prepareTranscriptionModel.mock.invocationCallOrder[0]).toBeLessThan(mocked.startWorkflow.mock.invocationCallOrder[0]);
  });

  it('asks for an API key instead of starting AI steps without one', async () => {
    setup({ hasKey: false });
    render(<App />);
    await waitFor(() => expect(screen.getByRole('option', { name: /Whisper small/ })).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText('视频链接'), 'https://youtu.be/demo');
    await userEvent.click(screen.getByRole('button', { name: '开始生成字幕' }));
    expect(await screen.findByRole('dialog', { name: 'AI 服务' })).toBeInTheDocument();
    expect(mocked.createProject).not.toHaveBeenCalled();
  });

  it('shows subtitles of a past project and limits export to what exists', async () => {
    setup({ projects: [project] });
    mocked.getSegments.mockResolvedValue([
      { id: 's1', index: 1, start: 1, end: 2, raw_text: 'hello', clean_text: 'Hello.', translated_text: '' },
    ]);
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /^Demo/ }));
    expect(await screen.findByText('Hello.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '导出 SRT' }));
    expect(screen.getByRole('menuitem', { name: '原文字幕' })).toBeEnabled();
    expect(screen.getByRole('menuitem', { name: '双语字幕' })).toBeDisabled();
  });
});
