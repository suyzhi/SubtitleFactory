import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import * as api from '../api/backend';
import type {
  AISettings, AppSettings, HealthStatus, PathValidationResult,
  ProcessingConfig, AppSettingWarning, TaskStatus,
} from '../types';
import LanguagePicker from './LanguagePicker';
import AppSelect from './AppSelect';
import { languageLabel } from '../languages';

type Category = 'general' | 'transcription' | 'ai' | 'translation' | 'storage' | 'appearance' | 'about';

const CATEGORIES: { id: Category; icon: string; label: string }[] = [
  { id: 'general', icon: '⌂', label: '通用' },
  { id: 'transcription', icon: '⌁', label: '转写' },
  { id: 'ai', icon: '✦', label: 'AI 服务' },
  { id: 'translation', icon: '文', label: '翻译' },
  { id: 'storage', icon: '⇩', label: '下载与存储' },
  { id: 'appearance', icon: '◐', label: '外观与动画' },
  { id: 'about', icon: 'ⓘ', label: '快捷键与关于' },
];

interface ModelStatusResult {
  recommended_model: string;
  recommendation_reason?: string;
  category_order?: string[];
  models: api.TranscriptionModelStatus[];
}

interface Props {
  open: boolean;
  onClose: () => void;
  config: ProcessingConfig;
  onConfigChange: (config: ProcessingConfig) => void;
  appSettings: AppSettings;
  onAppSettingsChange: (settings: AppSettings) => void;
  aiSettings: AISettings | null;
  onAISaved: (settings: AISettings) => void;
  theme: 'light' | 'dark';
  onThemeChange: (theme: 'light' | 'dark') => void;
  motionEnabled: boolean;
  onMotionEnabledChange: (enabled: boolean) => void;
  density: 'comfortable' | 'compact';
  onDensityChange: (density: 'comfortable' | 'compact') => void;
  health: HealthStatus | null;
  onRefreshHealth: () => void;
  modelStatus: ModelStatusResult | null;
  onRefreshModels: () => void;
  onOpenLogs: () => void;
}

function bytes(value?: number) {
  if (!value && value !== 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let current = value;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) { current /= 1024; index += 1; }
  return `${current >= 10 || index === 0 ? current.toFixed(0) : current.toFixed(1)} ${units[index]}`;
}

function runtimeCopy(item: Record<string, unknown> | undefined) {
  if (!item) return { ok: false, title: '尚未检查', detail: '打开设置后刷新运行状态' };
  const ok = Boolean(item.ok ?? item.available ?? (item.status === 'ready'));
  const title = String(item.message || item.status || (ok ? '可用' : '不可用'));
  const detail = item.source === 'bundled' ? 'App 内置' : String(item.path || item.resolved_path || item.source || '');
  return { ok, title, detail };
}

