// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { describe, expect, it, vi } from 'vitest';
import SettingsCenter from './SettingsCenter';
import * as api from '../api/backend';

vi.mock('../api/backend', async importOriginal => {
  const actual = await importOriginal<typeof import('../api/backend')>();
  return {
    ...actual,
    getAppSettings: vi.fn().mockResolvedValue({ settings: { default_model: 'small' }, warnings: [] }),
    getAISettings: vi.fn().mockResolvedValue({ settings: {}, presets: [] }),
    getAIProviders: vi.fn().mockResolvedValue({ providers: [], assignments: { clean_provider_id: 'deepseek', translate_provider_id: 'deepseek', content_provider_id: 'deepseek' } }),
    getCloudAuthorizations: vi.fn().mockResolvedValue({ authorizations: [] }),
    setCloudAuthorization: vi.fn().mockResolvedValue(undefined),
    prepareTranscriptionModel: vi.fn().mockResolvedValue({ task_id: 'download-task', model_id: 'tiny', runtime: 'cpu', message: '正在准备模型' }),
    getTaskStatus: vi.fn().mockResolvedValue({
      id: 'download-task', project_id: '', type: 'prepare_model', status: 'success',
      step: 'model_ready', progress: 100, message: '完成', error: null,
      created_at: '', updated_at: '', details: {}, logs: [],
    }),
    removeTranscriptionModel: vi.fn().mockResolvedValue({
      model_id: 'medasr-ctc-en-int8-2025-12-25', removed: true,
      removed_bytes: 154_111_131, message: '模型文件已移除，可随时重新下载',
    }),
  };
});

const categoryNames: Record<string, string> = {
  lightweight: '轻量快速',
  balanced: '日常均衡',
  performance: '高性能 / 高精度',
  english: '英语专用',
  parakeet: 'Parakeet',
  multilingual: '通用多语言',
  chinese: '中文与中英混说',
  dialects: '中文方言专用',
  east_asian: '日韩与俄语',
  specialized: '专业场景',
  cloud: '云端转写（需授权）',
};

