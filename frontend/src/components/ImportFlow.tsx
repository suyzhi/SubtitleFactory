import { useEffect, useState, type ReactNode } from 'react';

export type ImportSource = { kind:'files'; files:File[] } | { kind:'link'; url:string };

const formatSize = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB`
  : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;

export default function ImportFlow({ source, setup, canStart, busy, onStart, onClose }: {
  source: ImportSource; setup: ReactNode; canStart:boolean; busy:boolean;
  onStart:(generate:boolean,remember:boolean) => Promise<void>; onClose:() => void;
}) {
  const [remember,setRemember] = useState(false);
  const [error,setError] = useState('');
  const start = (generate:boolean) => { setError(''); void onStart(generate,remember).catch(error => setError(error instanceof Error ? error.message : '导入失败')); };
  // Escape works wherever focus is (the file picker returns focus to the page, not the dialog).
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) { event.preventDefault(); onClose(); } };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [busy, onClose]);
  const items = source.kind === 'files'
    ? source.files.map(file => ({ name: file.name, detail: formatSize(file.size) }))
    : [{ name: source.url, detail: '视频链接' }];
  return <div className="modal-backdrop"><section className="import-flow" role="dialog" aria-modal="true" aria-label="素材准备">
    <header>
      <div><small>导入素材</small><h2>{items.length > 1 ? `准备导入 ${items.length} 个视频` : '准备生成字幕'}</h2></div>
      <button type="button" className="panel-close" aria-label="取消导入" title="取消 (Esc)" disabled={busy} onClick={onClose}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg></button>
    </header>
    <ul className="import-files">{items.map(item => <li key={item.name}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 9.5v5l4-2.5z" fill="currentColor"/></svg>
      <span><strong title={item.name}>{item.name}</strong><small>{item.detail}</small></span>
    </li>)}</ul>
    <div className="import-setup">{setup}</div>
    <label className="check-row import-remember"><input type="checkbox" checked={remember} onChange={event => setRemember(event.target.checked)}/>以后使用相同配置，导入后直接转写</label>
    {error && <p role="alert" className="import-error">{error}</p>}
    <footer>
      <p className="import-hint">{!canStart ? '先选择运行设备，才能导入后立即生成字幕' : '转写配置之后也可以在“任务与工具”中调整'}</p>
      <div>
        <button className="button secondary" disabled={busy} onClick={() => start(false)} title="只保存素材，稍后再生成字幕">仅导入</button>
        <button className="button primary" disabled={busy || !canStart} onClick={() => start(true)}>{busy ? '正在准备…' : '导入并生成字幕'}</button>
      </div>
    </footer>
  </section></div>;
}
