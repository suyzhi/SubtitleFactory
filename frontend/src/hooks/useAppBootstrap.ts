import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react';
import * as api from '../api/backend';
import { loadAppBootstrap } from '../appBootstrap';
import { languageLabel } from '../languages';
import { resolveConfiguredModel, resolveRuntimeSelection } from '../transcriptionSelection';
import type { AppSettings, HealthStatus, ProcessingConfig, Project } from '../types';

interface UseAppBootstrapOptions {
  activeProject: Project | null;
  config: ProcessingConfig;
  setConfig: Dispatch<SetStateAction<ProcessingConfig>>;
  showProjectWorkspace: boolean;
  playlistDialogUrl: string | null;
  setLibraryRefresh: Dispatch<SetStateAction<number>>;
  setSelectedStep: Dispatch<SetStateAction<string | null>>;
  setToolsTab: Dispatch<SetStateAction<'process' | 'quality' | 'smart' | 'content'>>;
  setToolsOpen: Dispatch<SetStateAction<boolean>>;
  showToast: (message: string, duration?: number) => void;
}

export function useAppBootstrap({
  activeProject,
  config,
  setConfig,
  showProjectWorkspace,
  playlistDialogUrl,
  setLibraryRefresh,
  setSelectedStep,
  setToolsTab,
  setToolsOpen,
  showToast,
}: UseAppBootstrapOptions) {
  const [backendStatus, setBackendStatus] = useState<'connecting' | 'connected' | 'error'>(
    'connecting',
  );
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [aiProviderState, setAIProviderState] = useState<api.AIProvidersResponse | null>(null);
  const [modelError, setModelError] = useState('');
  const [modelStatus, setModelStatus] = useState<Awaited<
    ReturnType<typeof api.getTranscriptionModels>
  > | null>(null);
  const [appSettings, setAppSettings] = useState<AppSettings>({
    default_workflow: 'automatic',
    auto_save: true,
    startup_behavior: 'restore_last',
    youtube_media_mode: 'local',
  });
  const [transcriptionRuntimes, setTranscriptionRuntimes] = useState<Record<string, string>>(() => {
    try {
      return JSON.parse(localStorage.getItem('subtitle_factory_transcription_runtimes') || '{}');
    } catch {
      return {};
    }
  });

  const modelRequest = useRef(0);

  const applyAppSettings = useCallback((settings: AppSettings) => {
    setAppSettings(settings);
    const persisted = settings.transcription_runtime_by_model || {};
    setTranscriptionRuntimes(current => {
      const next = { ...current, ...persisted };
      localStorage.setItem('subtitle_factory_transcription_runtimes', JSON.stringify(next));
      return next;
    });
  }, []);

  const refreshModels = useCallback(() => {
    if (backendStatus !== 'connected') return;
    const sequence = ++modelRequest.current;
    setModelError('');
    api
      .getTranscriptionModels(activeProject?.id, config.language)
      .then(result => {
        if (sequence === modelRequest.current) setModelStatus(result);
      })
      .catch(error => {
        if (sequence === modelRequest.current) setModelError(error.message);
      });
  }, [activeProject?.id, backendStatus, config.language]);

  const refreshAIProviders = useCallback(async () => {
    try {
      const latest = await api.getAIProviders();
      setAIProviderState(latest);
    } catch {
      // Local media and transcription remain available when Keychain access is
      // temporarily unavailable. SettingsCenter provides the visible retry UI.
    }
  }, []);

  const refreshHealth = useCallback(() => {
    api.checkHealth().then(setHealth).catch(() => undefined);
  }, []);

  // Wait for bundled backend & bootstrap initial settings
  useEffect(() => {
    let stopped = false;
    let attempts = 0;
    const connect = async () => {
      try {
        const healthResult = await api.checkHealth();
        if (stopped) return;
        setHealth(healthResult);
        setBackendStatus('connected');
        const startup = await loadAppBootstrap(false);
        if (!stopped) {
          setLibraryRefresh(value => value + 1);
          applyAppSettings(startup.app.settings);
          setConfig(current => ({
            ...current,
            model: String(startup.app.settings.default_model || current.model),
            language: String(startup.app.settings.source_language || current.language),
            target_language: String(
              startup.app.settings.translation_target_language || current.target_language,
            ),
          }));
        }
      } catch {
        if (stopped) return;
        attempts += 1;
        // 首次启动的内置转写运行时需要加载较多原生库；超时前保持“正在启动”。
        setBackendStatus(attempts < 80 ? 'connecting' : 'error');
        window.setTimeout(connect, attempts < 80 ? 750 : 5000);
      }
    };
    void connect();
    return () => {
      stopped = true;
    };
  }, [applyAppSettings, setConfig, setLibraryRefresh]);

  useEffect(refreshModels, [refreshModels]);

  useEffect(() => {
    if (!showProjectWorkspace && !playlistDialogUrl) return;
    void refreshAIProviders();
  }, [playlistDialogUrl, refreshAIProviders, showProjectWorkspace]);

  const compatibleModel = useCallback((): string | null => {
    const requestedModel = resolveConfiguredModel(config.model, modelStatus?.recommended_model);
    if (config.language === 'auto') return requestedModel;
    const model = modelStatus?.models.find(item => item.id === requestedModel);
    const supported = model?.languages || ['*'];
    if (supported.includes('*') || supported.includes(config.language)) return requestedModel;
    const switchModel = window.confirm(
      `${model?.name || '所选模型'}不支持${languageLabel(config.language)}。\n\n是否切换到 Whisper Small 后继续？`,
    );
    if (!switchModel) return null;
    setConfig(current => ({ ...current, model: 'small' }));
    showToast('已切换到 Whisper Small', 2600);
    return 'small';
  }, [config.language, config.model, modelStatus, setConfig, showToast]);

  const runtimeForModel = useCallback(
    (model: string) =>
      resolveRuntimeSelection(
        model,
        appSettings.transcription_runtime_by_model,
        transcriptionRuntimes,
        modelStatus?.models.find(item => item.id === model)?.selected_runtime,
      ),
    [appSettings.transcription_runtime_by_model, modelStatus, transcriptionRuntimes],
  );

  const chooseRuntime = useCallback(
    (model: string, runtime: string) => {
      setTranscriptionRuntimes(current => {
        const next = { ...current, [model]: runtime };
        localStorage.setItem('subtitle_factory_transcription_runtimes', JSON.stringify(next));
        return next;
      });
      setAppSettings(current => ({
        ...current,
        transcription_runtime_by_model: {
          ...(current.transcription_runtime_by_model || {}),
          [model]: runtime,
        },
      }));
      void api
        .saveAppSettings({ transcription_runtime_by_model: { [model]: runtime } })
        .then(result => applyAppSettings(result.settings))
        .catch(error =>
          showToast(
            `运行设备保存失败：${error instanceof Error ? error.message : String(error)}`,
            4200,
          ),
        );
    },
    [applyAppSettings, showToast],
  );

  const requireRuntime = useCallback(
    (model: string) => {
      const runtime = runtimeForModel(model);
      const option = modelStatus?.models
        .find(item => item.id === model)
        ?.runtimes?.find(item => item.id === runtime);
      if (!runtime || !option?.available) {
        setSelectedStep('transcribe');
        setToolsTab('process');
        setToolsOpen(true);
        showToast(
          !runtime
            ? '请选择转写运行设备：CPU、Apple GPU 或 Core ML'
            : '所选运行设备当前不可用，请重新选择',
          4200,
        );
        return '';
      }
      return runtime;
    },
    [modelStatus, runtimeForModel, setSelectedStep, setToolsOpen, setToolsTab, showToast],
  );

  const cleanAIProvider = aiProviderState?.providers.find(
    item => item.provider_id === aiProviderState.assignments.clean_provider_id,
  );
  const translateAIProvider = aiProviderState?.providers.find(
    item => item.provider_id === aiProviderState.assignments.translate_provider_id,
  );
  const cleanAIReady = Boolean(cleanAIProvider?.enabled && cleanAIProvider.has_api_key);
  const translateAIReady = Boolean(translateAIProvider?.enabled && translateAIProvider.has_api_key);

  return {
    backendStatus,
    health,
    refreshHealth,
    appSettings,
    applyAppSettings,
    aiProviderState,
    setAIProviderState,
    cleanAIProvider,
    translateAIProvider,
    cleanAIReady,
    translateAIReady,
    modelStatus,
    modelError,
    refreshModels,
    compatibleModel,
    runtimeForModel,
    chooseRuntime,
    requireRuntime,
  };
}
