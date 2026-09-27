import { useEffect, useRef, type ReactNode } from 'react';

/** Nonmodal tools keep the editor mounted, with reversible focus restoration. */
export default function WorkspacePanel({ title, children, actions, onClose }: { title: string; children: ReactNode; actions?: ReactNode; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  return <aside className="workspace-tool-panel" role="dialog" aria-label={title} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onClose(); } }}>
    <header><h2>{title}</h2><div className="workspace-panel-actions">{actions}<button ref={closeRef} className="panel-close" onClick={onClose} aria-label="关闭任务与工具" title="关闭 (Esc)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg></button></div></header>
    {children}
  </aside>;
}
