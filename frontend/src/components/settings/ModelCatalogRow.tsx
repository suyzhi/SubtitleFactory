import * as api from '../../api/backend';
import type { AppSettings, TaskStatus } from '../../types';
import AppSelect from '../AppSelect';
import { bytes } from './settingsHelpers';

const MODEL_SOURCE_LABELS: Record<string, string> = {
  bundled: '内置',
  app_download: 'App 下载',
  app_managed: 'App 下载',
  external_detected: 'Memo（外部检测）',
  custom: '自定义路径',
  environment: '环境变量',
  unavailable: '不可用',
  huggingface: 'Hugging Face',
  github: 'GitHub',
  legacy_cache: '已有缓存',
  custom_path: '本地目录',
  local_selection: '本地目录',
  dashscope: '阿里云百炼',
};

interface Props {
  model: api.TranscriptionModelStatus;
  recommendedModel?: string;
  draft: AppSettings;
  providerCards: api.AIProviderCard[];
  cloudAuthorizations: api.CloudAuthorization[];
  busy: boolean;
  validatingModel: string;
  preparingModel: string;
  modelTasks: Record<string, TaskStatus>;
  onUpdateDraft: (partial: Partial<AppSettings>) => void;
  onValidateModel: (modelId: string) => void;
  onPrepareModel: (modelId: string, runtime: string, repair: boolean) => void;
  onRemoveModel: (model: api.TranscriptionModelStatus) => void;
  onUpdateCloudAuth: (granted: boolean) => void;
  onRequireDashscopeConfig: () => void;
}

