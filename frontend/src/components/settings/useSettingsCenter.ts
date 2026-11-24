import { useEffect, useRef, useState, type RefObject } from 'react';
import * as api from '../../api/backend';
import type {
  AppSettingWarning,
  AppSettings,
  HealthStatus,
  PathValidationResult,
  ProcessingConfig,
  TaskStatus,
} from '../../types';
import { bytes, type ModelStatusResult, type SettingsCategory } from './settingsHelpers';

interface UseSettingsCenterOptions {
  open: boolean;
  onClose: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
  config: ProcessingConfig;
  onConfigChange: (config: ProcessingConfig) => void;
  appSettings: AppSettings;
  onAppSettingsChange: (settings: AppSettings) => void;
  onAIProvidersChange: (state: api.AIProvidersResponse) => void;
  health: HealthStatus | null;
  onRefreshHealth: () => void;
  modelStatus: ModelStatusResult | null;
  onRefreshModels: () => void;
}

export function useSettingsCenter({
  open,
  onClose,
  returnFocusRef,
  config,
  onConfigChange,
  appSettings,
  onAppSettingsChange,
  onAIProvidersChange,
  health,
  onRefreshHealth,
  modelStatus,
  onRefreshModels,
}: UseSettingsCenterOptions) {
  const [category, setCategory] = useState<SettingsCategory>('general');
  const [draft, setDraft] = useState<AppSettings>(appSettings);
  const [warnings, setWarnings] = useState<AppSettingWarning[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [pathChecks, setPathChecks] = useState<Record<string, PathValidationResult>>({});
  const [preparingModel, setPreparingModel] = useState('');
  const [modelTasks, setModelTasks] = useState<Record<string, TaskStatus>>({});
  const [backupState, setBackupState] = useState<api.BackupsResponse>({
    directory: '',
    backups: [],
    pending_restore: null,
    last_restore: null,
  });
  const [validatingModel, setValidatingModel] = useState('');
  const [providerCards, setProviderCards] = useState<api.AIProviderCard[]>([]);
  const [cloudAuthorizations, setCloudAuthorizations] = useState<api.CloudAuthorization[]>([]);
  const [assignments, setAssignments] = useState<api.AIAssignments>({
    clean_provider_id: 'deepseek',
    translate_provider_id: 'deepseek',
    content_provider_id: 'deepseek',
  });
  const [scannedModels, setScannedModels] = useState<api.ScannedModel[]>([]);

  const dialogRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const loadProviders = async () => {
      try {
        return await api.getAIProviders();
      } catch {
        // Retry a transient local API failure once while the backend starts.
        try {
          return await api.getAIProviders();
        } catch {
          return null;
        }
      }
    };
    setMessage('');
    setError('');
    void Promise.all([
      api.getAppSettings(),
      loadProviders(),
      api.getCloudAuthorizations().catch(() => ({ authorizations: [] })),
    ])
      .then(([app, providers, authorizations]) => {
        if (cancelled) return;
        setDraft(app.settings || {});
        setWarnings(app.warnings || []);
        if (providers) {
          setProviderCards(providers.providers);
          setAssignments(providers.assignments);
          onAIProvidersChange(providers);
        }
        setCloudAuthorizations(authorizations.authorizations);
        if (!providers) setError('AI 凭据暂时无法读取；本地转写和其他设置仍可使用。');
      })
      .catch(() => {
        if (!cancelled) setError('设置数据暂时无法读取，请关闭设置后重试。');
      });
    onRefreshHealth();
    onRefreshModels();
    return () => {
      cancelled = true;
    };
  }, [onAIProvidersChange, open, onRefreshHealth, onRefreshModels]);

  useEffect(() => {
    if (!open || category !== 'storage') return;
    void api.getBackups().then(setBackupState).catch(reason => setError(reason.message));
  }, [category, open]);

  useEffect(() => {
    if (!open) return;
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const explicitReturnFocus = returnFocusRef?.current;
    const frame = window.requestAnimationFrame(() => {
      const dialog = dialogRef.current;
      const activeCategory = dialog?.querySelector<HTMLButtonElement>(
        '.settings-navigation button.active',
      );
      (activeCategory || dialog)?.focus();
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== 'Tab') return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter(element => !element.hasAttribute('hidden') && element.getClientRects().length > 0);
      if (!focusable.length) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const current = document.activeElement;
      if (event.shiftKey && (current === first || !dialog.contains(current))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (current === last || !dialog.contains(current))) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('keydown', onKeyDown);
      (explicitReturnFocus || previousFocus)?.focus();
    };
  }, [open, returnFocusRef]);

  const resolvedSourceLanguage = String(draft.source_language || config.language);
  const resolvedTargetLanguage = String(
    draft.translation_target_language || config.target_language,
  );
  const resolvedModel = String(draft.default_model || config.model);

  const updateDraft = (partial: Partial<AppSettings>) =>
    setDraft(current => ({ ...current, ...partial }));

  const saveApp = async (partial: Partial<AppSettings> = draft) => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await api.saveAppSettings(partial);
      setDraft(result.settings);
      onAppSettingsChange(result.settings);
      setWarnings(result.warnings || []);
      onConfigChange({
        ...config,
        model: String(result.settings.default_model || resolvedModel),
        language: String(result.settings.source_language || resolvedSourceLanguage),
        target_language: String(
          result.settings.translation_target_language || resolvedTargetLanguage,
        ),
      });
      setMessage('设置已保存');
      onRefreshHealth();
      onRefreshModels();
    } catch (reason: any) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  };

  const validatePath = async (kind: PathValidationResult['kind'], path: string) => {
    if (!path.trim()) return;
    setBusy(true);
    setError('');
    try {
      const result = await api.validateAppPath({ kind, path: path.trim() });
      setPathChecks(current => ({ ...current, [kind]: result }));
      setMessage(result.ok ? '路径校验通过' : result.reason || '路径不可用');
    } catch (reason: any) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  };

  const choosePath = async (
    field: keyof AppSettings,
    kind: PathValidationResult['kind'],
    directory = false,
  ) => {
    if (!(window as any).__TAURI_INTERNALS__) {
      setError('路径选择器仅在字幕工厂桌面 App 中可用；Web 预览不会读取本机绝对路径。');
      return;
    }
    try {
      const { open: openDialog } = await import('@tauri-apps/plugin-dialog');
      const selected = await openDialog({ directory, multiple: false });
      if (!selected || Array.isArray(selected)) return;
      updateDraft({ [field]: selected });
      await validatePath(kind, selected);
    } catch (reason: any) {
      setError(`无法打开路径选择器：${reason.message}`);
    }
  };

  const clearPath = (field: keyof AppSettings, kind: PathValidationResult['kind']) => {
    updateDraft({ [field]: '' });
    setPathChecks(current => {
      const next = { ...current };
      delete next[kind];
      return next;
    });
  };

  const prepareModel = async (modelId: string, runtime: string, repair: boolean) => {
    const taskKey = `${modelId}:${runtime}`;
    setError('');
    setMessage('');
    setPreparingModel(taskKey);
    try {
      const created = await api.prepareTranscriptionModel(modelId, runtime, repair);
      setMessage(created.message);
      const poll = async (attempt: number) => {
        const task = await api.getTaskStatus(created.task_id);
        setModelTasks(current => ({ ...current, [taskKey]: task }));
        if (['success', 'failed', 'cancelled', 'partial'].includes(task.status)) {
          setPreparingModel('');
          onRefreshModels();
          setMessage(task.status === 'success' ? '模型已准备就绪' : task.error || task.message);
          return;
        }
        if (attempt < 900) window.setTimeout(() => void poll(attempt + 1), 1000);
      };
      await poll(0);
    } catch (reason: any) {
      setError(reason.message);
      setPreparingModel('');
    }
  };

  const validateModel = async (modelId: string) => {
    setValidatingModel(modelId);
    setError('');
    setMessage('');
    try {
      const result = await api.validateTranscriptionModel(modelId);
      setMessage(
        result.ready
          ? `${result.name || modelId} 校验通过`
          : result.runtime_error || `${result.name || modelId} 尚未就绪`,
      );
      onRefreshModels();
    } catch (reason: any) {
      setError(reason.message);
    } finally {
      setValidatingModel('');
    }
  };

  const removeModel = async (model: api.TranscriptionModelStatus) => {
    if (!window.confirm(`移除“${model.name}”的本地模型文件吗？以后可以重新下载。`)) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await api.removeTranscriptionModel(model.id);
      setMessage(`${result.message}，释放 ${bytes(result.removed_bytes)}`);
      onRefreshModels();
    } catch (reason: any) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  };

  const scanModelFolder = async () => {
    try {
      const { open: openDialog } = await import('@tauri-apps/plugin-dialog');
      const path = await openDialog({ directory: true, multiple: false, title: '选择模型根目录' });
      if (typeof path !== 'string') return;
      setBusy(true);
      const result = await api.scanLocalModels(path);
      setScannedModels(result.models);
      setMessage(`发现 ${result.models.length} 个模型候选`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const importScannedModel = async (model: api.ScannedModel) => {
    try {
      await api.importLocalModel(model.path, model.cli_path);
      setMessage(`${model.display_name} 已登记`);
      onRefreshModels();
    } catch (reason: any) {
      setError(reason.message);
    }
  };

  const updateProvider = (id: string, patch: Partial<api.AIProviderCard>) =>
    setProviderCards(items =>
      items.map(item => (item.provider_id === id ? { ...item, ...patch } : item)),
    );

  const saveProvider = async (card: api.AIProviderCard) => {
    setBusy(true);
    try {
      const saved = await api.saveAIProvider(card.provider_id, card);
      const providers = providerCards.map(item =>
        item.provider_id === card.provider_id ? { ...item, ...saved } : item,
      );
      setProviderCards(providers);
      onAIProvidersChange({ providers, assignments });
      setMessage(`${card.name} 已保存`);
      onRefreshModels();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const testProvider = async (card: api.AIProviderCard) => {
    try {
      const result = await api.testAIProvider(card.provider_id);
      updateProvider(card.provider_id, {
        last_test_status: 'success',
        last_latency_ms: result.latency_ms,
      });
      setMessage(`${card.name} ${result.latency_ms}ms`);
    } catch (reason: any) {
      setError(reason.message);
    }
  };

  const saveAssignments = async () => {
    setBusy(true);
    try {
      await api.saveAIAssignments(assignments);
      onAIProvidersChange({ providers: providerCards, assignments });
      setMessage('任务分配已保存');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const updateTranscriptionCloudAuthorization = async (granted: boolean) => {
    const dashscope = providerCards.find(card => card.provider_id === 'dashscope');
    if (granted && !dashscope?.has_api_key) {
      setCategory('ai');
      setError('请先在 AI 服务中保存通义千问（百炼）API Key。');
      return;
    }
    if (
      granted &&
      !window.confirm(
        'Fun-Realtime-ASR 会把每次使用时所在项目的转写音频分段上传到阿里云百炼，可能产生费用；不会上传视频、文件路径或其他项目。此授权会保留到您主动撤销。确认授权吗？',
      )
    ) {
      return;
    }
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await api.setCloudAuthorization('transcription', granted, 'dashscope');
      const latest = await api.getCloudAuthorizations();
      setCloudAuthorizations(latest.authorizations);
      setMessage(
        granted ? '已授权 Fun-Realtime-ASR 上传转写音频' : '已撤销 Fun-Realtime-ASR 音频上传授权',
      );
      onRefreshModels();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const backupNow = async () => {
    setBusy(true);
    try {
      await api.createBackup();
      setBackupState(await api.getBackups());
      setMessage('数据库备份已完成');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const restoreSelectedBackup = async (name: string) => {
    if (
      !window.confirm(
        `恢复“${name}”会把数据库回到该备份的时间点。\n\n字幕工厂会先验证备份并保存当前数据库，然后安全重启；不会在当前进程中直接覆盖正在使用的 SQLite。继续吗？`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      await api.restoreBackup(name);
      setMessage('备份已验证，正在安全重启并应用恢复…');
      const restarting = await api.restartDesktopApp();
      if (!restarting) {
        setBackupState(await api.getBackups());
        setMessage(
          '备份已排队；当前是浏览器调试模式，请立即手动重启字幕工厂。重启前不要开始新任务',
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const copyDiagnostics = async () => {
    const runtime = health?.runtime;
    const safeDiagnostics = {
      version: health?.version,
      status: health?.status,
      runtime: {
        ffmpeg: runtime?.ffmpeg && {
          ok: runtime.ffmpeg.ok,
          status: runtime.ffmpeg.status,
          source: runtime.ffmpeg.source,
          version: runtime.ffmpeg.version,
        },
        ffprobe: runtime?.ffprobe && {
          ok: runtime.ffprobe.ok,
          status: runtime.ffprobe.status,
          source: runtime.ffprobe.source,
          version: runtime.ffprobe.version,
        },
        yt_dlp: runtime?.yt_dlp && {
          ok: runtime.yt_dlp.ok,
          status: runtime.yt_dlp.status,
          source: runtime.yt_dlp.source,
          version: runtime.yt_dlp.version,
        },
        deno: runtime?.deno && {
          ok: runtime.deno.ok,
          status: runtime.deno.status,
          source: runtime.deno.source,
          version: runtime.deno.version,
        },
        ejs: runtime?.ejs && {
          ok: runtime.ejs.ok,
          status: runtime.ejs.status,
          source: runtime.ejs.source,
          version: runtime.ejs.version,
        },
        disk: runtime?.disk && {
          ok: runtime.disk.ok,
          status: runtime.disk.status,
          free_bytes: runtime.disk.free_bytes,
        },
        models: modelStatus?.models.map(model => ({
          id: model.id,
          ready: model.ready,
          source: model.source,
          status: model.status,
        })),
      },
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(safeDiagnostics, null, 2));
      setMessage('诊断信息已复制（不包含本机路径和密钥）');
    } catch {
      setError('无法复制诊断信息');
    }
  };

  const exportDiagnostics = async () => {
    setError('');
    try {
      const saved = await api.downloadDiagnostics();
      setMessage(saved ? '脱敏诊断包已保存' : '已取消保存诊断包');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const openPrivacyPolicy = async () => {
    setError('');
    try {
      await api.openPublicLink('privacy');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法打开隐私政策');
    }
  };

  return {
    dialogRef,
    category,
    setCategory,
    draft,
    updateDraft,
    warnings,
    busy,
    message,
    setMessage,
    error,
    setError,
    pathChecks,
    preparingModel,
    modelTasks,
    backupState,
    validatingModel,
    providerCards,
    cloudAuthorizations,
    assignments,
    setAssignments,
    scannedModels,
    resolvedSourceLanguage,
    resolvedTargetLanguage,
    resolvedModel,
    saveApp,
    validatePath,
    choosePath,
    clearPath,
    prepareModel,
    validateModel,
    removeModel,
    scanModelFolder,
    importScannedModel,
    updateProvider,
    saveProvider,
    testProvider,
    saveAssignments,
    updateTranscriptionCloudAuthorization,
    backupNow,
    restoreSelectedBackup,
    copyDiagnostics,
    exportDiagnostics,
    openPrivacyPolicy,
  };
}
