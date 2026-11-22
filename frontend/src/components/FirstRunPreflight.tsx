import type * as api from '../api/backend';
import type { AppSettings, HealthStatus } from '../types';

interface Props {
  appSettings: AppSettings;
  health: HealthStatus | null;
  modelStatus: Awaited<ReturnType<typeof api.getTranscriptionModels>>;
  onConfirm: (modelId?: string, runtimeId?: string) => void;
}

export default function FirstRunPreflight({
  appSettings,
  health,
  modelStatus,
  onConfirm,
}: Props) {
  const configuredModel = String(appSettings.default_model || 'auto');
  const modelId =
    configuredModel === 'auto' ? String(modelStatus.recommended_model || 'small') : configuredModel;
  const model = modelStatus.models.find(item => item.id === modelId) || modelStatus.models[0];
  const recommended =
    model?.runtimes?.find(item => item.available && item.id !== 'cpu') ||
    model?.runtimes?.find(item => item.available);

  return (
    <div className="preflight-overlay" role="dialog" aria-modal="true" aria-label="首次运行预检">
      <section className="preflight-card">
        <header>
          <small>首次运行</small>
          <h2>本机准备就绪</h2>
          <p>基础转写在本机完成；AI、OCR 和说话人模型只在首次使用对应功能时准备。</p>
        </header>
        <div className="preflight-checks">
          <span className={health?.runtime?.ffmpeg?.ok ? 'ok' : 'warning'}>
            <i>{health?.runtime?.ffmpeg?.ok ? '✓' : '!'}</i>
            <strong>FFmpeg</strong>
            <small>{health?.runtime?.ffmpeg?.ok ? '可用' : '需要在设置中检查'}</small>
          </span>
          <span className={health?.runtime?.disk?.ok ? 'ok' : 'warning'}>
            <i>{health?.runtime?.disk?.ok ? '✓' : '!'}</i>
            <strong>磁盘</strong>
            <small>{health?.runtime?.disk?.message || '已检查可用空间'}</small>
          </span>
          <span className={model?.ready ? 'ok' : 'warning'}>
            <i>{model?.ready ? '✓' : '↓'}</i>
            <strong>{model?.name || '默认模型'}</strong>
            <small>{model?.ready ? '已就绪' : '首次转写时按需下载'}</small>
          </span>
        </div>
        <div className="preflight-device">
          <strong>推荐运行设备</strong>
          <span>
            {recommended?.name || 'CPU'}
            <small>{recommended?.engine || '本地运行'}</small>
          </span>
        </div>
        <footer>
          <button
            className="button primary"
            disabled={!recommended}
            onClick={() => onConfirm(model?.id, recommended?.id)}
          >
            确认并开始使用
          </button>
        </footer>
      </section>
    </div>
  );
}