const legacyModels: api.TranscriptionModelStatus[] = [
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

const managedModelIds = [
  'dolphin-base-ctc-multi-lang-int8-2025-04-02',
  'omnilingual-asr-1600-languages-300m-ctc-v2-int8-2026-02-05',
  'qwen3-asr-0.6b-int8-2026-03-25',
  'moonshine-base-zh-quantized-2026-02-27',
  'paraformer-zh-2023-09-14',
  'fire-red-asr2-ctc-zh-en-int8-2026-02-25',
  'telespeech-ctc-int8-zh-2024-06-04',
  'paraformer-zh-int8-2025-10-07',
  'wenetspeech-yue-u2pp-conformer-ctc-zh-en-cantonese-int8-2025-09-10',
  'wenetspeech-wu-u2pp-conformer-ctc-zh-int8-2026-02-03',
  'sense-voice-zh-en-ja-ko-yue-int8-2025-09-09',
  'moonshine-tiny-en-quantized-2026-02-27',
  'medasr-ctc-en-int8-2025-12-25',
  'moonshine-tiny-ja-quantized-2026-02-27',
  'nemo-parakeet-tdt-ctc-0.6b-ja-35000-int8',
  'moonshine-tiny-ko-quantized-2026-02-27',
  'zipformer-korean-2024-06-24',
  'nemo-transducer-punct-giga-am-v3-russian-2025-12-16',
] as const;

const managedModels: api.TranscriptionModelStatus[] = managedModelIds.map((id, index) => {
  const medical = id.startsWith('medasr');
  const category = medical || id.startsWith('qwen3') || id.startsWith('telespeech') || id.startsWith('sense')
    ? 'specialized'
    : id.startsWith('dolphin') || id.startsWith('omnilingual')
      ? 'multilingual'
      : id.includes('yue') || id.includes('wu-') || id === 'paraformer-zh-int8-2025-10-07'
        ? 'dialects'
        : id.includes('ja-') || id.includes('ko-') || id.includes('korean') || id.includes('russian')
          ? 'east_asian'
          : id.startsWith('moonshine-base')
            ? 'lightweight'
            : 'chinese';
  return {
    id,
    name: medical ? 'MedASR 英语医疗' : `托管模型 ${index + 1}`,
    category_id: category,
    category_name: categoryNames[category],
    purpose: medical ? '英语医疗术语' : '测试用途',
    language_description: medical ? '英语医疗语音' : '多语言',
    size_label: '下载约 120 MiB，安装约 150 MiB',
    publisher: '官方 sherpa-onnx 模型',
    tags: medical ? ['医疗', 'CPU'] : ['CPU'],
    family: medical ? 'MedASR CTC' : '测试家族',
    scenarios: medical ? ['医疗'] : ['通用字幕'],
    strengths: ['固定官方资源', '逐文件校验'],
    limitations: medical ? ['仅英语', '不会自动启用'] : ['测试限制'],
    speed_tier: '快', accuracy_tier: '高', memory_tier: '中',
    timestamp_mode: medical ? 'token' : 'segment',
    punctuation_mode: 'native',
    installed_bytes: 150_000_000,
    license: '官方许可',
    removable: true,
    ready: medical,
    download_required: !medical,
    languages: medical ? ['en'] : ['*'],
    runtimes: [{
      id: 'cpu', name: 'CPU', engine: 'sherpa-onnx', available: true,
      model_ready: medical, download_required: !medical,
      download_bytes: 120_000_000, source: medical ? 'app_download' : 'github',
    }],
    selected_runtime: 'cpu',
  };
});

const addedModels: api.TranscriptionModelStatus[] = [{
  id: 'qwen3-asr-1.7b', name: 'Qwen3-ASR 1.7B', category_id: 'specialized',
  category_name: categoryNames.specialized, purpose: '高精度多语言转写',
  language_description: '30 种语言及中文方言', size_label: '下载约 4.38 GB',
  publisher: 'Qwen / MLX Qwen3-ASR', tags: ['Apple GPU'], family: 'Qwen3-ASR',
  scenarios: ['高精度'], strengths: ['Apple GPU'], limitations: ['片段级时间轴'],
  speed_tier: '较慢', accuracy_tier: '很高', memory_tier: '很高',
  timestamp_mode: 'segment', punctuation_mode: 'native', installed_bytes: 4_700_000_000,
  license: 'Apache-2.0', removable: true, ready: false, download_required: true,
  languages: ['*'], runtimes: [{ id: 'mlx', name: 'Apple GPU', engine: 'MLX', available: true,
    model_ready: false, download_required: true, download_bytes: 4_700_000_000, source: 'huggingface' }],
  selected_runtime: 'mlx',
}, {
  id: 'fun-asr-realtime', name: 'Fun-Realtime-ASR', category_id: 'cloud',
  category_name: categoryNames.cloud, purpose: '阿里云百炼云端转写',
  language_description: '多语言及中文方言', size_label: '无需下载 · 云端按量计费',
  publisher: '阿里云百炼', tags: ['云端'], family: 'Fun-ASR', scenarios: ['方言'],
  strengths: ['逐词时间戳'], limitations: ['会上传当前项目音频'], speed_tier: '取决于网络',
  accuracy_tier: '高', memory_tier: '低（本机）', timestamp_mode: 'word', punctuation_mode: 'native',
  installed_bytes: 0, license: '阿里云模型服务条款', removable: false, ready: false,
  download_required: false, languages: ['*'], runtimes: [{ id: 'dashscope_cloud', name: '阿里云（云端）',
    engine: 'Fun-Realtime-ASR · DashScope', available: false, reason: '需要授权',
    model_ready: false, download_required: false, source: 'dashscope' }],
}];

const models = [...legacyModels, ...managedModels, ...addedModels];

function settingsProps(overrides: Partial<ComponentProps<typeof SettingsCenter>> = {}): ComponentProps<typeof SettingsCenter> {
  return {
    open: true,
    onClose: vi.fn(),
    config: { model: 'small', language: 'auto', target_language: 'zh' } as never,
    onConfigChange: vi.fn(),
    appSettings: { default_model: 'small' } as never,
    onAppSettingsChange: vi.fn(),
    aiSettings: null,
    onAISaved: vi.fn(),
    theme: 'dark',
    onThemeChange: vi.fn(),
    motionEnabled: true,
    onMotionEnabledChange: vi.fn(),
    density: 'comfortable',
    onDensityChange: vi.fn(),
    health: null,
    onRefreshHealth: vi.fn(),
    modelStatus: {
      recommended_model: 'small',
      recommendation_reason: '没有已下载且匹配所选语言的专用模型，已回退 Whisper Small',
      category_order: [
        'lightweight','balanced','performance','multilingual','chinese',
        'dialects','english','east_asian','specialized','parakeet','cloud',
      ],
      models,
    },
    onRefreshModels: vi.fn(),
    onOpenLogs: vi.fn(),
    ...overrides,
  };
}

describe('SettingsCenter model catalog', () => {
  it('renders the settings surface at the document root', () => {
    render(<SettingsCenter {...settingsProps()}/>);
    const dialog = screen.getByRole('dialog', { name: '设置中心' });
    const backdrop = dialog.closest('.settings-backdrop');
    expect(backdrop?.parentElement).toBe(document.body);
    expect(backdrop).toHaveClass('theme-dark');
  });

  it('carries the light theme into the document-level portal', () => {
    render(<SettingsCenter {...settingsProps({ theme: 'light' })}/>);
    expect(screen.getByRole('dialog', { name: '设置中心' }).closest('.settings-backdrop'))
      .toHaveClass('theme-light');
  });

  it('retries a transient provider credential read without showing a false warning', async () => {
    const callsBefore = vi.mocked(api.getAIProviders).mock.calls.length;
    vi.mocked(api.getAIProviders).mockRejectedValueOnce(new Error('Keychain temporarily unavailable'));
    render(<SettingsCenter {...settingsProps()}/>);
    await waitFor(() => expect(api.getAIProviders).toHaveBeenCalledTimes(callsBefore + 2));
    expect(screen.queryByText('AI 凭据暂时无法读取；本地转写和其他设置仍可使用。')).not.toBeInTheDocument();
  });

  it('keeps non-AI settings usable when provider credentials cannot be loaded twice', async () => {
    const callsBefore = vi.mocked(api.getAIProviders).mock.calls.length;
    vi.mocked(api.getAIProviders)
      .mockRejectedValueOnce(new Error('Keychain unavailable'))
      .mockRejectedValueOnce(new Error('Keychain unavailable'));
    render(<SettingsCenter {...settingsProps()}/>);
    expect(await screen.findByText('AI 凭据暂时无法读取；本地转写和其他设置仍可使用。')).toBeInTheDocument();
    expect(api.getAIProviders).toHaveBeenCalledTimes(callsBefore + 2);
    expect(screen.getByRole('button', { name: '保存更改' })).toBeEnabled();
  });

  it('groups twenty-nine models and downloads the selected runtime', async () => {
    render(<SettingsCenter {...settingsProps()}/>);
    fireEvent.click(screen.getByRole('button', { name: /转写/ }));
    await waitFor(() => expect(screen.getByText('轻量快速')).toBeInTheDocument());
    expect(document.querySelectorAll('.model-catalog-row')).toHaveLength(29);
    for (const category of Object.values(categoryNames)) {
      expect(screen.getByText(category)).toBeInTheDocument();
    }

    const tinyRow = screen.getByText('Whisper Tiny').closest('article');
    expect(tinyRow).not.toBeNull();
    fireEvent.click(within(tinyRow as HTMLElement).getByRole('button', { name: '下载' }));
    await waitFor(() => expect(api.prepareTranscriptionModel).toHaveBeenCalledWith('tiny', 'cpu', false));
  });

  it('requires visible confirmation before authorizing Fun-Realtime-ASR audio upload', async () => {
    vi.spyOn(window, 'confirm').mockReturnValueOnce(true);
    vi.mocked(api.getAIProviders).mockResolvedValueOnce({
      providers: [{
        provider_id: 'dashscope', name: '通义千问',
        base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        api_key: '', model: 'qwen-plus', models: ['qwen-plus'], enabled: true,
        has_api_key: true,
      }],
      assignments: { clean_provider_id: 'deepseek', translate_provider_id: 'deepseek', content_provider_id: 'deepseek' },
    });
    render(<SettingsCenter {...settingsProps()}/>);
    fireEvent.click(screen.getByRole('button', { name: /转写/ }));
    const row = (await screen.findByText('Fun-Realtime-ASR')).closest('article');
    expect(row).not.toBeNull();
    fireEvent.click(within(row as HTMLElement).getByRole('button', { name: '授权音频上传' }));
    await waitFor(() => expect(api.setCloudAuthorization).toHaveBeenCalledWith(
      'transcription', true, 'dashscope',
    ));
  });

  it('searches professional metadata and removes only managed files after confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValueOnce(true);
    render(<SettingsCenter {...settingsProps()}/>);
    fireEvent.click(screen.getByRole('button', { name: /转写/ }));
    fireEvent.change(await screen.findByPlaceholderText('名称、语言、场景或特点'), {
      target: { value: '医疗' },
    });
    const row = (await screen.findByText('MedASR 英语医疗')).closest('article');
    expect(row).not.toBeNull();
    expect(document.querySelectorAll('.model-catalog-row')).toHaveLength(1);
    fireEvent.click(within(row as HTMLElement).getByRole('button', { name: '移除' }));
    await waitFor(() => expect(api.removeTranscriptionModel).toHaveBeenCalledWith(
      'medasr-ctc-en-int8-2025-12-25',
    ));
  });

  it('keeps focus and typed model text when the parent supplies a new close callback', async () => {
    vi.mocked(api.getAIProviders).mockResolvedValueOnce({
      providers: [{
        provider_id: 'deepseek',
        name: 'DeepSeek',
        base_url: 'https://api.deepseek.com/v1',
        api_key: '',
        model: 'deepseek-v4-flash',
        models: ['deepseek-v4-flash', 'deepseek-v4-pro'],
        enabled: true,
        has_api_key: true,
      }],
      assignments: {
        clean_provider_id: 'deepseek',
        translate_provider_id: 'deepseek',
        content_provider_id: 'deepseek',
      },
    });
    const firstClose = vi.fn();
    const secondClose = vi.fn();
    const stableProps = settingsProps({ onClose: firstClose });
    const { rerender } = render(<SettingsCenter {...stableProps}/>);

    fireEvent.click(screen.getByRole('button', { name: /AI 服务/ }));
    const providerSection = (await screen.findByText('模型供应商')).closest('section');
    const card = providerSection?.querySelector('article.provider-card') || null;
    expect(card).not.toBeNull();
    const modelInput = within(card as HTMLElement).getByLabelText('模型');
    modelInput.focus();
    fireEvent.change(modelInput, { target: { value: 'deepseek-v4-f' } });
    expect(modelInput).toHaveFocus();

    rerender(<SettingsCenter {...stableProps} onClose={secondClose}/>);
    expect(modelInput).toHaveFocus();
    fireEvent.change(modelInput, { target: { value: 'deepseek-v4-flash' } });
    expect(modelInput).toHaveValue('deepseek-v4-flash');

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(firstClose).not.toHaveBeenCalled();
    expect(secondClose).toHaveBeenCalledOnce();
  });
});
