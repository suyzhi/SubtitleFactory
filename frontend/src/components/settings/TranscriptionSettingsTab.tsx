import { useMemo, useState } from 'react';
import type * as api from '../../api/backend';
import type { AppSettings, PathValidationResult, TaskStatus } from '../../types';
import AppSelect from '../AppSelect';
import LanguagePicker from '../LanguagePicker';
import { ModelCatalogRow } from './ModelCatalogRow';
import type { ModelStatusResult } from './settingsHelpers';
import { PathSettingField, SettingsSection } from './SettingsPrimitives';

interface Props {
  draft: AppSettings;
  resolvedModel: string;
  resolvedSourceLanguage: string;
  externalPathsEnabled: boolean;
  modelStatus: ModelStatusResult | null;
  providerCards: api.AIProviderCard[];
  cloudAuthorizations: api.CloudAuthorization[];
  scannedModels: api.ScannedModel[];
  pathChecks: Record<string, PathValidationResult>;
  busy: boolean;
  validatingModel: string;
  preparingModel: string;
  modelTasks: Record<string, TaskStatus>;
  onUpdateDraft: (partial: Partial<AppSettings>) => void;
  onRefreshModels: () => void;
  onScanModelFolder: () => void;
  onImportScannedModel: (model: api.ScannedModel) => void;
  onValidateModel: (modelId: string) => void;
  onPrepareModel: (modelId: string, runtime: string, repair: boolean) => void;
  onRemoveModel: (model: api.TranscriptionModelStatus) => void;
  onUpdateCloudAuth: (granted: boolean) => void;
  onRequireDashscopeConfig: () => void;
  onChoosePath: (
    field: keyof AppSettings,
    kind: PathValidationResult['kind'],
    directory?: boolean,
  ) => void;
  onValidatePath: (kind: PathValidationResult['kind'], path: string) => void;
  onClearPath: (field: keyof AppSettings, kind: PathValidationResult['kind']) => void;
}