export function ModelCatalogRow({
  model,
  recommendedModel,
  draft,
  providerCards,
  cloudAuthorizations,
  busy,
  validatingModel,
  preparingModel,
  modelTasks,
  onUpdateDraft,
  onValidateModel,
  onPrepareModel,
  onRemoveModel,
  onUpdateCloudAuth,
  onRequireDashscopeConfig,
}: Props) {
  const selectedRuntime = String(
    (draft.transcription_runtime_by_model as Record<string, string> | undefined)?.[model.id] ||
      model.selected_runtime ||
      (model.runtimes?.length === 1 ? model.runtimes[0].id : ''),
  );
  const selectedVariant = model.runtimes?.find(runtime => runtime.id === selectedRuntime);
  const ready = Boolean(selectedVariant?.model_ready);
  const canDownload = Boolean(selectedVariant?.download_required);
  const modelReady = Boolean(model.ready || model.runtimes?.some(runtime => runtime.model_ready));
  const hasDownloadableRuntime = Boolean(
    model.runtimes?.some(runtime => runtime.download_required),
  );
  const isFunCloud = model.id === 'fun-asr-realtime';
  const dashscopeConfigured = Boolean(
    providerCards.find(card => card.provider_id === 'dashscope')?.has_api_key,
  );
  const funCloudGranted = Boolean(
    cloudAuthorizations.find(
      item => item.capability === 'transcription' && item.provider_id === 'dashscope',
    )?.granted,
  );
  const taskKey = `${model.id}:${selectedRuntime}`;
  const task = modelTasks[taskKey];
  const progress = task?.details?.model_download as Record<string, unknown> | undefined;
  const source = String(
    selectedVariant?.source || model.source || (ready ? 'app_download' : 'unavailable'),
  );
  const progressBytes = Number(progress?.downloaded_bytes || 0);
  const totalBytes = Number(progress?.total_bytes || selectedVariant?.download_bytes || 0);

  return (
    <article className="model-row model-catalog-row">
      <span
        className={`status-orb ${modelReady ? 'ok' : hasDownloadableRuntime ? 'warning' : 'error'}`}
      />
      <div className="model-copy">
        <strong>
          {model.name}
          {model.id === recommendedModel && <b className="model-recommended">推荐</b>}
        </strong>
        <small>
          {model.purpose} · {model.language_description} · {model.size_label}
        </small>
        <span className="model-tags">
          {model.family && <i>{model.family}</i>}
          {model.speed_tier && <i>速度 {model.speed_tier}</i>}
          {model.accuracy_tier && <i>精度 {model.accuracy_tier}</i>}
          {model.memory_tier && <i>内存 {model.memory_tier}</i>}
          {model.timestamp_mode && (
            <i>
              {model.timestamp_mode === 'word'
                ? '逐词时间戳'
                : model.timestamp_mode === 'token'
                  ? '逐字/词元时间戳'
                  : '语音片段级时间轴'}
            </i>
          )}
          {model.punctuation_mode && (
            <i>
              {model.punctuation_mode === 'native'
                ? '原生标点'
                : model.punctuation_mode === 'none'
                  ? '不补标点'
                  : '有限标点'}
            </i>
          )}
          {model.tags?.map(tag => (
            <i key={tag}>{tag}</i>
          ))}
        </span>
        {(model.strengths?.length || model.limitations?.length || model.scenarios?.length) && (
          <details className="model-explanation">
            <summary>查看适用场景和限制</summary>
            {!!model.scenarios?.length && (
              <p>
                <strong>适合：</strong>
                {model.scenarios.join('、')}
              </p>
            )}
            {!!model.strengths?.length && (
              <p>
                <strong>优势：</strong>
                {model.strengths.join('；')}
              </p>
            )}
            {!!model.limitations?.length && (
              <p>
                <strong>不适合：</strong>
                {model.limitations.join('；')}
              </p>
            )}
            <p>
              <strong>来源：</strong>
              {model.publisher || model.source_site || '官方模型'}
              {model.license ? ` · 许可证 ${model.license}` : ''}
            </p>
          </details>
        )}
        {task && !['success', 'failed', 'cancelled'].includes(task.status) && (
          <span className="model-progress">
            <progress max={100} value={task.progress} />
            <small>
              {task.step === 'verifying_model'
                ? '正在校验 SHA-256'
                : `${bytes(progressBytes)} / ${bytes(totalBytes)} · ${task.progress.toFixed(1)}%${progress?.resumed ? ' · 断点续传' : ''}`}
            </small>
          </span>
        )}
        {task?.status === 'failed' && (
          <small className="model-error">{task.error || task.message}</small>
        )}
      </div>
      <em title={selectedVariant?.repository || model.publisher}>
        {MODEL_SOURCE_LABELS[source] || model.source_site || source}
      </em>
      {!!model.runtimes?.length && (
        <AppSelect
          className="model-runtime-select"
          value={selectedRuntime}
          onChange={runtime =>
            onUpdateDraft({
              transcription_runtime_by_model: {
                ...((draft.transcription_runtime_by_model as Record<string, string> | undefined) ||
                  {}),
                [model.id]: runtime,
              },
            })
          }
          label={`${model.name} 运行设备`}
          placeholder="选择 CPU / GPU"
          options={model.runtimes.map(runtime => ({
            value: runtime.id,
            label: runtime.name,
            description: `${runtime.engine || ''}${runtime.model_ready ? ' · 已就绪' : runtime.download_required ? ` · 下载 ${bytes(runtime.download_bytes)}` : ''}`,
            disabled: !runtime.available,
          }))}
        />
      )}
      <span className="model-row-actions">
        <button
          className="button secondary model-action"
          disabled={!!validatingModel}
          onClick={() => onValidateModel(model.id)}
        >
          {validatingModel === model.id ? '校验中…' : '校验'}
        </button>
        {isFunCloud ? (
          dashscopeConfigured ? (
            <button
              className={`button secondary model-action${funCloudGranted ? ' danger' : ''}`}
              disabled={busy}
              onClick={() => onUpdateCloudAuth(!funCloudGranted)}
            >
              {funCloudGranted ? '撤销上传授权' : '授权音频上传'}
            </button>
          ) : (
            <button
              className="button secondary model-action"
              onClick={onRequireDashscopeConfig}
            >
              配置百炼
            </button>
          )
        ) : (
          selectedRuntime &&
          (ready || canDownload) && (
            <button
              className="button secondary model-action"
              disabled={!!preparingModel}
              onClick={() => onPrepareModel(model.id, selectedRuntime, ready)}
            >
              {preparingModel === taskKey ? '处理中…' : ready ? '修复' : '下载'}
            </button>
          )
        )}
        {!isFunCloud && !selectedRuntime && (modelReady || hasDownloadableRuntime) && (
          <small className="model-runtime-required">先选择运行设备</small>
        )}
        {modelReady && model.removable && (
          <button
            className="button secondary model-action danger"
            disabled={busy || !!preparingModel}
            onClick={() => onRemoveModel(model)}
          >
            移除
          </button>
        )}
        {preparingModel === taskKey && task && (
          <button
            className="button secondary model-action danger"
            onClick={() => void api.cancelTask(task.id)}
          >
            取消
          </button>
        )}
      </span>
    </article>
  );
}