export default function SettingsCenter(props: Props) {
  const {
    open, onClose, config, onConfigChange, appSettings, onAppSettingsChange, theme, onThemeChange,
    motionEnabled, onMotionEnabledChange, density, onDensityChange, health, onRefreshHealth,
    modelStatus, onRefreshModels, onOpenLogs,
  } = props;
  const youtubeEnabled = api.youtubeFeaturesEnabled();
  const externalPathsEnabled = api.externalRuntimePathsEnabled();
  const distributionChannel = api.getDistributionChannel();
  const [category, setCategory] = useState<Category>('general');
  const [draft, setDraft] = useState<AppSettings>(appSettings);
  const [warnings, setWarnings] = useState<AppSettingWarning[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [pathChecks, setPathChecks] = useState<Record<string, PathValidationResult>>({});
  const [preparingModel, setPreparingModel] = useState('');
  const [modelTasks, setModelTasks] = useState<Record<string, TaskStatus>>({});
  const [backupState, setBackupState] = useState<{directory: string; backups: api.BackupRecord[]}>({ directory: '', backups: [] });
  const [validatingModel, setValidatingModel] = useState('');
  const [favoriteLanguage, setFavoriteLanguage] = useState('fr');
  const [providerCards,setProviderCards]=useState<api.AIProviderCard[]>([]);
  const [cloudAuthorizations,setCloudAuthorizations]=useState<api.CloudAuthorization[]>([]);
  const [assignments,setAssignments]=useState<api.AIAssignments>({
    clean_provider_id:'deepseek',
    translate_provider_id:'deepseek',
    content_provider_id:'deepseek',
  });
  const [scannedModels,setScannedModels]=useState<api.ScannedModel[]>([]);
  const [modelSearch, setModelSearch] = useState('');
  const [modelLanguage, setModelLanguage] = useState('all');
  const [modelScenario, setModelScenario] = useState('all');
  const [modelFamily, setModelFamily] = useState('all');
  const [modelDevice, setModelDevice] = useState('all');
  const [modelReadyFilter, setModelReadyFilter] = useState('all');
  const [modelTimestamp, setModelTimestamp] = useState('all');
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
        // macOS Keychain access can briefly fail while the app is becoming
        // active. Retry once before showing a warning so a transient read does
        // not make otherwise healthy settings look broken.
        try { return await api.getAIProviders(); }
        catch { return null; }
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
        }
        setCloudAuthorizations(authorizations.authorizations);
        if (!providers) setError('AI 凭据暂时无法读取；本地转写和其他设置仍可使用。');
      })
      .catch(() => {
        if (!cancelled) setError('设置数据暂时无法读取，请关闭设置后重试。');
      });
    onRefreshHealth();
    onRefreshModels();
    return () => { cancelled = true; };
  }, [open, onRefreshHealth, onRefreshModels]);

  useEffect(() => {
    if (!open || category !== 'storage') return;
    void api.getBackups().then(setBackupState).catch(reason => setError(reason.message));
  }, [category, open]);

  const backupNow = async () => {
    setBusy(true);
    try { await api.createBackup(); setBackupState(await api.getBackups()); setMessage('数据库备份已完成'); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  };
  const restoreSelectedBackup = async (name: string) => {
    if (!window.confirm(`恢复“${name}”会替换当前数据库，继续吗？恢复前会自动再创建安全备份。`)) return;
    setBusy(true);
    try { await api.restoreBackup(name); setMessage('备份已恢复，请重新启动字幕工厂'); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  };

  const scanModelFolder=async()=>{ try { const {open}=await import('@tauri-apps/plugin-dialog'); const path=await open({directory:true,multiple:false,title:'选择模型根目录'}); if(typeof path!=='string')return; setBusy(true); const result=await api.scanLocalModels(path); setScannedModels(result.models); setMessage(`发现 ${result.models.length} 个模型候选`); } catch(reason){setError(reason instanceof Error?reason.message:String(reason));} finally{setBusy(false);} };
  const updateProvider=(id:string,patch:Partial<api.AIProviderCard>)=>setProviderCards(items=>items.map(item=>item.provider_id===id?{...item,...patch}:item));
  const saveProvider=async(card:api.AIProviderCard)=>{setBusy(true);try{const saved=await api.saveAIProvider(card.provider_id,card);updateProvider(card.provider_id,saved);setMessage(`${card.name} 已保存`);onRefreshModels();}catch(reason){setError(reason instanceof Error?reason.message:String(reason));}finally{setBusy(false);}};
  const updateTranscriptionCloudAuthorization=async(granted:boolean)=>{
    const dashscope=providerCards.find(card=>card.provider_id==='dashscope');
    if(granted&&!dashscope?.has_api_key){setCategory('ai');setError('请先在 AI 服务中保存通义千问（百炼）API Key。');return;}
    if(granted&&!window.confirm('Fun-Realtime-ASR 会把每次使用时所在项目的转写音频分段上传到阿里云百炼，可能产生费用；不会上传视频、文件路径或其他项目。此授权会保留到您主动撤销。确认授权吗？'))return;
    setBusy(true);setError('');setMessage('');
    try{await api.setCloudAuthorization('transcription',granted,'dashscope');const latest=await api.getCloudAuthorizations();setCloudAuthorizations(latest.authorizations);setMessage(granted?'已授权 Fun-Realtime-ASR 上传转写音频':'已撤销 Fun-Realtime-ASR 音频上传授权');onRefreshModels();}
    catch(reason){setError(reason instanceof Error?reason.message:String(reason));}
    finally{setBusy(false);}
  };

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => {
      const dialog = dialogRef.current;
      const activeCategory = dialog?.querySelector<HTMLButtonElement>('.settings-navigation button.active');
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
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )).filter(element => !element.hasAttribute('hidden') && element.getClientRects().length > 0);
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
      previousFocus?.focus();
    };
  }, [open]);

  const resolvedSourceLanguage = String(draft.source_language || config.language);
  const resolvedTargetLanguage = String(draft.translation_target_language || config.target_language);
  const resolvedModel = String(draft.default_model || config.model);
  const runtime = health?.runtime;
  const catalogFamilies = useMemo(() => Array.from(new Set(
    (modelStatus?.models || []).map(model => model.family).filter((value): value is string => Boolean(value)),
  )).sort((left, right) => left.localeCompare(right)), [modelStatus]);
  const filteredModels = useMemo(() => {
    const search = modelSearch.trim().toLocaleLowerCase();
    return (modelStatus?.models || []).filter(model => {
      const searchable = [
        model.name, model.family, model.purpose, model.language_description,
        ...(model.tags || []), ...(model.scenarios || []),
        ...(model.strengths || []), ...(model.limitations || []),
      ].filter(Boolean).join(' ').toLocaleLowerCase();
      if (search && !searchable.includes(search)) return false;
      if (modelLanguage !== 'all' && !(model.languages || []).some(language => language === '*' || language === modelLanguage)) return false;
      if (modelScenario !== 'all' && !(model.scenarios || []).includes(modelScenario)) return false;
      if (modelFamily !== 'all' && model.family !== modelFamily) return false;
      if (modelDevice !== 'all' && !(model.runtimes || []).some(item => item.id === modelDevice)) return false;
      if (modelReadyFilter === 'ready' && !(model.runtimes || []).some(item => item.model_ready)) return false;
      if (modelReadyFilter === 'download' && !(model.runtimes || []).some(item => item.download_required)) return false;
      if (modelTimestamp !== 'all' && model.timestamp_mode !== modelTimestamp) return false;
      return true;
    });
  }, [modelDevice, modelFamily, modelLanguage, modelReadyFilter, modelScenario, modelSearch, modelStatus, modelTimestamp]);

  const modelSourceLabels = useMemo(() => ({
    bundled: '内置', app_download: 'App 下载', external_detected: '外部检测', custom: '自定义路径',
    environment: '环境变量', unavailable: '不可用', huggingface: 'Hugging Face',
    github: 'GitHub', legacy_cache: '已有缓存', custom_path: '本地目录',
    local_selection: '本地目录',
    dashscope: '阿里云百炼',
  } as Record<string, string>), []);

  if (!open) return null;

  const updateDraft = (partial: Partial<AppSettings>) => setDraft(current => ({ ...current, ...partial }));

  const saveApp = async (partial: Partial<AppSettings> = draft) => {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await api.saveAppSettings(partial);
      setDraft(result.settings);
      onAppSettingsChange(result.settings);
      setWarnings(result.warnings || []);
      onConfigChange({
        ...config,
        model: String(result.settings.default_model || resolvedModel),
        language: String(result.settings.source_language || resolvedSourceLanguage),
        target_language: String(result.settings.translation_target_language || resolvedTargetLanguage),
      });
      setMessage('设置已保存');
      onRefreshHealth();
      onRefreshModels();
    } catch (reason: any) { setError(reason.message); }
    finally { setBusy(false); }
  };

  const validatePath = async (kind: PathValidationResult['kind'], path: string) => {
    if (!path.trim()) return;
    setBusy(true); setError('');
    try {
      const result = await api.validateAppPath({ kind, path: path.trim() });
      setPathChecks(current => ({ ...current, [kind]: result }));
      setMessage(result.ok ? '路径校验通过' : result.reason || '路径不可用');
    } catch (reason: any) { setError(reason.message); }
    finally { setBusy(false); }
  };

  const choosePath = async (field: keyof AppSettings, kind: PathValidationResult['kind'], directory = false) => {
    if (!(window as any).__TAURI_INTERNALS__) {
      setError('路径选择器仅在字幕工厂桌面 App 中可用；Web 预览不会读取本机绝对路径。');
      return;
    }
    try {
      const { open } = await import('@tauri-apps/plugin-dialog');
      const selected = await open({ directory, multiple: false });
      if (!selected || Array.isArray(selected)) return;
      updateDraft({ [field]: selected });
      await validatePath(kind, selected);
    } catch (reason: any) {
      setError(`无法打开路径选择器：${reason.message}`);
    }
  };

  const prepareModel = async (modelId: string, runtime: string, repair: boolean) => {
    const taskKey = `${modelId}:${runtime}`;
    setError(''); setMessage(''); setPreparingModel(taskKey);
    try {
      const created = await api.prepareTranscriptionModel(modelId, runtime, repair);
      setMessage(created.message);
      const poll = async (attempt: number) => {
        const task = await api.getTaskStatus(created.task_id);
        setModelTasks(current => ({ ...current, [taskKey]: task }));
        if (['success', 'failed', 'cancelled', 'partial'].includes(task.status)) {
          setPreparingModel(''); onRefreshModels();
          setMessage(task.status === 'success' ? '模型已准备就绪' : task.error || task.message);
          return;
        }
        if (attempt < 900) window.setTimeout(() => void poll(attempt + 1), 1000);
      };
      await poll(0);
    } catch (reason: any) {
      setError(reason.message); setPreparingModel('');
    }
  };

  const validateModel = async (modelId: string) => {
    setValidatingModel(modelId); setError(''); setMessage('');
    try {
      const result = await api.validateTranscriptionModel(modelId);
      setMessage(result.ready ? `${result.name || modelId} 校验通过` : result.runtime_error || `${result.name || modelId} 尚未就绪`);
      onRefreshModels();
    } catch (reason: any) { setError(reason.message); }
    finally { setValidatingModel(''); }
  };

  const removeModel = async (model: api.TranscriptionModelStatus) => {
    if (!window.confirm(`移除“${model.name}”的本地模型文件吗？以后可以重新下载。`)) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await api.removeTranscriptionModel(model.id);
      setMessage(`${result.message}，释放 ${bytes(result.removed_bytes)}`);
      onRefreshModels();
    } catch (reason: any) { setError(reason.message); }
    finally { setBusy(false); }
  };

  const addFavoriteLanguage = () => {
    const language = favoriteLanguage.trim();
    if (!language || language === 'auto' || language === 'none') return;
    const current = draft.favorite_languages || ['zh', 'en', 'ja', 'ko'];
    if (!current.includes(language)) updateDraft({ favorite_languages: [...current, language] });
  };

  const copyDiagnostics = async () => {
    const runtime = health?.runtime;
    const safeDiagnostics = {
      version: health?.version,
      status: health?.status,
      runtime: {
        ffmpeg: runtime?.ffmpeg && { ok: runtime.ffmpeg.ok, status: runtime.ffmpeg.status, source: runtime.ffmpeg.source, version: runtime.ffmpeg.version },
        ffprobe: runtime?.ffprobe && { ok: runtime.ffprobe.ok, status: runtime.ffprobe.status, source: runtime.ffprobe.source, version: runtime.ffprobe.version },
        yt_dlp: runtime?.yt_dlp && { ok: runtime.yt_dlp.ok, status: runtime.yt_dlp.status, source: runtime.yt_dlp.source, version: runtime.yt_dlp.version },
        deno: runtime?.deno && { ok: runtime.deno.ok, status: runtime.deno.status, source: runtime.deno.source, version: runtime.deno.version },
        ejs: runtime?.ejs && { ok: runtime.ejs.ok, status: runtime.ejs.status, source: runtime.ejs.source, version: runtime.ejs.version },
        disk: runtime?.disk && { ok: runtime.disk.ok, status: runtime.disk.status, free_bytes: runtime.disk.free_bytes },
        models: modelStatus?.models.map(model => ({ id: model.id, ready: model.ready, source: model.source, status: model.status })),
      },
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(safeDiagnostics, null, 2));
      setMessage('诊断信息已复制（不包含本机路径和密钥）');
    } catch { setError('无法复制诊断信息'); }
  };

  const renderPath = (
    label: string, field: keyof AppSettings, kind: PathValidationResult['kind'], placeholder: string, directory = false,
  ) => {
    const value = String(draft[field] || '');
    const check = pathChecks[kind];
    return <div className="settings-field path-setting">
      <span><strong>{label}</strong><small>{placeholder}</small></span>
      <div className="path-control">
        <input readOnly value={value} placeholder={placeholder}/>
        <button className="button secondary" onClick={() => void choosePath(field, kind, directory)}>选择…</button>
        <button className="button secondary" disabled={!value || busy} onClick={() => validatePath(kind, value)}>校验</button>
        {value && <button className="button secondary" onClick={() => { updateDraft({ [field]: '' }); setPathChecks(current => { const next = { ...current }; delete next[kind]; return next; }); }}>清除</button>}
      </div>
      {check && <small className={`path-result ${check.ok ? 'success' : 'failure'}`}>{check.ok ? '✓' : '!'} {check.resolved_path || check.reason || (check.ok ? '路径可用' : '路径不可用')}</small>}
    </div>;
  };

  return createPortal(
    <div className={`modal-backdrop settings-backdrop theme-${theme}`} role="presentation" onMouseDown={onClose}>
      <section ref={dialogRef} tabIndex={-1} className="settings-center" role="dialog" aria-modal="true" aria-label="设置中心" onMouseDown={event => event.stopPropagation()}>
        <aside className="settings-navigation">
          <div className="settings-title"><strong>设置</strong><small>字幕工厂</small></div>
          <nav aria-label="设置分类">
            {CATEGORIES.map(item => <button key={item.id} className={category === item.id ? 'active' : ''} onClick={() => { setCategory(item.id); setMessage(''); setError(''); }}>
              <i>{item.icon}</i><span>{item.label}</span>
            </button>)}
          </nav>
          <small className="settings-version">Version {health?.version || '0.3.2'}</small>
        </aside>

        <div className="settings-content">
          <header className="settings-content-header">
            <div><h2>{CATEGORIES.find(item => item.id === category)?.label}</h2><p>更改会保存在这台 Mac 上。</p></div>
            <button className="icon-button" aria-label="关闭设置" onClick={onClose}>×</button>
          </header>

          <div className="settings-scroll" key={category}>
            {warnings.map(warning => <div className="settings-notice warning" key={`${warning.field}:${warning.code}`}>! {warning.message}</div>)}

            {category === 'general' && <>
              <SettingsSection title="默认工作流" description="下载与本地导入均可自动完成音频提取和转写。">
                <Segmented value={String(draft.default_workflow || 'automatic')} onChange={value => updateDraft({ default_workflow: value })}
                  options={[['automatic', '自动'], ['manual', '手动']]}/>
                <Toggle label="自动保存字幕编辑" detail="当前编辑器会在确认单行编辑后立即写入本地项目" checked={draft.auto_save !== false} onChange={auto_save => updateDraft({ auto_save })}/>
              </SettingsSection>
              <SettingsSection title="启动" description="选择 App 打开时看到的内容。">
                <label className="settings-field horizontal"><span><strong>启动行为</strong><small>不影响正在运行的后台任务</small></span>
                  <AppSelect value={draft.startup_behavior||'restore_last'} onChange={startup_behavior=>updateDraft({startup_behavior:startup_behavior as AppSettings['startup_behavior']})} label="启动行为" options={[{value:'restore_last',label:'打开上次项目'},{value:'project_library',label:'显示项目库'}]}/>
                </label>
              </SettingsSection>
            </>}

            {category === 'transcription' && <>
              <SettingsSection title="默认转写" description="自动选择只使用已经下载且匹配源语言的模型，不会在后台下载大模型。">
                {modelStatus?.recommendation_reason && <div className="settings-notice">{modelStatus.recommendation_reason}</div>}
                <label className="settings-field horizontal"><span><strong>默认模型</strong><small>没有合适的已下载模型时安全回退到 Whisper Small</small></span>
                  <AppSelect value={resolvedModel} onChange={default_model=>updateDraft({default_model})} label="默认模型" searchable options={[{value:'auto',label:'智能自动选择',description:'按源语言选择已下载模型 · 不自动下载 · 安全回退'},...(modelStatus?.models||[]).map(model=>({value:model.id,label:model.name,description:[model.category_name,model.language_description,model.size_label].filter(Boolean).join(' · ')}))]}/>
                </label>
                <label className="settings-field horizontal"><span><strong>源语言</strong><small>可搜索常用语言、粤语和吴语</small></span>
                  <LanguagePicker value={resolvedSourceLanguage} onChange={source_language => updateDraft({ source_language })}/>
                </label>
              </SettingsSection>
              <SettingsSection title="模型管理" description="下载对应运行设备的固定版本；本地导入和已有缓存保持原位。" action={<div className="inline-actions">{externalPathsEnabled && <button className="button secondary" onClick={scanModelFolder}>导入本地模型</button>}<button className="button secondary" onClick={onRefreshModels}>刷新状态</button></div>}>
                <div className="model-catalog-filters" aria-label="模型筛选">
                  <label className="model-search"><span>搜索模型</span><input value={modelSearch} onChange={event => setModelSearch(event.target.value)} placeholder="名称、语言、场景或特点"/></label>
                  <AppSelect value={modelLanguage} onChange={setModelLanguage} label="按语言筛选" options={[
                    {value:'all',label:'全部语言'}, {value:'zh',label:'普通话'}, {value:'yue',label:'粤语'},
                    {value:'wuu',label:'吴语'}, {value:'en',label:'英语'}, {value:'ja',label:'日语'},
                    {value:'ko',label:'韩语'}, {value:'ru',label:'俄语'},
                  ]}/>
                  <AppSelect value={modelScenario} onChange={setModelScenario} label="按场景筛选" options={[
                    {value:'all',label:'全部场景'}, {value:'通用字幕',label:'通用字幕'}, {value:'低配置',label:'低配置'},
                    {value:'高精度',label:'高精度'}, {value:'方言',label:'方言'}, {value:'电话录音',label:'电话录音'},
                    {value:'医疗',label:'医疗'}, {value:'声音事件',label:'声音事件'}, {value:'歌词与说唱',label:'歌词与说唱'},
                  ]}/>
                  <AppSelect value={modelFamily} onChange={setModelFamily} label="按模型家族筛选" options={[
                    {value:'all',label:'全部家族'}, ...catalogFamilies.map(family => ({value:family,label:family})),
                  ]}/>
                  <AppSelect value={modelDevice} onChange={setModelDevice} label="按设备筛选" options={[
                    {value:'all',label:'全部设备'}, {value:'cpu',label:'CPU'}, {value:'coreml',label:'Core ML'}, {value:'mlx',label:'Apple GPU'}, {value:'dashscope_cloud',label:'云端'},
                  ]}/>
                  <AppSelect value={modelReadyFilter} onChange={setModelReadyFilter} label="按状态筛选" options={[
                    {value:'all',label:'全部状态'}, {value:'ready',label:'已下载'}, {value:'download',label:'可下载'},
                  ]}/>
                  <AppSelect value={modelTimestamp} onChange={setModelTimestamp} label="按时间戳筛选" options={[
                    {value:'all',label:'全部时间轴'}, {value:'word',label:'逐词时间戳'},
                    {value:'token',label:'逐字/词元时间戳'}, {value:'segment',label:'语音片段级'},
                  ]}/>
                  <button className="button secondary" onClick={() => {
                    setModelSearch(''); setModelLanguage('all'); setModelScenario('all'); setModelFamily('all');
                    setModelDevice('all'); setModelReadyFilter('all'); setModelTimestamp('all');
                  }}>清除筛选</button>
                </div>
                <div className="model-catalog">
                  {!modelStatus && <div className="settings-empty">正在读取模型状态…</div>}
                  {modelStatus && !filteredModels.length && <div className="settings-empty">没有符合当前筛选条件的模型。</div>}
                  {[...(modelStatus?.category_order || ['lightweight','balanced','performance','english','parakeet']), ...((modelStatus?.models || []).some(model => model.category_id === 'local') ? ['local'] : [])].map(categoryId => {
                    const models = filteredModels.filter(model => model.category_id === categoryId);
                    if (!models.length) return null;
                    return <section className="model-category" key={categoryId}>
                      <header><strong>{models[0].category_name || categoryId}</strong><span>{models.length} 个模型</span></header>
                      <div className="model-list">{models.map(model => {
                        const selectedRuntime = String((draft.transcription_runtime_by_model as Record<string, string> | undefined)?.[model.id] || model.selected_runtime || '');
                        const selectedVariant = model.runtimes?.find(runtime => runtime.id === selectedRuntime);
                        const ready = Boolean(selectedVariant?.model_ready);
                        const canDownload = Boolean(selectedVariant?.download_required);
                        const modelReady = Boolean(model.ready || model.runtimes?.some(runtime => runtime.model_ready));
                        const hasDownloadableRuntime = Boolean(model.runtimes?.some(runtime => runtime.download_required));
                        const isExternalCoreML = model.id === 'parakeet-tdt-0.6b-v3-coreml';
                        const isFunCloud = model.id === 'fun-asr-realtime';
                        const dashscopeConfigured = Boolean(providerCards.find(card => card.provider_id === 'dashscope')?.has_api_key);
                        const funCloudGranted = Boolean(cloudAuthorizations.find(item => item.capability === 'transcription' && item.provider_id === 'dashscope')?.granted);
                        const taskKey = `${model.id}:${selectedRuntime}`;
                        const task = modelTasks[taskKey];
                        const progress = task?.details?.model_download as Record<string, unknown> | undefined;
                        const source = String(selectedVariant?.source || model.source || (ready ? 'app_download' : 'unavailable'));
                        const progressBytes = Number(progress?.downloaded_bytes || 0);
                        const totalBytes = Number(progress?.total_bytes || selectedVariant?.download_bytes || 0);
                        return <article className="model-row model-catalog-row" key={model.id}>
                          <span className={`status-orb ${modelReady ? 'ok' : hasDownloadableRuntime ? 'warning' : 'error'}`}/>
                          <div className="model-copy">
                            <strong>{model.name}{model.id === modelStatus?.recommended_model && <b className="model-recommended">推荐</b>}</strong>
                            <small>{model.purpose} · {model.language_description} · {model.size_label}</small>
                            <span className="model-tags">
                              {model.family && <i>{model.family}</i>}
                              {model.speed_tier && <i>速度 {model.speed_tier}</i>}
                              {model.accuracy_tier && <i>精度 {model.accuracy_tier}</i>}
                              {model.memory_tier && <i>内存 {model.memory_tier}</i>}
                              {model.timestamp_mode && <i>{model.timestamp_mode === 'word' ? '逐词时间戳' : model.timestamp_mode === 'token' ? '逐字/词元时间戳' : '语音片段级时间轴'}</i>}
                              {model.punctuation_mode && <i>{model.punctuation_mode === 'native' ? '原生标点' : model.punctuation_mode === 'none' ? '不补标点' : '有限标点'}</i>}
                              {model.tags?.map(tag => <i key={tag}>{tag}</i>)}
                            </span>
                            {(model.strengths?.length || model.limitations?.length || model.scenarios?.length) && <details className="model-explanation">
                              <summary>查看适用场景和限制</summary>
                              {!!model.scenarios?.length && <p><strong>适合：</strong>{model.scenarios.join('、')}</p>}
                              {!!model.strengths?.length && <p><strong>优势：</strong>{model.strengths.join('；')}</p>}
                              {!!model.limitations?.length && <p><strong>不适合：</strong>{model.limitations.join('；')}</p>}
                              <p><strong>来源：</strong>{model.publisher || model.source_site || '官方模型'}{model.license ? ` · 许可证 ${model.license}` : ''}</p>
                            </details>}
                            {task && !['success','failed','cancelled'].includes(task.status) && <span className="model-progress">
                              <progress max={100} value={task.progress}/>
                              <small>{task.step === 'verifying_model' ? '正在校验 SHA-256' : `${bytes(progressBytes)} / ${bytes(totalBytes)} · ${task.progress.toFixed(1)}%${progress?.resumed ? ' · 断点续传' : ''}`}</small>
                            </span>}
                            {task?.status === 'failed' && <small className="model-error">{task.error || task.message}</small>}
                          </div>
                          <em title={selectedVariant?.repository || model.publisher}>{modelSourceLabels[source] || model.source_site || source}</em>
                          {!!model.runtimes?.length && <AppSelect
                            className="model-runtime-select"
                            value={selectedRuntime}
                            onChange={runtime => updateDraft({ transcription_runtime_by_model: { ...((draft.transcription_runtime_by_model as Record<string, string> | undefined) || {}), [model.id]: runtime } })}
                            label={`${model.name} 运行设备`}
                            placeholder="选择 CPU / GPU"
                            options={model.runtimes.map(runtime => ({ value: runtime.id, label: runtime.name, description: `${runtime.engine || ''}${runtime.model_ready ? ' · 已就绪' : runtime.download_required ? ` · 下载 ${bytes(runtime.download_bytes)}` : ''}`, disabled: !runtime.available }))}
                          />}
                          <span className="model-row-actions">
                            <button className="button secondary model-action" disabled={!!validatingModel} onClick={() => void validateModel(model.id)}>{validatingModel === model.id ? '校验中…' : '校验'}</button>
                            {isFunCloud
                              ? dashscopeConfigured
                                ? <button className={`button secondary model-action${funCloudGranted?' danger':''}`} disabled={busy} onClick={() => void updateTranscriptionCloudAuthorization(!funCloudGranted)}>{funCloudGranted?'撤销上传授权':'授权音频上传'}</button>
                                : <button className="button secondary model-action" onClick={() => { setCategory('ai'); setError('请先保存通义千问（百炼）API Key。'); }}>配置百炼</button>
                              : isExternalCoreML && externalPathsEnabled
                              ? <button className="button secondary model-action" onClick={() => void choosePath('coreml_model_path', 'coreml_model', true)}>重新选择目录</button>
                              : selectedRuntime && (ready || canDownload) && <button className="button secondary model-action" disabled={!!preparingModel} onClick={() => void prepareModel(model.id, selectedRuntime, ready)}>{preparingModel === taskKey ? '处理中…' : ready ? '修复' : '下载'}</button>}
                            {!isFunCloud && !isExternalCoreML && !selectedRuntime && (modelReady || hasDownloadableRuntime) && <small className="model-runtime-required">先选择运行设备</small>}
                            {modelReady && model.removable && <button className="button secondary model-action danger" disabled={busy || !!preparingModel} onClick={() => void removeModel(model)}>移除</button>}
                            {preparingModel === taskKey && task && <button className="button secondary model-action danger" onClick={() => void api.cancelTask(task.id)}>取消</button>}
                          </span>
                        </article>;
                      })}</div>
                    </section>;
                  })}
                </div>
                {externalPathsEnabled&&!!scannedModels.length&&<div className="scanned-models">{scannedModels.map(model=><div className="model-row" key={model.path}><span className={`status-orb ${model.supported?'ok':'warning'}`}/><div><strong>{model.display_name}</strong><small>{model.format} · {model.version||'未标版本'} · {model.supported?(model.reason||'可原地引用'):model.reason}</small></div>{model.supported&&<button className="button secondary model-action" disabled={busy||Boolean(model.reason)} onClick={()=>void api.importLocalModel(model.path,model.cli_path).then(()=>{setMessage(`${model.display_name} 已登记`);onRefreshModels();}).catch(reason=>setError(reason.message))}>登记</button>}</div>)}</div>}
              </SettingsSection>
              {externalPathsEnabled && <SettingsSection title="自定义运行时" description="路径只保存在本机，不会写入项目、日志或发布包。">
                {renderPath('自定义模型目录', 'custom_model_path', 'model', '选择包含模型文件的目录', true)}
                {renderPath('外部 Core ML 目录', 'coreml_model_path', 'coreml_model', '可选；检测到后才参与自动选择', true)}
                {renderPath('转写 CLI', 'coreml_cli_path', 'cli', '可选可执行文件')}
              </SettingsSection>}
            </>}

            {category === 'ai' && <>
              <SettingsSection title="任务分配" description="整理、翻译与内容生成分别选择服务商，不会静默继承彼此的配置。">
                <div className="provider-assignments">
                  <label>AI 整理<AppSelect value={assignments.clean_provider_id} onChange={clean_provider_id=>setAssignments({...assignments,clean_provider_id})} label="AI 整理供应商" options={providerCards.map(card=>({value:card.provider_id,label:card.name,description:card.model}))}/></label>
                  <label>AI 翻译<AppSelect value={assignments.translate_provider_id} onChange={translate_provider_id=>setAssignments({...assignments,translate_provider_id})} label="AI 翻译供应商" options={providerCards.map(card=>({value:card.provider_id,label:card.name,description:card.model}))}/></label>
                  <label>内容生成<AppSelect value={assignments.content_provider_id} onChange={content_provider_id=>setAssignments({...assignments,content_provider_id})} label="内容生成供应商" options={providerCards.map(card=>({value:card.provider_id,label:card.name,description:card.model}))}/></label>
                  <button className="button primary" onClick={()=>void api.saveAIAssignments(assignments).then(()=>setMessage('任务分配已保存')).catch(reason=>setError(reason.message))}>保存分配</button>
                </div>
              </SettingsSection>
              <SettingsSection title="模型供应商" description="每张卡的地址、密钥和模型互相隔离，密钥只保存在本机。">
                <div className="provider-card-grid">{providerCards.map(card=><article className="provider-card" key={card.provider_id}><header><strong>{card.name}</strong><span className={card.has_api_key?'ready':''}>{card.has_api_key?'已配置':'未配置'}</span></header><label>Base URL<input value={card.base_url} onChange={event=>updateProvider(card.provider_id,{base_url:event.target.value})}/></label><label>模型<input value={card.model} onChange={event=>updateProvider(card.provider_id,{model:event.target.value})}/></label>{!!card.models.length&&<div className="provider-model-chips">{card.models.map(model=><button type="button" className={model===card.model?'active':''} key={model} onClick={()=>updateProvider(card.provider_id,{model})}>{model}</button>)}</div>}<label>API Key<input type="password" value={card.api_key} placeholder={card.has_api_key?'留空保留现有密钥':'sk-…'} onChange={event=>updateProvider(card.provider_id,{api_key:event.target.value})}/></label><footer><button className="button secondary" disabled={busy||!card.has_api_key} onClick={()=>void api.testAIProvider(card.provider_id).then(result=>{updateProvider(card.provider_id,{last_test_status:'success',last_latency_ms:result.latency_ms});setMessage(`${card.name} ${result.latency_ms}ms`);}).catch(reason=>setError(reason.message))}>测试连接</button><button className="button primary" disabled={busy} onClick={()=>void saveProvider(card)}>保存</button></footer></article>)}</div>
              </SettingsSection>
            </>}

            {category === 'translation' && <>
              <SettingsSection title="默认语言" description="目标语言支持直接输入自定义语言或语言代码。">
                <label className="settings-field horizontal"><span><strong>目标语言</strong><small>用于新建项目和快速翻译</small></span><LanguagePicker mode="target" allowCustom value={resolvedTargetLanguage} onChange={translation_target_language => updateDraft({ translation_target_language })}/></label>
                <label className="settings-field horizontal"><span><strong>双语顺序</strong><small>可在单个导出中临时覆盖</small></span><AppSelect value={String(draft.bilingual_order||'original_first')} onChange={bilingual_order=>updateDraft({bilingual_order})} label="双语顺序" options={[{value:'original_first',label:'原文在上'},{value:'translated_first',label:'译文在上'}]}/></label>
              </SettingsSection>
              <SettingsSection title="常用语言" description="保存后用于快速选择；可搜索内置语言，也可输入自定义语言代码。">
                <div className="favorite-language-add"><LanguagePicker mode="target" allowCustom value={favoriteLanguage} onChange={setFavoriteLanguage}/><button className="button secondary" onClick={addFavoriteLanguage}>添加</button></div>
                <div className="language-tags">{(draft.favorite_languages || ['zh', 'en', 'ja', 'ko']).map(language => <span key={language}>{languageLabel(language)}<button aria-label={`移除 ${languageLabel(language)}`} onClick={() => updateDraft({ favorite_languages: (draft.favorite_languages || ['zh', 'en', 'ja', 'ko']).filter(item => item !== language) })}>×</button></span>)}</div>
              </SettingsSection>
            </>}

            {category === 'storage' && <>
              {youtubeEnabled && <SettingsSection title="YouTube 媒体模式" description="决定新建 YouTube 项目的默认处理方式；已有项目可在“处理 → 下载”中单独切换。">
                <Segmented value={String(draft.youtube_media_mode || 'local')} onChange={youtube_media_mode => updateDraft({ youtube_media_mode: youtube_media_mode as 'local' | 'web' })} options={[['web', '网页播放'], ['local', '下载至本地']]}/>
                <div className="settings-mode-explainer">
                  <strong>{draft.youtube_media_mode === 'web' ? '网页播放：更快开始' : '下载至本地：完整离线素材'}</strong>
                  <p>{draft.youtube_media_mode === 'web'
                    ? '只提取转写音频，视频由 YouTube 网页播放器呈现。字幕时间轴、倍速、循环、样式预览与本地模式一致；网页受限、离线使用或导出成片时再按需下载视频。'
                    : '创建项目后下载完整视频，适合离线播放、多音轨选择和频繁导出成片，但首次等待时间和磁盘占用更高。'}</p>
                </div>
                <p className="settings-help">网页播放依赖网络、视频可嵌入状态和平台可用性。请只处理你有权使用的内容；本功能不会绕过地区、年龄、登录或版权限制。</p>
              </SettingsSection>}
              {youtubeEnabled && <SettingsSection title="下载偏好" description="下载时会移除播放定位参数，并获取完整视频。">
                <label className="settings-field horizontal"><span><strong>画质</strong><small>高清画面与音频需要 FFmpeg 合并</small></span><AppSelect value={String(draft.download_quality||'best')} onChange={download_quality=>updateDraft({download_quality})} label="下载画质" options={[{value:'best',label:'最佳可用'},{value:'1080p',label:'最高 1080p'},{value:'720p',label:'最高 720p'}]}/></label>
                <label className="settings-field horizontal"><span><strong>容器</strong></span><AppSelect value={draft.download_container||'mp4'} onChange={download_container=>updateDraft({download_container:download_container as AppSettings['download_container']})} label="下载容器" options={[{value:'mp4',label:'MP4'},{value:'mkv',label:'MKV'},{value:'webm',label:'WebM'}]}/></label>
              </SettingsSection>}
              <SettingsSection title="运行状态" description="处理前请确保本地媒体引擎与存储均可用。" action={<button className="button secondary" onClick={onRefreshHealth}>重新检查</button>}>
                <RuntimeRow label="FFmpeg" value={runtimeCopy(runtime?.ffmpeg as any)}/>
                <RuntimeRow label="FFprobe" value={runtimeCopy(runtime?.ffprobe as any)}/>
                {youtubeEnabled && <RuntimeRow label="yt-dlp" value={runtimeCopy(runtime?.yt_dlp as any)}/>}
                {youtubeEnabled && <RuntimeRow label="Deno" value={runtimeCopy(runtime?.deno as any)}/>}
                {youtubeEnabled && <RuntimeRow label="EJS 挑战组件" value={runtimeCopy(runtime?.ejs as any)}/>}
                <RuntimeRow label="输出目录" value={runtimeCopy(runtime?.output_directory as any)}/>
                <RuntimeRow label="磁盘空间" value={{ ...runtimeCopy(runtime?.disk as any), detail: runtime?.disk?.free_bytes ? `${bytes(runtime.disk.free_bytes)} 可用` : runtimeCopy(runtime?.disk as any).detail }}/>
              </SettingsSection>
              {externalPathsEnabled && <SettingsSection title="路径" description="下载器固定使用 App 内置的 yt-dlp Python API；旧版 yt_dlp_path 设置会继续保留但不再参与下载。">
                {renderPath('下载目录', 'download_directory', 'download_directory', 'App 默认数据目录', true)}
                {renderPath('FFmpeg 自定义路径', 'ffmpeg_path', 'ffmpeg', '通常无需设置')}
              </SettingsSection>}
              <SettingsSection title="数据库备份" description="默认保留 7 份每日备份和 4 份每周备份；恢复前会再创建安全备份。" action={<button className="button secondary" disabled={busy} onClick={() => void backupNow()}>立即备份</button>}>
                <div className="backup-list">
                  {!backupState.backups.length && <span className="settings-empty">尚无备份</span>}
                  {backupState.backups.slice(0, 8).map(backup => <div className="backup-row" key={backup.name}><span><strong>{backup.name}</strong><small>{backup.modified_at} · {bytes(backup.size)}</small></span><button className="button secondary" disabled={busy} onClick={() => void restoreSelectedBackup(backup.name)}>恢复</button></div>)}
                </div>
                <button className="button secondary" disabled={!backupState.directory} onClick={() => void api.revealLocalPath(backupState.directory)}>在 Finder 中打开备份目录</button>
              </SettingsSection>
            </>}

            {category === 'appearance' && <>
              <SettingsSection title="外观" description="主题同时作用于 Web 界面和 macOS 原生标题栏。">
                <Segmented value={theme} onChange={value => onThemeChange(value as 'light' | 'dark')} options={[['light', '浅色'], ['dark', '深色']]}/>
                <label className="settings-field horizontal"><span><strong>界面密度</strong><small>紧凑模式适合小屏幕</small></span><AppSelect value={density} onChange={value=>onDensityChange(value as 'comfortable'|'compact')} label="界面密度" options={[{value:'comfortable',label:'舒适'},{value:'compact',label:'紧凑'}]}/></label>
              </SettingsSection>
              <SettingsSection title="动画" description="系统“减少动态效果”始终具有最高优先级。">
                <Toggle label="界面动画" detail="状态反馈 120ms、常规过渡 180ms、弹窗和抽屉 240ms" checked={motionEnabled} onChange={onMotionEnabledChange}/>
              </SettingsSection>
            </>}

            {category === 'about' && <>
              <SettingsSection title="快捷键" description="所有核心操作均可通过键盘完成。">
                <div className="shortcut-grid"><span>播放 / 暂停</span><kbd>Space</kbd><span>剧院模式</span><kbd>T</kbd><span>关闭弹窗或检查器</span><kbd>Esc</kbd><span>保存字幕编辑</span><kbd>Return</kbd></div>
              </SettingsSection>
              <SettingsSection title="关于字幕工厂" description="本地优先的专业字幕工作台。">
                <div className="about-card"><strong>字幕工厂 {health?.version || '0.4.1'}</strong><span>Apple Silicon · {distributionChannel === 'app_store' ? 'Mac App Store 版' : '直装版'}</span><small>服务状态：{health?.status || '正在连接'}</small></div>
                <div className="about-data-row"><span><strong>数据目录</strong><small>{health?.runtime?.data_directory || 'App 本地数据目录'}</small></span></div>
                <div className="inline-actions"><button className="button secondary" onClick={() => void copyDiagnostics()}>复制诊断信息</button><button className="button secondary" onClick={() => void api.downloadDiagnostics().catch(reason => setError(reason.message))}>导出脱敏诊断包</button><button className="button secondary" onClick={() => { onClose(); onOpenLogs(); }}>查看处理日志</button></div>
                <p className="settings-help">复制的诊断信息不包含本机路径或 API Key。自定义路径和密钥不会进入 Git、默认配置、日志或 Release。</p>
              </SettingsSection>
              <SettingsSection title="隐私与数据" description="默认本地处理；任何云端发送都需要用户主动配置并明确执行。">
                <div className="about-data-row"><span><strong>本地项目</strong><small>媒体、字幕、编辑历史、数据库和模型保存在这台 Mac；不会自动同步。</small></span></div>
                <div className="about-data-row"><span><strong>云端 AI</strong><small>翻译、整理和内容生成只发送当前操作所需的文本。Fun-Realtime-ASR 只在单独授权后分段上传当前项目音频，授权可随时撤销。</small></span></div>
                {youtubeEnabled
                  ? <div className="about-data-row"><span><strong>第三方媒体</strong><small>直装版可按用户指令连接 YouTube；仅应处理拥有明确权利的内容，Chrome 登录状态最多在权限挑战时读取一次。</small></span></div>
                  : <div className="about-data-row"><span><strong>App Store 保护</strong><small>此发行版不提供第三方网站媒体读取、Cookie 访问、持久监听文件夹或外部运行时路径。</small></span></div>}
                <p className="settings-help">API Key 保存在 macOS 钥匙串。诊断包不包含密钥、媒体、字幕正文或完整用户目录路径。</p>
              </SettingsSection>
            </>}
          </div>

          {category !== 'ai' && category !== 'appearance' && category !== 'about' && <footer className="settings-footer">
            <div>{message && <span className="success-copy">{message}</span>}{error && <span className="error-copy">{error}</span>}</div>
            <button className="button primary" disabled={busy} onClick={() => saveApp()}>{busy ? '正在保存…' : '保存更改'}</button>
          </footer>}
          {(category === 'ai' || category === 'appearance' || category === 'about') && (message || error) && <footer className="settings-footer"><div>{message && <span className="success-copy">{message}</span>}{error && <span className="error-copy">{error}</span>}</div></footer>}
        </div>
      </section>
    </div>,
    document.body,
  );
}

function SettingsSection({ title, description, action, children }: { title: string; description?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return <section className="settings-section"><header><div><h3>{title}</h3>{description && <p>{description}</p>}</div>{action}</header><div className="settings-section-body">{children}</div></section>;
}

function Toggle({ label, detail, checked, onChange }: { label: string; detail?: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="settings-field horizontal toggle-row"><span><strong>{label}</strong>{detail && <small>{detail}</small>}</span><input type="checkbox" checked={checked} onChange={event => onChange(event.target.checked)}/></label>;
}

function Segmented({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: string[][] }) {
  return <div className="segmented-control">{options.map(([id, label]) => <button className={value === id ? 'active' : ''} key={id} onClick={() => onChange(id)}>{label}</button>)}</div>;
}

function RuntimeRow({ label, value }: { label: string; value: { ok: boolean; title: string; detail: string } }) {
  return <div className="runtime-row"><span className={`status-orb ${value.ok ? 'ok' : 'error'}`}/><div><strong>{label}</strong><small>{value.detail || value.title}</small></div><em>{value.ok ? '可用' : value.title}</em></div>;
}
