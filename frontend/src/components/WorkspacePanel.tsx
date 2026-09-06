import { useEffect, useRef, type ReactNode } from 'react';

/** Nonmodal tools keep the editor mounted, with reversible focus restoration. */
export default function WorkspacePanel({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  return <aside className="workspace-tool-panel" role="dialog" aria-label={title} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onClose(); } }}>
    <header><h2>{title}</h2><button ref={closeRef} className="button secondary" onClick={onClose} aria-label="关闭任务与工具">关闭</button></header>
    {children}
  </aside>;
}
