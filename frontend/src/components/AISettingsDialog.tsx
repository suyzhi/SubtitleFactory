import { useEffect, useState } from 'react';
import * as api from '../api/backend';
import type { AIProviderPreset, AISettings } from '../types';
import AppSelect from './AppSelect';

interface Props {
  open: boolean;
  onClose: () => void;
  onSaved?: (settings: AISettings) => void;
}

const EMPTY: AISettings = {
  provider: 'deepseek',
  base_url: 'https://api.deepseek.com/v1',
  api_key: '',
  model: 'deepseek-chat',
};

export default function AISettingsDialog({ open, onClose, onSaved }: Props) {
  const [settings, setSettings] = useState<AISettings>(EMPTY);
  const [presets, setPresets] = useState<AIProviderPreset[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setMessage('');
    setError('');
    api.getAISettings()
      .then(data => {
        setSettings(data.settings);
        setPresets(data.presets);
      })
      .catch(err => setError(err.message));
  }, [open]);

  if (!open) return null;

  const selectProvider = (provider: string) => {
    const preset = presets.find(item => item.id === provider);
    setSettings(current => ({
      ...current,
      provider,
      has_api_key: provider === current.provider ? current.has_api_key : false,
      api_key: '',
      base_url: preset?.base_url ?? current.base_url,
      model: preset?.model ?? current.model,
    }));
  };

  const run = async (kind: 'test' | 'save') => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      if (kind === 'test') {
        const result = await api.testAISettings(settings);
        setSettings(result.settings);
        onSaved?.(result.settings);
        setMessage(`连接成功 · ${result.latency_ms} ms · ${settings.provider} / ${settings.model}`);
      } else {
        const result = await api.saveAISettings(settings);
        setSettings(result.settings);
        onSaved?.(result.settings);
        setMessage('AI 接入配置已保存');
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="modal-card ai-settings-dialog" role="dialog" aria-modal="true" aria-label="AI 接入管理"
        onMouseDown={event => event.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h2>AI 接入管理</h2>
            <p>整理与翻译共用 OpenAI 兼容接口，密钥仅保存在本机。</p>
          </div>
          <button className="btn btn-ghost" onClick={onClose} aria-label="关闭">✕</button>
        </div>

        <div className="ai-settings-form">
          <label>服务商
            <AppSelect value={settings.provider} onChange={selectProvider} label="AI 服务商" options={presets.map(preset=>({value:preset.id,label:preset.name,description:preset.model}))}/>
          </label>
          <label>Base URL
            <input value={settings.base_url}
              onChange={event => setSettings({ ...settings, base_url: event.target.value })}
              placeholder="https://api.example.com/v1" />
          </label>
          <label>模型
            <input value={settings.model}
              onChange={event => setSettings({ ...settings, model: event.target.value })}
              placeholder="模型名称" />
          </label>
          <label>API Key
            <input type="password" value={settings.api_key}
              onChange={event => setSettings({ ...settings, api_key: event.target.value })}
              placeholder={settings.has_api_key ? '已保存；留空表示不更改' : '请输入 API Key'} />
          </label>
        </div>

        {message && <div className="form-message success-message">{message}</div>}
        {error && <div className="form-message error-message">{error}</div>}

        <div className="modal-actions">
          <button className="btn btn-outline" disabled={busy} onClick={() => run('test')}>测试连接</button>
          <button className="btn btn-primary" disabled={busy || !settings.base_url || !settings.model}
            onClick={() => run('save')}>{busy ? '处理中…' : '保存配置'}</button>
        </div>
      </section>
    </div>
  );
}
