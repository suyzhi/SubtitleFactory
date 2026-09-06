import { useEffect, useRef, useState, type ReactNode } from 'react';
import './EditorWorkbench.css';

function savedRatio(key: string, fallback: number) {
  const value = Number(localStorage.getItem(key));
  return value >= 25 && value <= 65 ? value : fallback;
}
type Layout = 'auto' | 'wide' | 'stacked';

/** One persistent player and editor. Panels resize within the available workspace. */
export default function EditorWorkbench({ player, editor, timeline, toolbar, inspector }: {
  player: ReactNode; editor: ReactNode; timeline: ReactNode; toolbar: ReactNode; inspector?: ReactNode;
}) {
  const root = useRef<HTMLElement>(null);
  const [width, setWidth] = useState(0);
  const [layout, setLayout] = useState<Layout>(() => {
    const saved = localStorage.getItem('subtitle_factory_editor_layout');
    return saved === 'wide' || saved === 'stacked' ? saved : 'auto';
  });
  const [focused, setFocused] = useState(false);
  const [horizontal, setHorizontal] = useState(() => savedRatio('subtitle_factory_editor_horizontal', 50));
  const [vertical, setVertical] = useState(() => savedRatio('subtitle_factory_editor_vertical', 50));
  const wide = width >= 760 && (layout === 'wide' || (layout === 'auto' && width >= 1040));
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    setWidth(element.clientWidth);
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => { localStorage.setItem('subtitle_factory_editor_horizontal', String(horizontal)); }, [horizontal]);
  useEffect(() => { localStorage.setItem('subtitle_factory_editor_vertical', String(vertical)); }, [vertical]);
  useEffect(() => { localStorage.setItem('subtitle_factory_editor_layout', layout); }, [layout]);
  const ratio = wide ? horizontal : vertical;
  const changeRatio = (value: number) => (wide ? setHorizontal : setVertical)(Math.max(25, Math.min(65, value)));
  return <section ref={root} className={`editor-workbench ${wide ? 'wide' : 'stacked'} ${focused ? 'text-focused' : ''}`} aria-label="播放与字幕校对">
    <header className="editor-actions">
      <div className="editor-primary-actions">{toolbar}</div>
      <div className="editor-view-actions">
        <label>布局<select aria-label="编辑器布局" value={layout} disabled={focused} onChange={event => setLayout(event.target.value as Layout)}>
          <option value="auto">自动</option><option value="wide" disabled={width < 760}>左右</option><option value="stacked">上下</option>
        </select></label>
        <label className="editor-ratio-control">播放区<input aria-label="播放区比例" type="range" min={25} max={65} step={1} value={ratio} disabled={focused} onChange={event => changeRatio(Number(event.target.value))}/><output>{Math.round(ratio)}%</output></label>
        <button className="button secondary editor-reset" disabled={focused} title="恢复当前布局的均分比例" onClick={() => changeRatio(50)}>均分</button>
        <button className="button secondary" aria-pressed={focused} onClick={() => setFocused(value => !value)}>{focused ? '显示播放器' : '专注字幕'}</button>
      </div>
    </header>
    <div className="editor-body" style={{ '--editor-ratio': `${ratio}%` } as React.CSSProperties}>
      <div className="editor-player">{player}</div>
      <div className="editor-divider" role="separator" tabIndex={0} aria-label="调整播放与字幕比例" aria-orientation={wide ? 'vertical' : 'horizontal'} aria-valuenow={Math.round(ratio)} aria-valuemin={25} aria-valuemax={65}
        onPointerDown={event => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); }}
        onDoubleClick={() => changeRatio(50)} title="拖动调整比例；双击均分"
        onPointerMove={event => {
          if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
          const bounds = event.currentTarget.parentElement!.getBoundingClientRect();
          changeRatio(wide ? (event.clientX - bounds.left) / bounds.width * 100 : (event.clientY - bounds.top) / bounds.height * 100);
        }} onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
        onKeyDown={event => { if (['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown'].includes(event.key)) { event.preventDefault(); changeRatio(ratio + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -2 : 2)); } }} />
      <div className="editor-transcript">{editor}</div>
      {inspector && <aside className="editor-inspector">{inspector}</aside>}
    </div>
    <div className="editor-timeline">{timeline}</div>
  </section>;
}
