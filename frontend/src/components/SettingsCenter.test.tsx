// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import SettingsCenter from './SettingsCenter';
import * as api from '../api/backend';

vi.mock('../api/backend', async importOriginal => {
  const actual = await importOriginal<typeof import('../api/backend')>();
  return {
    ...actual,
    getAppSettings: vi.fn().mockResolvedValue({ settings: { default_model: 'small' }, warnings: [] }),
    getAISettings: vi.fn().mockResolvedValue({ settings: {}, presets: [] }),
    getAIProviders: vi.fn().mockResolvedValue({ providers: [], assignments: { clean_provider_id: 'deepseek', translate_provider_id: 'deepseek' } }),
    prepareTranscriptionModel: vi.fn().mockResolvedValue({ task_id: 'download-task', model_id: 'tiny', runtime: 'cpu', message: '正在准备模型' }),
    getTaskStatus: vi.fn().mockResolvedValue({
      id: 'download-task', project_id: '', type: 'prepare_model', status: 'success',
      step: 'model_ready', progress: 100, message: '完成', error: null,
      created_at: '', updated_at: '', details: {}, logs: [],
    }),
  };
});

const categoryNames: Record<string, string> = {
  lightweight: '轻量快速',
  balanced: '日常均衡',
  performance: '高性能 / 高精度',
  english: '英语专用',
  parakeet: 'Parakeet',
};

const models: api.TranscriptionModelStatus[] = [
  ['tiny', 'Whisper Tiny', 'lightweight'],
  ['base', 'Whisper Base', 'lightweight'],
  ['small', 'Whisper Small', 'balanced'],
  ['medium', 'Whisper Medium', 'balanced'],
  ['large-v3', 'Whisper Large V3', 'performance'],
  ['large-v3-turbo', 'Whisper Large V3 Turbo', 'performance'],
  ['distil-large-v3', 'Distil-Whisper Large V3', 'english'],
  ['parakeet-tdt-0.6b-v3-int8', 'Parakeet V3 ONNX', 'parakeet'],
  ['parakeet-tdt-0.6b-v3-coreml', 'Parakeet V3 外部 Core ML', 'parakeet'],
].map(([id, name, category]) => ({
  id, name, category_id: category, category_name: categoryNames[category],
  purpose: '测试用途', language_description: category === 'english' ? '仅英语' : '多语言',
  size_label: '约 75 MB', publisher: '官方', tags: ['CPU'],
  ready: false, download_required: id !== 'parakeet-tdt-0.6b-v3-coreml',
  languages: category === 'english' ? ['en'] : ['*'],
  runtimes: id === 'parakeet-tdt-0.6b-v3-coreml' ? [{
    id: 'external_coreml', name: '外部 Core ML', available: false,
    model_ready: false, download_required: false,
  }] : [{
    id: 'cpu', name: 'CPU', engine: 'CTranslate2', available: true,
    model_ready: false, download_required: true, download_bytes: 75_000_000,
    source: 'huggingface',
  }, {
    id: 'mlx', name: 'Apple GPU', engine: 'MLX', available: true,
    model_ready: true, download_required: false, download_bytes: 74_000_000,
    source: 'app_download',
  }],
  selected_runtime: 'cpu',
}));

describe('SettingsCenter model catalog', () => {
  it('groups nine models and downloads the currently selected runtime', async () => {
    render(<SettingsCenter
      open
      onClose={vi.fn()}
      config={{ model: 'small', language: 'auto', target_language: 'zh' } as never}
      onConfigChange={vi.fn()}
      appSettings={{ default_model: 'small' } as never}
      onAppSettingsChange={vi.fn()}
      aiSettings={null}
      onAISaved={vi.fn()}
      theme="dark"
      onThemeChange={vi.fn()}
      motionEnabled
      onMotionEnabledChange={vi.fn()}
      density="comfortable"
      onDensityChange={vi.fn()}
      health={null}
      onRefreshHealth={vi.fn()}
      modelStatus={{
        recommended_model: 'small',
        category_order: ['lightweight','balanced','performance','english','parakeet'],
        models,
      }}
      onRefreshModels={vi.fn()}
      onOpenLogs={vi.fn()}
    />);
    fireEvent.click(screen.getByRole('button', { name: /转写/ }));
    await waitFor(() => expect(screen.getByText('轻量快速')).toBeInTheDocument());
    expect(screen.getAllByText(/Whisper|Parakeet/).length).toBeGreaterThanOrEqual(9);
    for (const category of Object.values(categoryNames)) {
      expect(screen.getByText(category)).toBeInTheDocument();
    }

    const tinyRow = screen.getByText('Whisper Tiny').closest('article');
    expect(tinyRow).not.toBeNull();
    fireEvent.click(within(tinyRow as HTMLElement).getByRole('button', { name: '下载' }));
    await waitFor(() => expect(api.prepareTranscriptionModel).toHaveBeenCalledWith('tiny', 'cpu', false));
  });
});
