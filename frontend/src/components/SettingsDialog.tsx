import { useEffect, useState } from 'react';
import * as api from '../api/backend';
import { errorMessage } from '../format';
import type { AIProvidersResponse } from '../types';

interface Props {
  onClose: () => void;
  onSaved: () => void;
}

export default function SettingsDialog({ onClose, onSaved }: Props) {
  const [data, setData] = useState<AIProvidersResponse | null>(null);
  const [providerId, setProviderId] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [thinking, setThinking] = useState(false);
  const [status, setStatus] = useState<{ tone: 'ok' | 'error' | 'info'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const provider = data?.providers.find(item => item.provider_id === providerId);

  useEffect(() => {
    api.getAIProviders().then(result => {
      setData(result);
      setProviderId(result.assignments.translate_provider_id || result.providers[0]?.provider_id || '');
    }).catch(error => setStatus({ tone: 'error', text: errorMessage(error) }));
  }, []);

  useEffect(() => {
    if (!provider) return;
    setBaseUrl(provider.base_url);
    setModel(provider.model);
    setThinking(provider.thinking_enabled);
    setApiKey('');
  }, [provider]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const save = async () => {
    if (!provider) return false;
    await api.saveAIProvider(provider.provider_id, {
      base_url: baseUrl.trim(), model: model.trim(), thinking_enabled: thinking,
      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
    });
    await api.assignAIProvider(provider.provider_id);
    return true;
  };

  const run = async (action: 'save' | 'test') => {
    setBusy(true);
    setStatus(action === 'test' ? { tone: 'info', text: '正在测试连接…' } : null);
    try {
      await save();
      if (action === 'test') {
        const result = await api.testAIProvider(providerId);
        setStatus({ tone: 'ok', text: `连接成功，耗时 ${result.latency_ms} ms` });
      } else {
        onSaved();
        onClose();
      }
    } catch (error) {
      setStatus({ tone: 'error', text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <form
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onMouseDown={event => event.stopPropagation()}
        onSubmit={event => { event.preventDefault(); void run('save'); }}
      >
        <header>
          <h2 id="settings-title">AI 服务</h2>
          <p>AI 整理和翻译会把字幕文本发送到这里配置的服务。</p>
        </header>

        {data ? (
          <div className="form-grid">
            <label className="field">
              <span>服务商</span>
              <select value={providerId} onChange={event => setProviderId(event.target.value)}>
                {data.providers.map(item => <option key={item.provider_id} value={item.provider_id}>{item.name}</option>)}
              </select>
            </label>
            <label className="field">
              <span>API Key</span>
              <input
                type="password"
                autoComplete="off"
                placeholder={provider?.has_api_key ? '已保存，留空则不修改' : '粘贴 API Key'}
                value={apiKey}
                onChange={event => setApiKey(event.target.value)}
              />
            </label>
            <label className="field">
              <span>模型</span>
              <input list="provider-models" value={model} onChange={event => setModel(event.target.value)} />
              <datalist id="provider-models">
                {provider?.models.map(item => <option key={item} value={item} />)}
              </datalist>
            </label>
            <label className="field">
              <span>接口地址</span>
              <input value={baseUrl} onChange={event => setBaseUrl(event.target.value)} />
            </label>
            {provider?.supports_thinking && (
              <label className="toggle-row compact">
                <span><strong>思考模式</strong><small>更慢，但复杂内容的整理质量可能更好</small></span>
                <input type="checkbox" role="switch" checked={thinking} onChange={event => setThinking(event.target.checked)} />
              </label>
            )}
          </div>
        ) : (
          <p className="muted">正在读取设置…</p>
        )}

        {status && <p className={`status ${status.tone}`}>{status.text}</p>}

        <footer>
          <button type="button" className="button secondary" disabled={!provider || busy} onClick={() => void run('test')}>测试连接</button>
          <span className="spacer" />
          <button type="button" className="button secondary" onClick={onClose}>取消</button>
          <button type="submit" className="button primary" disabled={!provider || busy || !model.trim() || !baseUrl.trim()}>保存</button>
        </footer>
      </form>
    </div>
  );
}
