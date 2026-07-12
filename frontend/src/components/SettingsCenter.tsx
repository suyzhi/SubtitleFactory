import { useEffect, useMemo, useRef, useState } from 'react';
import * as api from '../api/backend';
import type {
  AISettings, AppSettings, HealthStatus, PathValidationResult,
  ProcessingConfig, AppSettingWarning,
} from '../types';
import LanguagePicker from './LanguagePicker';
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
  const [category, setCategory] = useState<Category>('general');
  const [draft, setDraft] = useState<AppSettings>(appSettings);
  const [warnings, setWarnings] = useState<AppSettingWarning[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [pathChecks, setPathChecks] = useState<Record<string, PathValidationResult>>({});
  const [preparingModel, setPreparingModel] = useState('');
  const [validatingModel, setValidatingModel] = useState('');
  const [favoriteLanguage, setFavoriteLanguage] = useState('fr');
  const [providerCards,setProviderCards]=useState<api.AIProviderCard[]>([]);
  const [assignments,setAssignments]=useState({clean_provider_id:'deepseek',translate_provider_id:'deepseek'});
  const [scannedModels,setScannedModels]=useState<api.ScannedModel[]>([]);
  const dialogRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!open) return;
    setMessage('');
    setError('');
    Promise.all([api.getAppSettings(), api.getAISettings(), api.getAIProviders()])
      .then(([app, , providers]) => {
        setDraft(app.settings || {});
        setWarnings(app.warnings || []);
        setProviderCards(providers.providers); setAssignments(providers.assignments);
      })
      .catch(reason => setError(reason.message));
    onRefreshHealth();
    onRefreshModels();
  }, [open, onRefreshHealth, onRefreshModels]);

  const scanModelFolder=async()=>{ try { const {open}=await import('@tauri-apps/plugin-dialog'); const path=await open({directory:true,multiple:false,title:'选择模型根目录'}); if(typeof path!=='string')return; setBusy(true); const result=await api.scanLocalModels(path); setScannedModels(result.models); setMessage(`发现 ${result.models.length} 个模型候选`); } catch(reason){setError(reason instanceof Error?reason.message:String(reason));} finally{setBusy(false);} };
  const updateProvider=(id:string,patch:Partial<api.AIProviderCard>)=>setProviderCards(items=>items.map(item=>item.provider_id===id?{...item,...patch}:item));
  const saveProvider=async(card:api.AIProviderCard)=>{setBusy(true);try{const saved=await api.saveAIProvider(card.provider_id,card);updateProvider(card.provider_id,saved);setMessage(`${card.name} 已保存`);}catch(reason){setError(reason instanceof Error?reason.message:String(reason));}finally{setBusy(false);}};

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => {
      const dialog = dialogRef.current;
      const activeCategory = dialog?.querySelector<HTMLButtonElement>('.settings-navigation button.active');
      (activeCategory || dialog)?.focus();
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
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
  }, [onClose, open]);

  const resolvedSourceLanguage = String(draft.source_language || config.language);
  const resolvedTargetLanguage = String(draft.translation_target_language || config.target_language);
  const resolvedModel = String(draft.default_model || config.model);
  const runtime = health?.runtime;

  const modelSourceLabels = useMemo(() => ({
    bundled: '内置', app_download: 'App 下载', external_detected: '外部检测', custom: '自定义路径',
    environment: '环境变量', unavailable: '不可用',
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

  const prepareModel = async (modelId: string, repair: boolean) => {
    setBusy(true); setError(''); setMessage(''); setPreparingModel(modelId);
    try {
      const created = await api.prepareTranscriptionModel(modelId, repair);
      setMessage(created.message);
      const poll = async (attempt: number) => {
        const task = await api.getTaskStatus(created.task_id);
        if (['success', 'failed', 'cancelled', 'partial'].includes(task.status)) {
          setPreparingModel(''); setBusy(false); onRefreshModels();
          setMessage(task.status === 'success' ? '模型已准备就绪' : task.error || task.message);
          return;
        }
        if (attempt < 900) window.setTimeout(() => void poll(attempt + 1), 1000);
      };
      await poll(0);
    } catch (reason: any) {
      setError(reason.message); setPreparingModel(''); setBusy(false);
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
        yt_dlp: runtime?.yt_dlp && { ok: runtime.yt_dlp.ok, status: runtime.yt_dlp.status, source: runtime.yt_dlp.source, version: runtime.yt_dlp.version },
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

  return (
    <div className="modal-backdrop settings-backdrop" role="presentation" onMouseDown={onClose}>
      <section ref={dialogRef} tabIndex={-1} className="settings-center" role="dialog" aria-modal="true" aria-label="设置中心" onMouseDown={event => event.stopPropagation()}>
        <aside className="settings-navigation">
          <div className="settings-title"><strong>设置</strong><small>字幕工厂</small></div>
          <nav aria-label="设置分类">
            {CATEGORIES.map(item => <button key={item.id} className={category === item.id ? 'active' : ''} onClick={() => { setCategory(item.id); setMessage(''); setError(''); }}>
              <i>{item.icon}</i><span>{item.label}</span>
            </button>)}
          </nav>
          <small className="settings-version">Version {health?.version || '0.3.0'}</small>
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
                  <select value={draft.startup_behavior || 'restore_last'} onChange={event => updateDraft({ startup_behavior: event.target.value as AppSettings['startup_behavior'] })}>
                    <option value="restore_last">打开上次项目</option><option value="project_library">显示项目库</option>
                  </select>
                </label>
              </SettingsSection>
            </>}

            {category === 'transcription' && <>
              <SettingsSection title="默认转写" description="自动选择在普通用户电脑上使用 Whisper Small。">
                <label className="settings-field horizontal"><span><strong>默认模型</strong><small>Parakeet 仅支持其模型声明的语言</small></span>
                  <select value={resolvedModel} onChange={event => updateDraft({ default_model: event.target.value })}>
                    <option value="auto">自动选择 · Whisper Small</option><option value="small">Whisper Small</option><option value="medium">Whisper Medium</option><option value="large-v3">Whisper Large V3</option>
                    {modelStatus?.models.filter(model => model.id.includes('parakeet') && (model.ready || model.download_required)).map(model => <option value={model.id} key={model.id}>{model.name}</option>)}
                  </select>
                </label>
                <label className="settings-field horizontal"><span><strong>源语言</strong><small>可搜索 20 种常用语言</small></span>
                  <LanguagePicker value={resolvedSourceLanguage} onChange={source_language => updateDraft({ source_language })}/>
                </label>
              </SettingsSection>
              <SettingsSection title="模型管理" description="可扫描 Memo/models 等目录并原地引用；移除登记不会删除源模型。" action={<div className="inline-actions"><button className="button secondary" onClick={scanModelFolder}>导入本地模型</button><button className="button secondary" onClick={onRefreshModels}>刷新状态</button></div>}>
                <div className="model-list">
                  {modelStatus?.models.filter(model => model.ready || model.download_required || !model.id.includes('coreml')).map(model => {
                    const source = String((model as any).source || (model.ready ? 'bundled' : 'unavailable'));
                    const canPrepare = model.id.includes('parakeet');
                    return <div className="model-row" key={model.id}>
                      <span className={`status-orb ${model.ready ? 'ok' : model.download_required ? 'warning' : 'error'}`}/>
                      <div><strong>{model.name}</strong><small>{model.ready ? '已就绪' : model.download_required ? `首次转写时下载${model.download_bytes ? ` · ${bytes(model.download_bytes)}` : ''}` : model.runtime_error || '不可用'}</small></div>
                      <em>{modelSourceLabels[source] || source}</em>
                      <span className="model-row-actions"><button className="button secondary model-action" disabled={!!validatingModel} onClick={() => void validateModel(model.id)}>{validatingModel === model.id ? '校验中…' : '校验'}</button>{canPrepare && <button className="button secondary model-action" disabled={!!preparingModel} onClick={() => void prepareModel(model.id, model.ready || !model.download_required)}>{preparingModel === model.id ? '处理中…' : model.ready ? '修复' : '下载'}</button>}</span>
                    </div>;
                  }) || <div className="settings-empty">正在读取模型状态…</div>}
                </div>
                {!!scannedModels.length&&<div className="scanned-models">{scannedModels.map(model=><div className="model-row" key={model.path}><span className={`status-orb ${model.supported?'ok':'warning'}`}/><div><strong>{model.display_name}</strong><small>{model.format} · {model.version||'未标版本'} · {model.supported?(model.reason||'可原地引用'):model.reason}</small></div>{model.supported&&<button className="button secondary model-action" disabled={busy||Boolean(model.reason)} onClick={()=>void api.importLocalModel(model.path,model.cli_path).then(()=>{setMessage(`${model.display_name} 已登记`);onRefreshModels();}).catch(reason=>setError(reason.message))}>登记</button>}</div>)}</div>}
              </SettingsSection>
              <SettingsSection title="自定义运行时" description="路径只保存在本机，不会写入项目、日志或发布包。">
                {renderPath('自定义模型目录', 'custom_model_path', 'model', '选择包含模型文件的目录', true)}
                {renderPath('外部 Core ML 目录', 'coreml_model_path', 'coreml_model', '可选；检测到后才参与自动选择', true)}
                {renderPath('转写 CLI', 'coreml_cli_path', 'cli', '可选可执行文件')}
              </SettingsSection>
            </>}

            {category === 'ai' && <>
              <SettingsSection title="任务分配" description="整理和翻译可使用不同的服务商与模型。">
                <div className="provider-assignments"><label>AI 整理<select value={assignments.clean_provider_id} onChange={event=>setAssignments({...assignments,clean_provider_id:event.target.value})}>{providerCards.map(card=><option value={card.provider_id} key={card.provider_id}>{card.name}</option>)}</select></label><label>AI 翻译<select value={assignments.translate_provider_id} onChange={event=>setAssignments({...assignments,translate_provider_id:event.target.value})}>{providerCards.map(card=><option value={card.provider_id} key={card.provider_id}>{card.name}</option>)}</select></label><button className="button primary" onClick={()=>void api.saveAIAssignments(assignments).then(()=>setMessage('任务分配已保存')).catch(reason=>setError(reason.message))}>保存分配</button></div>
              </SettingsSection>
              <SettingsSection title="模型供应商" description="每张卡的地址、密钥和模型互相隔离，密钥只保存在本机。">
                <div className="provider-card-grid">{providerCards.map(card=><article className="provider-card" key={card.provider_id}><header><strong>{card.name}</strong><span className={card.has_api_key?'ready':''}>{card.has_api_key?'已配置':'未配置'}</span></header><label>Base URL<input value={card.base_url} onChange={event=>updateProvider(card.provider_id,{base_url:event.target.value})}/></label><label>模型<input value={card.model} onChange={event=>updateProvider(card.provider_id,{model:event.target.value})}/></label>{!!card.models.length&&<div className="provider-model-chips">{card.models.map(model=><button type="button" className={model===card.model?'active':''} key={model} onClick={()=>updateProvider(card.provider_id,{model})}>{model}</button>)}</div>}<label>API Key<input type="password" value={card.api_key} placeholder={card.has_api_key?'留空保留现有密钥':'sk-…'} onChange={event=>updateProvider(card.provider_id,{api_key:event.target.value})}/></label><footer><button className="button secondary" disabled={busy||!card.has_api_key} onClick={()=>void api.testAIProvider(card.provider_id).then(result=>{updateProvider(card.provider_id,{last_test_status:'success',last_latency_ms:result.latency_ms});setMessage(`${card.name} ${result.latency_ms}ms`);}).catch(reason=>setError(reason.message))}>测试连接</button><button className="button primary" disabled={busy} onClick={()=>void saveProvider(card)}>保存</button></footer></article>)}</div>
              </SettingsSection>
            </>}

            {category === 'translation' && <>
              <SettingsSection title="默认语言" description="目标语言支持直接输入自定义语言或语言代码。">
                <label className="settings-field horizontal"><span><strong>目标语言</strong><small>用于新建项目和快速翻译</small></span><LanguagePicker mode="target" allowCustom value={resolvedTargetLanguage} onChange={translation_target_language => updateDraft({ translation_target_language })}/></label>
                <label className="settings-field horizontal"><span><strong>双语顺序</strong><small>可在单个导出中临时覆盖</small></span><select value={String(draft.bilingual_order || 'original_first')} onChange={event => updateDraft({ bilingual_order: event.target.value })}><option value="original_first">原文在上</option><option value="translated_first">译文在上</option></select></label>
              </SettingsSection>
              <SettingsSection title="常用语言" description="保存后用于快速选择；可搜索内置语言，也可输入自定义语言代码。">
                <div className="favorite-language-add"><LanguagePicker mode="target" allowCustom value={favoriteLanguage} onChange={setFavoriteLanguage}/><button className="button secondary" onClick={addFavoriteLanguage}>添加</button></div>
                <div className="language-tags">{(draft.favorite_languages || ['zh', 'en', 'ja', 'ko']).map(language => <span key={language}>{languageLabel(language)}<button aria-label={`移除 ${languageLabel(language)}`} onClick={() => updateDraft({ favorite_languages: (draft.favorite_languages || ['zh', 'en', 'ja', 'ko']).filter(item => item !== language) })}>×</button></span>)}</div>
              </SettingsSection>
            </>}

            {category === 'storage' && <>
              <SettingsSection title="下载偏好" description="YouTube 播放定位参数会自动移除，并下载完整视频。">
                <label className="settings-field horizontal"><span><strong>画质</strong><small>高清画面与音频需要 FFmpeg 合并</small></span><select value={String(draft.download_quality || 'best')} onChange={event => updateDraft({ download_quality: event.target.value })}><option value="best">最佳可用</option><option value="1080p">最高 1080p</option><option value="720p">最高 720p</option></select></label>
                <label className="settings-field horizontal"><span><strong>容器</strong></span><select value={draft.download_container || 'mp4'} onChange={event => updateDraft({ download_container: event.target.value as AppSettings['download_container'] })}><option value="mp4">MP4</option><option value="mkv">MKV</option><option value="webm">WebM</option></select></label>
              </SettingsSection>
              <SettingsSection title="运行状态" description="下载前请确保所有关键项目均为可用。" action={<button className="button secondary" onClick={onRefreshHealth}>重新检查</button>}>
                <RuntimeRow label="FFmpeg" value={runtimeCopy(runtime?.ffmpeg as any)}/>
                <RuntimeRow label="yt-dlp" value={runtimeCopy(runtime?.yt_dlp as any)}/>
                <RuntimeRow label="输出目录" value={runtimeCopy(runtime?.output_directory as any)}/>
                <RuntimeRow label="磁盘空间" value={{ ...runtimeCopy(runtime?.disk as any), detail: runtime?.disk?.free_bytes ? `${bytes(runtime.disk.free_bytes)} 可用` : runtimeCopy(runtime?.disk as any).detail }}/>
              </SettingsSection>
              <SettingsSection title="路径" description="解析顺序：App 内置 → 用户自定义 → 环境变量 → 系统 PATH。">
                {renderPath('下载目录', 'download_directory', 'download_directory', 'App 默认数据目录', true)}
                {renderPath('FFmpeg 自定义路径', 'ffmpeg_path', 'ffmpeg', '通常无需设置')}
                {renderPath('yt-dlp 自定义路径', 'yt_dlp_path', 'yt_dlp', '通常无需设置')}
              </SettingsSection>
            </>}

            {category === 'appearance' && <>
              <SettingsSection title="外观" description="主题同时作用于 Web 界面和 macOS 原生标题栏。">
                <Segmented value={theme} onChange={value => onThemeChange(value as 'light' | 'dark')} options={[['light', '浅色'], ['dark', '深色']]}/>
                <label className="settings-field horizontal"><span><strong>界面密度</strong><small>紧凑模式适合小屏幕</small></span><select value={density} onChange={event => onDensityChange(event.target.value as 'comfortable' | 'compact')}><option value="comfortable">舒适</option><option value="compact">紧凑</option></select></label>
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
                <div className="about-card"><strong>字幕工厂 {health?.version || '0.3.0'}</strong><span>Apple Silicon · 本地运行</span><small>服务状态：{health?.status || '正在连接'}</small></div>
                <div className="about-data-row"><span><strong>数据目录</strong><small>{health?.runtime?.data_directory || 'App 本地数据目录'}</small></span></div>
                <div className="inline-actions"><button className="button secondary" onClick={() => void copyDiagnostics()}>复制诊断信息</button><button className="button secondary" onClick={() => { onClose(); onOpenLogs(); }}>查看处理日志</button></div>
                <p className="settings-help">复制的诊断信息不包含本机路径或 API Key。自定义路径和密钥不会进入 Git、默认配置、日志或 Release。</p>
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
    </div>
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
