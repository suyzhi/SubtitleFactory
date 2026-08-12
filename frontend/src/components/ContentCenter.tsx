import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../api/backend';
import type {
  ClipAspectRatio, ClipCandidate, ClipLayout, ClipSet,
  ContentPack, ContentPackInputMode, ContentSection, Project, TaskStatus,
} from '../types';
import AppSelect from './AppSelect';

interface Props {
  project: Project;
  projectRevision: number;
  hasSegments: boolean;
  onPreview: (start: number, end: number) => void;
  onMessage: (message: string) => void;
}

type ContentTab = 'packs' | 'clips';
type SaveState = 'idle' | 'saving' | 'saved' | 'error';

const TERMINAL_TASKS = new Set<TaskStatus['status']>(['success', 'failed', 'cancelled', 'partial']);

function timecode(value: number) {
  const seconds = Math.max(0, Number(value) || 0);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = Math.floor(seconds % 60);
  const tenths = Math.floor((seconds % 1) * 10);
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}.${tenths}`
    : `${minutes}:${String(remainder).padStart(2, '0')}.${tenths}`;
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason);
}

function ContentAuthorization({
  granted, busy, onGrant,
}: {
  granted: boolean;
  busy: boolean;
  onGrant: () => void;
}) {
  if (granted) return null;
  return <section className="content-authorization">
    <span>云端内容生成尚未授权</span>
    <div>
      <strong>只发送生成所需的文字</strong>
      <p>会上传当前项目标题、所选字幕文字、时间码和说话人标签。不会上传视频、音频、本机路径或其他项目内容。</p>
    </div>
    <button className="button primary" disabled={busy} onClick={onGrant}>
      {busy ? '正在保存…' : '阅读并授权'}
    </button>
  </section>;
}

function SectionFields({
  kind, value, onChange,
}: {
  kind: ContentSection['kind'];
  value: Record<string, any>;
  onChange: (next: Record<string, any>) => void;
}) {
  const set = (key: string, next: unknown) => onChange({ ...value, [key]: next });
  const setArrayItem = (key: string, index: number, patch: Record<string, unknown>) => {
    const items = [...(Array.isArray(value[key]) ? value[key] : [])];
    items[index] = { ...items[index], ...patch };
    set(key, items);
  };

  if (kind === 'summary') {
    return <div className="content-fields">
      <label>内容摘要<textarea value={String(value.overview || '')} onChange={event => set('overview', event.target.value)}/></label>
      <label>要点（每行一条）<textarea value={(value.key_points || []).join('\n')} onChange={event => set('key_points', event.target.value.split('\n').filter(Boolean))}/></label>
    </div>;
  }
  if (kind === 'chapters') {
    return <div className="structured-item-list">{(value.chapters || []).map((item: any, index: number) =>
      <article key={`${item.start_index}-${index}`}>
        <span>{timecode(item.start)} → {timecode(item.end)}</span>
        <input aria-label={`章节 ${index + 1} 标题`} value={String(item.title || '')} onChange={event => setArrayItem('chapters', index, { title: event.target.value })}/>
        <textarea aria-label={`章节 ${index + 1} 摘要`} value={String(item.summary || '')} onChange={event => setArrayItem('chapters', index, { summary: event.target.value })}/>
      </article>)}</div>;
  }
  if (kind === 'quotes') {
    return <div className="structured-item-list">{(value.quotes || []).map((item: any, index: number) =>
      <article key={`${item.segment_index}-${index}`}>
        <span>{timecode(item.start)} · {item.speaker || '未标说话人'}</span>
        <textarea aria-label={`金句 ${index + 1}`} value={String(item.text || '')} onChange={event => setArrayItem('quotes', index, { text: event.target.value })}/>
        <input aria-label={`金句 ${index + 1} 说明`} value={String(item.reason || '')} onChange={event => setArrayItem('quotes', index, { reason: event.target.value })}/>
      </article>)}</div>;
  }
  if (kind === 'youtube') {
    return <div className="content-fields">
      <label>标题候选（每行一个）<textarea value={(value.titles || []).join('\n')} onChange={event => set('titles', event.target.value.split('\n').filter(Boolean))}/></label>
      <label>简介<textarea value={String(value.description || '')} onChange={event => set('description', event.target.value)}/></label>
      <label>章节文本<textarea value={String(value.chapter_text || '')} onChange={event => set('chapter_text', event.target.value)}/></label>
      <label>标签（逗号分隔）<input value={(value.tags || []).join(', ')} onChange={event => set('tags', event.target.value.split(',').map(item => item.trim()).filter(Boolean))}/></label>
    </div>;
  }
  if (kind === 'podcast') {
    return <div className="content-fields">
      <label>播客标题<input value={String(value.title || '')} onChange={event => set('title', event.target.value)}/></label>
      <label>节目简介<textarea value={String(value.intro || '')} onChange={event => set('intro', event.target.value)}/></label>
      <label>Show Notes<textarea value={String(value.show_notes || '')} onChange={event => set('show_notes', event.target.value)}/></label>
    </div>;
  }
  return <div className="content-fields">
    <label>小红书<textarea value={String(value.xiaohongshu || '')} onChange={event => set('xiaohongshu', event.target.value)}/></label>
    <label>公众号<textarea value={String(value.wechat || '')} onChange={event => set('wechat', event.target.value)}/></label>
    <label>通用社交<textarea value={String(value.generic || '')} onChange={event => set('generic', event.target.value)}/></label>
  </div>;
}

function SectionEditor({
  packId, section, stale, provider, model, onSaved, onRegenerate,
}: {
  packId: string;
  section: ContentSection;
  stale: boolean;
  provider?: string | null;
  model?: string | null;
  onSaved: (pack: ContentPack) => void;
  onRegenerate: (section: ContentSection) => void;
}) {
  const [draft, setDraft] = useState<Record<string, any>>(section.content || {});
  const [saveState, setSaveState] = useState<SaveState>('idle');
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const sectionId = useRef(section.id);

  useEffect(() => {
    setDraft(section.content || {});
    setDirty(false);
    if (sectionId.current !== section.id) {
      sectionId.current = section.id;
      setSaveState('idle');
    }
    setError('');
  }, [section.content, section.id, section.revision]);

  useEffect(() => {
    if (!dirty) return;
    const timer = window.setTimeout(() => {
      setSaveState('saving');
      setError('');
      void api.updateContentSection(packId, section.kind, draft, section.revision)
        .then(pack => {
          setDirty(false);
          setSaveState('saved');
          onSaved(pack);
        })
        .catch(reason => {
          setSaveState('error');
          setError(errorMessage(reason));
        });
    }, 800);
    return () => window.clearTimeout(timer);
  }, [dirty, draft, onSaved, packId, section.kind, section.revision]);

  return <details className={`content-section ${section.status}`} open={section.kind === 'summary'}>
    <summary>
      <span><strong>{section.title}</strong><small>{section.status === 'failed' ? section.error : section.generated_at ? `${section.generated_at} · 修订 ${section.revision}` : '等待生成'}</small></span>
      <em className={saveState}>{saveState === 'saving' ? '保存中…' : saveState === 'saved' ? '已保存' : saveState === 'error' ? '保存失败' : stale ? '源字幕已更新' : section.status === 'ready' ? '已就绪' : section.status}</em>
    </summary>
    {section.status === 'failed' && <p className="content-error">{section.error}</p>}
    <SectionFields kind={section.kind} value={draft} onChange={next => {
      setDraft(next);
      setDirty(true);
      setSaveState('idle');
    }}/>
    {error && <p className="content-error">{error}</p>}
    <footer>
      <span>{stale
        ? `源字幕已更新 · ${provider || '内容服务'} / ${model || '默认模型'} · 重新生成只替换本区域`
        : `生成来源：${section.generated_at ? `${provider || '内容服务'} / ${model || '默认模型'} + 人工编辑` : '等待 AI'}`}</span>
      <button className="button secondary" onClick={() => onRegenerate(section)}>重新生成本区域</button>
    </footer>
  </details>;
}

function PublicationPacks({
  project, hasSegments, granted, onRequireAuthorization, onMessage,
}: {
  project: Project;
  hasSegments: boolean;
  granted: boolean;
  onRequireAuthorization: () => Promise<boolean>;
  onMessage: (message: string) => void;
}) {
  const [packs, setPacks] = useState<ContentPack[]>([]);
  const [selected, setSelected] = useState<ContentPack | null>(null);
  const [name, setName] = useState('内容发布包');
  const [inputMode, setInputMode] = useState<ContentPackInputMode>('original');
  const [outputLanguage, setOutputLanguage] = useState('auto');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const refresh = useCallback(async (preferredId?: string) => {
    const result = await api.getContentPacks(project.id);
    if (!alive.current) return;
    setPacks(result.packs);
    const id = preferredId || selected?.id || result.packs[0]?.id;
    if (!id) {
      setSelected(null);
      return;
    }
    const detail = await api.getContentPack(id);
    if (alive.current) setSelected(detail);
  }, [project.id, selected?.id]);

  useEffect(() => {
    setSelected(null);
    setPacks([]);
    void refresh().catch(reason => setError(errorMessage(reason)));
  }, [project.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const waitForTask = useCallback(async (taskId: string, packId: string) => {
    while (alive.current) {
      const task = await api.getTaskStatus(taskId);
      if (TERMINAL_TASKS.has(task.status)) {
        await refresh(packId);
        if (task.status === 'failed') throw new Error(task.error || task.message);
        onMessage(task.status === 'partial' ? '发布包部分生成完成，可单独重试失败区域' : '内容发布包已生成');
        return;
      }
      await new Promise(resolve => window.setTimeout(resolve, 1000));
    }
  }, [onMessage, refresh]);

  const create = async (allowFallback = false) => {
    if (!hasSegments) return;
    if (!granted && !(await onRequireAuthorization())) return;
    setBusy(true);
    setError('');
    try {
      const result = await api.createContentPack(project.id, {
        name, input_mode: inputMode, output_language: outputLanguage,
        allow_translation_fallback: allowFallback,
      });
      setSelected(result.pack);
      await refresh(result.pack.id);
      void waitForTask(result.task_id, result.pack.id).catch(reason => setError(errorMessage(reason)));
    } catch (reason) {
      if (reason instanceof api.BackendError && reason.code === 'TRANSLATION_INCOMPLETE' && !allowFallback) {
        const coverage = Number(reason.details.coverage || 0);
        if (window.confirm(`译文覆盖率为 ${(coverage * 100).toFixed(1)}%。是否明确允许缺失部分使用原文补齐？`)) {
          await create(true);
        }
      } else {
        setError(errorMessage(reason));
      }
    } finally {
      setBusy(false);
    }
  };

  const regenerate = async (section: ContentSection) => {
    if (!granted && !(await onRequireAuthorization())) return;
    if (!window.confirm(`重新生成“${section.title}”会替换这个区域的人工修改，其他区域不受影响。继续吗？`)) return;
    try {
      const result = await api.regenerateContentSection(selected!.id, section.kind);
      onMessage(`正在重新生成${section.title}`);
      void waitForTask(result.task_id, selected!.id).catch(reason => setError(errorMessage(reason)));
    } catch (reason) { setError(errorMessage(reason)); }
  };

  const rename = async () => {
    if (!selected) return;
    const next = window.prompt('发布包名称', selected.name)?.trim();
    if (!next || next === selected.name) return;
    try {
      const pack = await api.updateContentPack(selected.id, next, selected.revision);
      setSelected(pack);
      await refresh(pack.id);
    } catch (reason) { setError(errorMessage(reason)); }
  };

  const remove = async () => {
    if (!selected || !window.confirm(`删除“${selected.name}”？此操作不会删除字幕。`)) return;
    try {
      await api.deleteContentPack(selected.id);
      setSelected(null);
      await refresh();
    } catch (reason) { setError(errorMessage(reason)); }
  };

  const exportPack = async () => {
    if (!selected) return;
    try {
      const result = await api.exportContentPack(selected.id);
      const saved = await api.downloadContentPack(
        result.export_id,
        `${selected.name}.zip`,
        result.path,
      );
      onMessage(saved ? '内容发布包 ZIP 已导出' : '已取消保存内容发布包');
    } catch (reason) { setError(errorMessage(reason)); }
  };

  return <div className="content-pack-layout">
    <aside className="content-collection-list">
      <header><strong>发布包</strong><span>{packs.length}</span></header>
      {packs.map(pack => <button key={pack.id} className={selected?.id === pack.id ? 'active' : ''} onClick={() => void api.getContentPack(pack.id).then(setSelected).catch(reason => setError(errorMessage(reason)))}>
        <span><strong>{pack.name}</strong><small>{pack.input_mode === 'original' ? '原文' : pack.input_mode === 'translated' ? '译文' : '双语'} · {pack.updated_at}</small></span>
        <em className={pack.stale ? 'stale' : pack.status}>{pack.stale ? '已过期' : pack.status}</em>
      </button>)}
      {!packs.length && <p>还没有发布包。右侧设置来源后开始生成。</p>}
    </aside>
    <section className="content-pack-main">
      {!selected && <div className="content-create-card">
        <header><small>新建发布包</small><h2>把字幕整理成一套可编辑发布资料</h2><p>六个区域独立生成，任何一个失败都不会覆盖其他成功内容。</p></header>
        <label>名称<input value={name} onChange={event => setName(event.target.value)}/></label>
        <div className="content-create-grid">
          <label>输入内容<AppSelect value={inputMode} onChange={value => setInputMode(value as ContentPackInputMode)} label="发布包输入内容" options={[
            { value: 'original', label: '整理后原文' },
            { value: 'translated', label: '译文', description: '不完整时会先确认' },
            { value: 'bilingual', label: '双语', description: '原文与译文共同参考' },
          ]}/></label>
          <label>输出语言<input value={outputLanguage} onChange={event => setOutputLanguage(event.target.value)} placeholder="auto / 中文 / English"/></label>
        </div>
        <button className="button primary" disabled={busy || !name.trim() || !hasSegments} onClick={() => void create()}>{busy ? '正在创建…' : '生成完整发布包'}</button>
      </div>}
      {selected && <>
        <header className="content-pack-toolbar">
          <div>
            <span><strong>{selected.name}</strong>{selected.stale && <em>源字幕已更新</em>}</span>
            <small>{selected.provider_id || '内容服务'} · {selected.model || '默认模型'} · 字幕修订 {selected.source_revision}</small>
          </div>
          <div>
            <button className="button secondary" onClick={rename}>重命名</button>
            <button className="button secondary" onClick={() => void navigator.clipboard.writeText(JSON.stringify(selected.sections || [], null, 2)).then(() => onMessage('发布包结构已复制'))}>复制</button>
            <button className="button secondary" onClick={() => void exportPack()}>导出 ZIP</button>
            <button className="button secondary danger" onClick={() => void remove()}>删除</button>
            <button className="button primary" onClick={() => setSelected(null)}>新建</button>
          </div>
        </header>
        {selected.stale && <div className="content-stale-banner">字幕在发布包生成后有过修改。旧内容和人工编辑都已保留，请按需重新生成具体区域。</div>}
        <div className="content-section-list">{(selected.sections || []).map(section =>
          <SectionEditor key={section.id} packId={selected.id} section={section} stale={selected.stale}
            provider={selected.provider_id} model={selected.model}
            onSaved={pack => {
              setSelected(pack);
              setPacks(current => current.map(item => item.id === pack.id ? { ...item, ...pack, sections: undefined } : item));
            }}
            onRegenerate={item => void regenerate(item)}/>)}</div>
      </>}
      {error && <p className="content-error">{error}</p>}
    </section>
  </div>;
}

function ClipCandidateCard({
  candidate, previewImage, onChange, onPreview, onMessage,
}: {
  candidate: ClipCandidate;
  previewImage?: string | null;
  onChange: (next: ClipSet) => void;
  onPreview: (start: number, end: number) => void;
  onMessage: (message: string) => void;
}) {
  const [draft, setDraft] = useState(candidate);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => setDraft(candidate), [candidate]);

  const mutateLayout = (aspect: ClipAspectRatio, patch: Partial<ClipLayout>) => {
    setDraft(current => ({
      ...current,
      layouts: current.layouts.map(item => item.aspect_ratio === aspect ? { ...item, ...patch } : item),
    }));
  };

  const moveFocalPoint = (event: React.PointerEvent<HTMLDivElement>, aspect: ClipAspectRatio) => {
    if (event.type === 'pointermove' && event.buttons !== 1) return;
    if (event.type === 'pointerdown') event.currentTarget.setPointerCapture(event.pointerId);
    const bounds = event.currentTarget.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    mutateLayout(aspect, {
      focal_x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      focal_y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
    });
  };

  const saveCandidate = async () => {
    setBusy(true);
    setError('');
    try {
      const next = await api.updateClipCandidate(candidate.id, {
        title: draft.title,
        start: Number(draft.start),
        end: Number(draft.end),
        selected: draft.selected,
        expected_revision: candidate.revision,
        confirm_current_source: candidate.stale,
      });
      onChange(next);
      onMessage('短片范围已保存');
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  const saveLayout = async (layout: ClipLayout) => {
    setBusy(true);
    setError('');
    try {
      const next = await api.updateClipLayout(candidate.id, layout.aspect_ratio, {
        enabled: layout.enabled,
        composition: layout.composition,
        focal_x: layout.focal_x,
        focal_y: layout.focal_y,
        subtitle_mode: layout.subtitle_mode,
        style: layout.style || {},
        expected_revision: candidate.layouts.find(item => item.aspect_ratio === layout.aspect_ratio)?.revision || 0,
      });
      onChange(next);
      onMessage(`${layout.aspect_ratio} 构图已保存`);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  const duration = Number(draft.end) - Number(draft.start);
  const recommended = duration >= 30 && duration <= 90;
  return <article className={`clip-candidate-card ${draft.selected ? 'selected' : ''}`}>
    <header>
      <label className="clip-select"><input type="checkbox" checked={draft.selected} onChange={event => setDraft({ ...draft, selected: event.target.checked })}/><span>选择渲染</span></label>
      <span className="clip-score">{Math.round(candidate.score * (candidate.score <= 1 ? 100 : 1))} 分</span>
      {candidate.stale && <em>字幕已更新</em>}
    </header>
    <input className="clip-title" value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })}/>
    <p><strong>开场钩子：</strong>{candidate.hook || '—'}</p>
    <p><strong>推荐理由：</strong>{candidate.reason || '—'}</p>
    <div className="clip-range-editor">
      <label>开始（秒）<input type="number" min={0} step={0.1} value={draft.start} onChange={event => setDraft({ ...draft, start: Number(event.target.value) })}/></label>
      <span>{timecode(draft.start)} → {timecode(draft.end)}<small className={recommended ? '' : 'warning'}>{duration.toFixed(1)} 秒{recommended ? ' · 推荐范围' : ' · 人工范围允许 15–180 秒'}</small></span>
      <label>结束（秒）<input type="number" min={0} step={0.1} value={draft.end} onChange={event => setDraft({ ...draft, end: Number(event.target.value) })}/></label>
      <button className="button secondary" onClick={() => onPreview(draft.start, draft.end)}>循环预览</button>
      <button className="button primary" disabled={busy || duration < 15 || duration > 180} onClick={() => void saveCandidate()}>保存范围</button>
    </div>
    <div className="clip-layout-grid">{draft.layouts.map(layout =>
      <section key={layout.aspect_ratio} className={layout.enabled ? 'enabled' : ''}>
        <header><label><input type="checkbox" checked={layout.enabled} onChange={event => mutateLayout(layout.aspect_ratio, { enabled: event.target.checked })}/><strong>{layout.aspect_ratio}</strong></label><small>{layout.aspect_ratio === '9:16' ? '1080×1920' : layout.aspect_ratio === '1:1' ? '1080×1080' : '1920×1080'}</small></header>
        <AppSelect value={layout.composition} onChange={composition => mutateLayout(layout.aspect_ratio, { composition: composition as 'blur' | 'crop' })} label={`${layout.aspect_ratio} 构图`} options={[
          { value: 'blur', label: '模糊背景', description: '完整保留原画面' },
          { value: 'crop', label: '填满裁切', description: '按焦点决定裁切中心' },
        ]}/>
        {layout.composition === 'crop' && <div className="clip-focal">
          <div
            className={`clip-focal-preview aspect-${layout.aspect_ratio.replace(':', 'x')}`}
            style={previewImage ? { backgroundImage: `url("${previewImage}")` } : undefined}
            role="application"
            aria-label={`${layout.aspect_ratio} 裁切焦点预览`}
            onPointerDown={event => moveFocalPoint(event, layout.aspect_ratio)}
            onPointerMove={event => moveFocalPoint(event, layout.aspect_ratio)}
          >
            <i style={{ left: `${layout.focal_x * 100}%`, top: `${layout.focal_y * 100}%` }}/>
            <span>拖动焦点决定裁切中心</span>
          </div>
          <label>水平焦点<input type="range" min={0} max={1} step={0.01} value={layout.focal_x} onChange={event => mutateLayout(layout.aspect_ratio, { focal_x: Number(event.target.value) })}/></label>
          <label>垂直焦点<input type="range" min={0} max={1} step={0.01} value={layout.focal_y} onChange={event => mutateLayout(layout.aspect_ratio, { focal_y: Number(event.target.value) })}/></label>
        </div>}
        <AppSelect value={layout.subtitle_mode} onChange={subtitle_mode => mutateLayout(layout.aspect_ratio, { subtitle_mode: subtitle_mode as ClipLayout['subtitle_mode'] })} label={`${layout.aspect_ratio} 字幕`} options={[
          { value: 'off', label: '关闭字幕' },
          { value: 'original', label: '原文' },
          { value: 'translated', label: '译文' },
          { value: 'bilingual', label: '双语' },
        ]}/>
        <button className="button secondary" disabled={busy} onClick={() => void saveLayout(layout)}>保存此布局</button>
      </section>)}</div>
    {!!candidate.renders.length && <div className="clip-render-list">{candidate.renders.map(render =>
      <span key={render.id} className={render.status}><strong>{render.aspect_ratio}</strong><small>{render.status === 'success' ? `${render.width}×${render.height} · ${Number(render.duration || 0).toFixed(1)} 秒` : render.error || render.status}</small>{render.status === 'success' && <button onClick={() => void api.downloadClipRender(render.id, `${candidate.title}-${render.aspect_ratio.replace(':', 'x')}.mp4`).then(saved => onMessage(saved ? '短片已保存' : '已取消保存短片')).catch(reason => setError(errorMessage(reason)))}>下载</button>}{['failed', 'cancelled'].includes(render.status) && <button onClick={() => void api.deleteClipRender(render.id).then(() => onMessage('短片记录已删除'))}>删除</button>}</span>)}</div>}
    {error && <p className="content-error">{error}</p>}
  </article>;
}

function ClipWorkbench({
  project, hasSegments, granted, onRequireAuthorization, onPreview, onMessage,
}: {
  project: Project;
  hasSegments: boolean;
  granted: boolean;
  onRequireAuthorization: () => Promise<boolean>;
  onPreview: (start: number, end: number) => void;
  onMessage: (message: string) => void;
}) {
  const [sets, setSets] = useState<ClipSet[]>([]);
  const [selected, setSelected] = useState<ClipSet | null>(null);
  const [name, setName] = useState('短片候选');
  const [count, setCount] = useState<3 | 5 | 10>(5);
  const [minimum, setMinimum] = useState(30);
  const [maximum, setMaximum] = useState(90);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const refresh = useCallback(async (preferredId?: string) => {
    const result = await api.getClipSets(project.id);
    if (!alive.current) return;
    setSets(result.clip_sets);
    const id = preferredId || selected?.id || result.clip_sets[0]?.id;
    if (id) {
      const detail = await api.getClipSet(id);
      if (alive.current) setSelected(detail);
    } else setSelected(null);
  }, [project.id, selected?.id]);

  useEffect(() => {
    setSelected(null);
    setSets([]);
    void refresh().catch(reason => setError(errorMessage(reason)));
  }, [project.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const waitForTask = useCallback(async (taskId: string, setId: string, message: string) => {
    while (alive.current) {
      const task = await api.getTaskStatus(taskId);
      if (TERMINAL_TASKS.has(task.status)) {
        await refresh(setId);
        if (task.status === 'failed') throw new Error(task.error || task.message);
        onMessage(message);
        return;
      }
      await new Promise(resolve => window.setTimeout(resolve, 1000));
    }
  }, [onMessage, refresh]);

  const create = async () => {
    if (!hasSegments) return;
    if (!granted && !(await onRequireAuthorization())) return;
    setBusy(true);
    setError('');
    try {
      const result = await api.createClipSet(project.id, {
        name, desired_count: count, min_duration: minimum, max_duration: maximum,
      });
      await refresh(result.clip_set_id);
      void waitForTask(result.task_id, result.clip_set_id, '短片候选已生成').catch(reason => setError(errorMessage(reason)));
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  const render = async () => {
    if (!selected) return;
    const hasSelectedStaleCandidate = (selected.candidates || []).some(
      candidate => candidate.selected && candidate.stale,
    );
    const items = (selected.candidates || []).flatMap(candidate =>
      candidate.selected
        ? candidate.layouts.filter(layout => layout.enabled).map(layout => ({
          candidate_id: candidate.id, aspect_ratio: layout.aspect_ratio,
        }))
        : []);
    if (!items.length) {
      setError('请先选择至少一个候选，并启用一个输出比例。');
      return;
    }
    if (project.source_type === 'youtube' && !project.video_available && !window.confirm('这个网页播放项目需要先下载本地视频。实际占用取决于源视频画质，下载完成后才会开始渲染。继续吗？')) return;
    if (hasSelectedStaleCandidate && !window.confirm('字幕已经更新。请确认你已重新预览当前范围；继续渲染吗？')) return;
    setBusy(true);
    setError('');
    try {
      const result = await api.renderClips(project.id, { items, confirm_stale: hasSelectedStaleCandidate });
      if (result.task_id) {
        onMessage(`已提交 ${items.length} 个短片输出，可在任务中心暂停或取消`);
        void waitForTask(result.task_id, selected.id, '短片渲染完成').catch(reason => setError(errorMessage(reason)));
      } else {
        await refresh(selected.id);
        onMessage('相同配置的成片已存在，已直接复用');
      }
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  return <div className="clip-workbench">
    <header className="clip-workbench-toolbar">
      <div><h2>长视频短片工作台</h2><p>AI 只推荐真实字幕范围；确认前不会下载、裁切或渲染视频。</p></div>
      {selected && <div><AppSelect value={selected.id} onChange={id => void api.getClipSet(id).then(setSelected).catch(reason => setError(errorMessage(reason)))} label="短片集合" options={sets.map(item => ({ value: item.id, label: item.name, description: `${item.candidate_count || 0} 个候选` }))}/><button className="button secondary" onClick={() => setSelected(null)}>新建集合</button><button className="button primary" disabled={busy} onClick={() => void render()}>渲染所选版本</button></div>}
    </header>
    {!selected && <section className="clip-create-card">
      <label>集合名称<input value={name} onChange={event => setName(event.target.value)}/></label>
      <label>推荐数量<AppSelect value={String(count)} onChange={value => setCount(Number(value) as 3 | 5 | 10)} label="短片推荐数量" options={[3, 5, 10].map(value => ({ value: String(value), label: `${value} 个` }))}/></label>
      <label>最短秒数<input type="number" min={15} max={179} value={minimum} onChange={event => setMinimum(Number(event.target.value))}/></label>
      <label>最长秒数<input type="number" min={16} max={180} value={maximum} onChange={event => setMaximum(Number(event.target.value))}/></label>
      <button className="button primary" disabled={busy || minimum >= maximum || !hasSegments} onClick={() => void create()}>{busy ? '正在创建…' : '生成候选'}</button>
    </section>}
    {selected?.stale && <div className="content-stale-banner">源字幕已更新。候选仍然保留，但渲染前必须重新预览并确认当前时间范围。</div>}
    {selected && <div className="clip-candidate-list">{(selected.candidates || []).map(candidate =>
      <ClipCandidateCard key={candidate.id} candidate={candidate}
        previewImage={project.thumbnail_access_url || project.thumbnail_url}
        onChange={next => {
          setSelected(next);
          setSets(current => current.map(item => item.id === next.id ? { ...item, ...next, candidates: undefined } : item));
        }}
        onPreview={onPreview} onMessage={onMessage}/>)}</div>}
    {error && <p className="content-error">{error}</p>}
  </div>;
}

export default function ContentCenter({ project, projectRevision, hasSegments, onPreview, onMessage }: Props) {
  const [tab, setTab] = useState<ContentTab>('packs');
  const [granted, setGranted] = useState(false);
  const [authorizationBusy, setAuthorizationBusy] = useState(false);

  const loadAuthorization = useCallback(async () => {
    const result = await api.getCloudAuthorizations();
    setGranted(Boolean(result.authorizations.find(item => item.capability === 'content')?.granted));
  }, []);

  useEffect(() => {
    void loadAuthorization().catch(reason => onMessage(errorMessage(reason)));
  }, [loadAuthorization, onMessage]);

  const requireAuthorization = useCallback(async () => {
    if (granted) return true;
    const accepted = window.confirm(
      '内容生成与短片推荐会把当前项目标题、所选字幕文字、时间码和说话人标签发送给你配置的内容生成服务商。\n\n不会上传视频、音频、本机路径或其他项目内容。是否授权？',
    );
    if (!accepted) return false;
    setAuthorizationBusy(true);
    try {
      const providers = await api.getAIProviders();
      await api.setCloudAuthorization('content', true, providers.assignments.content_provider_id);
      setGranted(true);
      onMessage('内容生成云端授权已保存，可随时在此撤销');
      return true;
    } catch (reason) {
      onMessage(errorMessage(reason));
      return false;
    } finally {
      setAuthorizationBusy(false);
    }
  }, [granted, onMessage]);

  const revoke = async () => {
    try {
      await api.setCloudAuthorization('content', false);
      setGranted(false);
      onMessage('内容生成云端授权已撤销；已有内容仍可查看、编辑和导出');
    } catch (reason) {
      onMessage(errorMessage(reason));
    }
  };

  return <section className="task-page content-center-page" data-project-revision={projectRevision}>
    <header className="content-center-header">
      <div><small>内容再生产</small><h2>从字幕到可发布内容</h2><p>发布包与短片定义保存在项目中，视频输出按需在本机生成。</p></div>
      <nav role="tablist" aria-label="内容工作区">
        <button role="tab" aria-selected={tab === 'packs'} className={tab === 'packs' ? 'active' : ''} onClick={() => setTab('packs')}>发布包</button>
        <button role="tab" aria-selected={tab === 'clips'} className={tab === 'clips' ? 'active' : ''} onClick={() => setTab('clips')}>短视频</button>
      </nav>
      {granted && <button className="button secondary" onClick={() => void revoke()}>撤销云端授权</button>}
    </header>
    {!hasSegments && <div className="content-source-required" role="status">
      <span>尚无字幕</span>
      <div>
        <strong>先生成或导入字幕</strong>
        <p>完成“处理 → 转写”，或在“字幕”工作区导入字幕后，再创建发布包和短视频候选。</p>
      </div>
    </div>}
    {hasSegments && <ContentAuthorization granted={granted} busy={authorizationBusy} onGrant={() => void requireAuthorization()}/>}
    {tab === 'packs'
      ? <PublicationPacks project={project} hasSegments={hasSegments} granted={granted} onRequireAuthorization={requireAuthorization} onMessage={onMessage}/>
      : <ClipWorkbench project={project} hasSegments={hasSegments} granted={granted} onRequireAuthorization={requireAuthorization} onPreview={onPreview} onMessage={onMessage}/>}
  </section>;
}
