import type * as api from '../api/backend';
import type { ProcessingConfig } from '../types';
import LanguagePicker from './LanguagePicker';
import TranscriptionSetup from './TranscriptionSetup';

interface SetupProps {
  modelError: string;
  models: api.TranscriptionModelStatus[];
  recommendedModel?: string;
  model: string;
  language: string;
  runtime: string;
  onRefreshModels: () => void;
  onModelChange: (model: string) => void;
  onLanguageChange: (language: string) => void;
  onRuntimeChange: (modelId: string, runtimeId: string) => void;
}

export function TranscriptionSetupBlock({
  modelError,
  models,
  recommendedModel,
  model,
  language,
  runtime,
  onRefreshModels,
  onModelChange,
  onLanguageChange,
  onRuntimeChange,
}: SetupProps) {
  return (
    <>
      {modelError && (
        <p role="alert">
          模型列表暂时无法刷新：{modelError}
          <button onClick={onRefreshModels}>重新加载模型</button>
        </p>
      )}
      <TranscriptionSetup
        models={models}
        model={model}
        language={language}
        recommended={recommendedModel}
        runtime={runtime}
        onModel={onModelChange}
        onLanguage={onLanguageChange}
        onRuntime={onRuntimeChange}
      />
    </>
  );
}

interface FlowOptionsProps {
  config: ProcessingConfig;
  cleanAIReady: boolean;
  translateAIReady: boolean;
  cleanAIProvider?: api.AIProviderCard;
  translateAIProvider?: api.AIProviderCard;
  onConfigChange: (updater: (current: ProcessingConfig) => ProcessingConfig) => void;
  onOpenSettings: () => void;
}

export function FlowOptionsPanel({
  config,
  cleanAIReady,
  translateAIReady,
  cleanAIProvider,
  translateAIProvider,
  onConfigChange,
  onOpenSettings,
}: FlowOptionsProps) {
  return (
    <details className="flow-options">
      <summary>
        生成后流程：{config.enable_clean || config.enable_translate ? '含文本处理' : '仅转写'}
      </summary>
      <label className="check-row">
        <input
          type="checkbox"
          checked={config.enable_clean}
          disabled={!cleanAIReady}
          onChange={event =>
            onConfigChange(current => ({ ...current, enable_clean: event.target.checked }))
          }
        />
        自动整理
      </label>
      <label className="check-row">
        <input
          type="checkbox"
          checked={config.enable_translate}
          disabled={!translateAIReady}
          onChange={event =>
            onConfigChange(current => ({ ...current, enable_translate: event.target.checked }))
          }
        />
        自动翻译
      </label>
      {config.enable_translate && (
        <LanguagePicker
          mode="target"
          value={config.target_language}
          onChange={target_language =>
            onConfigChange(current => ({ ...current, target_language }))
          }
        />
      )}
      <p>
        勾选即同意在此流程中将字幕文本发送至设置中的服务商（整理：
        {cleanAIProvider?.name || '未配置'}；翻译：{translateAIProvider?.name || '未配置'}
        ）。可能产生服务商费用，处理地区依服务商设置；取消勾选后，新任务不再自动执行相应步骤；正在运行的任务请在任务面板中终止。音视频不会随这两个步骤上传。
      </p>
      {(!cleanAIReady || !translateAIReady) && (
        <button className="button secondary" onClick={onOpenSettings}>
          配置文本服务
        </button>
      )}
    </details>
  );
}
