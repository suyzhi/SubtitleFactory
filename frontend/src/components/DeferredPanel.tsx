// 懒加载模块的统一占位面板：播放器、检查器、设置中心等按需打开时的骨架屏。

function DeferredPanel({ label, kind = 'panel', theme = 'dark' }: {
  label: string;
  kind?: 'panel' | 'player' | 'overlay' | 'settings';
  theme?: 'light' | 'dark';
}) {
  const status = <div className={`deferred-module deferred-module-${kind}`} role="status" aria-live="polite">
    <i aria-hidden="true"/><span>{label}</span>
  </div>;
  if (kind === 'overlay') {
    return <div className="production-overlay deferred-overlay">{status}</div>;
  }
  if (kind === 'settings') {
    return <div className={`modal-backdrop settings-backdrop theme-${theme}`}>
      <section className="settings-center deferred-settings">{status}</section>
    </div>;
  }
  return status;
}

export default DeferredPanel;