export function TranscriptionSettingsTab({
  draft,
  resolvedModel,
  resolvedSourceLanguage,
  externalPathsEnabled,
  modelStatus,
  providerCards,
  cloudAuthorizations,
  scannedModels,
  pathChecks,
  busy,
  validatingModel,
  preparingModel,
  modelTasks,
  onUpdateDraft,
  onRefreshModels,
  onScanModelFolder,
  onImportScannedModel,
  onValidateModel,
  onPrepareModel,
  onRemoveModel,
  onUpdateCloudAuth,
  onRequireDashscopeConfig,
  onChoosePath,
  onValidatePath,
  onClearPath,
}: Props) {
  const [modelSearch, setModelSearch] = useState('');
  const [modelLanguage, setModelLanguage] = useState('all');
  const [modelScenario, setModelScenario] = useState('all');
  const [modelFamily, setModelFamily] = useState('all');
  const [modelDevice, setModelDevice] = useState('all');
  const [modelReadyFilter, setModelReadyFilter] = useState('all');
  const [modelTimestamp, setModelTimestamp] = useState('all');

  const catalogFamilies = useMemo(
    () =>
      Array.from(
        new Set(
          (modelStatus?.models || [])
            .map(model => model.family)
            .filter((value): value is string => Boolean(value)),
        ),
      ).sort((left, right) => left.localeCompare(right)),
    [modelStatus],
  );

  const filteredModels = useMemo(() => {
    const search = modelSearch.trim().toLocaleLowerCase();
    return (modelStatus?.models || []).filter(model => {
      const searchable = [
        model.name,
        model.family,
        model.purpose,
        model.language_description,
        ...(model.tags || []),
        ...(model.scenarios || []),
        ...(model.strengths || []),
        ...(model.limitations || []),
      ]
        .filter(Boolean)
        .join(' ')
        .toLocaleLowerCase();
      if (search && !searchable.includes(search)) return false;
      if (
        modelLanguage !== 'all' &&
        !(model.languages || []).some(language => language === '*' || language === modelLanguage)
      ) {
        return false;
      }
      if (modelScenario !== 'all' && !(model.scenarios || []).includes(modelScenario)) return false;
      if (modelFamily !== 'all' && model.family !== modelFamily) return false;
      if (modelDevice !== 'all' && !(model.runtimes || []).some(item => item.id === modelDevice)) {
        return false;
      }
      if (modelReadyFilter === 'ready' && !(model.runtimes || []).some(item => item.model_ready)) {
        return false;
      }
      if (
        modelReadyFilter === 'download' &&
        !(model.runtimes || []).some(item => item.download_required)
      ) {
        return false;
      }
      if (modelTimestamp !== 'all' && model.timestamp_mode !== modelTimestamp) return false;
      return true;
    });
  }, [
    modelDevice,
    modelFamily,
    modelLanguage,
    modelReadyFilter,
    modelScenario,
    modelSearch,
    modelStatus,
    modelTimestamp,
  ]);

  const categoryOrder = [
    ...(modelStatus?.category_order || [
      'lightweight',
      'balanced',
      'performance',
      'english',
      'parakeet',
    ]),
    ...((modelStatus?.models || []).some(model => model.category_id === 'local') ? ['local'] : []),
  ];

  return (
    <>
      <SettingsSection
        title="默认转写"
        description="自动选择只使用已经下载且匹配源语言的模型，不会在后台下载大模型。"
      >
        {modelStatus?.recommendation_reason && (
          <div className="settings-notice">{modelStatus.recommendation_reason}</div>
        )}
        <label className="settings-field horizontal">
          <span>
            <strong>默认模型</strong>
            <small>没有合适的已下载模型时安全回退到 Whisper Small</small>
          </span>
          <AppSelect
            value={resolvedModel}
            onChange={default_model => onUpdateDraft({ default_model })}
            label="默认模型"
            searchable
            options={[
              {
                value: 'auto',
                label: '智能自动选择',
                description: '按源语言选择已下载模型 · 不自动下载 · 安全回退',
              },
              ...(modelStatus?.models || []).map(model => ({
                value: model.id,
                label: model.name,
                description: [model.category_name, model.language_description, model.size_label]
                  .filter(Boolean)
                  .join(' · '),
              })),
            ]}
          />
        </label>
        <label className="settings-field horizontal">
          <span>
            <strong>源语言</strong>
            <small>可搜索常用语言、粤语和吴语</small>
          </span>
          <LanguagePicker
            value={resolvedSourceLanguage}
            onChange={source_language => onUpdateDraft({ source_language })}
          />
        </label>
      </SettingsSection>

      <SettingsSection
        title="模型管理"
        description="下载对应运行设备的固定版本；本地导入和已有缓存保持原位。"
        action={
          <div className="inline-actions">
            {externalPathsEnabled && (
              <button className="button secondary" onClick={onScanModelFolder}>
                导入本地模型
              </button>
            )}
            <button className="button secondary" onClick={onRefreshModels}>
              刷新状态
            </button>
          </div>
        }
      >
        <div className="model-catalog-filters" aria-label="模型筛选">
          <label className="model-search">
            <span>搜索模型</span>
            <input
              value={modelSearch}
              onChange={event => setModelSearch(event.target.value)}
              placeholder="名称、语言、场景或特点"
            />
          </label>
          <AppSelect
            value={modelLanguage}
            onChange={setModelLanguage}
            label="按语言筛选"
            options={[
              { value: 'all', label: '全部语言' },
              { value: 'zh', label: '普通话' },
              { value: 'yue', label: '粤语' },
              { value: 'wuu', label: '吴语' },
              { value: 'en', label: '英语' },
              { value: 'ja', label: '日语' },
              { value: 'ko', label: '韩语' },
              { value: 'ru', label: '俄语' },
            ]}
          />
          <AppSelect
            value={modelScenario}
            onChange={setModelScenario}
            label="按场景筛选"
            options={[
              { value: 'all', label: '全部场景' },
              { value: '通用字幕', label: '通用字幕' },
              { value: '低配置', label: '低配置' },
              { value: '高精度', label: '高精度' },
              { value: '方言', label: '方言' },
              { value: '电话录音', label: '电话录音' },
              { value: '医疗', label: '医疗' },
              { value: '声音事件', label: '声音事件' },
              { value: '歌词与说唱', label: '歌词与说唱' },
            ]}
          />
          <AppSelect
            value={modelFamily}
            onChange={setModelFamily}
            label="按模型家族筛选"
            options={[
              { value: 'all', label: '全部家族' },
              ...catalogFamilies.map(family => ({ value: family, label: family })),
            ]}
          />
          <AppSelect
            value={modelDevice}
            onChange={setModelDevice}
            label="按设备筛选"
            options={[
              { value: 'all', label: '全部设备' },
              { value: 'cpu', label: 'CPU' },
              { value: 'coreml', label: 'Core ML' },
              { value: 'mlx', label: 'Apple GPU' },
              { value: 'dashscope_cloud', label: '云端' },
            ]}
          />
          <AppSelect
            value={modelReadyFilter}
            onChange={setModelReadyFilter}
            label="按状态筛选"
            options={[
              { value: 'all', label: '全部状态' },
              { value: 'ready', label: '已下载' },
              { value: 'download', label: '可下载' },
            ]}
          />
          <AppSelect
            value={modelTimestamp}
            onChange={setModelTimestamp}
            label="按时间戳筛选"
            options={[
              { value: 'all', label: '全部时间轴' },
              { value: 'word', label: '逐词时间戳' },
              { value: 'token', label: '逐字/词元时间戳' },
              { value: 'segment', label: '语音片段级' },
            ]}
          />
          <button
            className="button secondary"
            onClick={() => {
              setModelSearch('');
              setModelLanguage('all');
              setModelScenario('all');
              setModelFamily('all');
              setModelDevice('all');
              setModelReadyFilter('all');
              setModelTimestamp('all');
            }}
          >
            清除筛选
          </button>
        </div>

        <div className="model-catalog">
          {!modelStatus && <div className="settings-empty">正在读取模型状态…</div>}
          {modelStatus && !filteredModels.length && (
            <div className="settings-empty">没有符合当前筛选条件的模型。</div>
          )}
          {categoryOrder.map(categoryId => {
            const models = filteredModels.filter(model => model.category_id === categoryId);
            if (!models.length) return null;
            return (
              <details className="model-category" key={categoryId}>
                <summary>
                  <strong>{models[0].category_name || categoryId}</strong>
                  <span>{models.length} 个模型</span>
                </summary>
                <div className="model-list">
                  {models.map(model => (
                    <ModelCatalogRow
                      key={model.id}
                      model={model}
                      recommendedModel={modelStatus?.recommended_model}
                      draft={draft}
                      providerCards={providerCards}
                      cloudAuthorizations={cloudAuthorizations}
                      busy={busy}
                      validatingModel={validatingModel}
                      preparingModel={preparingModel}
                      modelTasks={modelTasks}
                      onUpdateDraft={onUpdateDraft}
                      onValidateModel={onValidateModel}
                      onPrepareModel={onPrepareModel}
                      onRemoveModel={onRemoveModel}
                      onUpdateCloudAuth={onUpdateCloudAuth}
                      onRequireDashscopeConfig={onRequireDashscopeConfig}
                    />
                  ))}
                </div>
              </details>
            );
          })}
        </div>

        {externalPathsEnabled && !!scannedModels.length && (
          <div className="scanned-models">
            {scannedModels.map(model => (
              <div className="model-row" key={model.path}>
                <span className={`status-orb ${model.supported ? 'ok' : 'warning'}`} />
                <div>
                  <strong>{model.display_name}</strong>
                  <small>
                    {model.format} · {model.version || '未标版本'} ·{' '}
                    {model.supported ? model.reason || '可原地引用' : model.reason}
                  </small>
                </div>
                {model.supported && (
                  <button
                    className="button secondary model-action"
                    disabled={busy || Boolean(model.reason)}
                    onClick={() => onImportScannedModel(model)}
                  >
                    登记
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </SettingsSection>

      {externalPathsEnabled && (
        <SettingsSection
          title="自定义运行时"
          description="路径只保存在本机，不会写入项目、日志或发布包。"
        >
          <PathSettingField
            label="自定义模型目录"
            field="custom_model_path"
            kind="model"
            placeholder="选择包含模型文件的目录"
            directory
            draft={draft}
            check={pathChecks.model}
            busy={busy}
            onChoosePath={onChoosePath}
            onValidatePath={onValidatePath}
            onClearPath={onClearPath}
          />
          <PathSettingField
            label="外部 Core ML 目录"
            field="coreml_model_path"
            kind="coreml_model"
            placeholder="可选；留空时使用 App 下载的官方模型"
            directory
            draft={draft}
            check={pathChecks.coreml_model}
            busy={busy}
            onChoosePath={onChoosePath}
            onValidatePath={onValidatePath}
            onClearPath={onClearPath}
          />
          <PathSettingField
            label="转写 CLI"
            field="coreml_cli_path"
            kind="cli"
            placeholder="可选可执行文件"
            draft={draft}
            check={pathChecks.cli}
            busy={busy}
            onChoosePath={onChoosePath}
            onValidatePath={onValidatePath}
            onClearPath={onClearPath}
          />
        </SettingsSection>
      )}
    </>
  );
}
