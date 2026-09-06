import { useState, type ReactNode } from 'react';

export type ImportSource = { kind:'files'; files:File[] } | { kind:'link'; url:string };
export default function ImportFlow({ source, setup, canStart, busy, onStart, onClose }: {
  source: ImportSource; setup: ReactNode; canStart:boolean; busy:boolean;
  onStart:(generate:boolean,remember:boolean) => Promise<void>; onClose:() => void;
}) {
  const [remember,setRemember] = useState(false);
  const [error,setError] = useState('');
  const start = (generate:boolean) => { setError(''); void onStart(generate,remember).catch(error => setError(error instanceof Error ? error.message : '导入失败')); };
  return <div className="modal-backdrop"><section className="import-flow" role="dialog" aria-modal="true" aria-label="素材准备">
    <header><h2>导入素材</h2><button className="button secondary" disabled={busy} onClick={onClose}>取消</button></header>
    <p>{source.kind === 'files' ? source.files.map(file => file.name).join('、') : source.url}</p>
    {setup}
    <label className="check-row"><input type="checkbox" checked={remember} onChange={event => setRemember(event.target.checked)}/>以后使用相同配置直接导入并转写</label>
    <p>转写配置可随时在任务面板中调整。“仅导入”保留素材，稍后再生成字幕。</p>
    {error && <p role="alert">{error}</p>}
    <footer><button className="button secondary" disabled={busy} onClick={() => start(false)}>仅导入</button><button className="button primary" disabled={busy || !canStart} onClick={() => start(true)}>{busy ? '正在准备…' : '导入并生成字幕'}</button></footer>
  </section></div>;
}
