// 字幕工厂 - 结构化日志查看器组件

import { useRef, useEffect, useState, useCallback } from 'react';
import type { ProcessLogEntry } from '../types';

interface Props {
  logs: ProcessLogEntry[];
  collapsed?: boolean;
  onToggle?: () => void;
  onClear?: () => void;
}

const LEVEL_ICON: Record<string, string> = {
  info: 'ℹ',
  warning: '⚠',
  error: '✕',
};

export default function ProcessLogViewer({ logs, collapsed, onToggle, onClear }: Props) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const previousLogCount = useRef(logs.length);
  const lastLogId = logs[logs.length - 1]?.id;
  const previousLastLogId = useRef(lastLogId);
  const [showDetail, setShowDetail] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const [autoFollow, setAutoFollow] = useState(true);
  const [unreadCount, setUnreadCount] = useState(0);

  // 只滚动日志框本身。scrollIntoView 会连带滚动右侧检查器，导致整个
  // 操作面板在任务运行时不断被拽到底部。
  useEffect(() => {
    const previousCount = previousLogCount.current;
    const lastChanged = lastLogId !== previousLastLogId.current;
    let added = Math.max(0, logs.length - previousCount);
    // App 只保留最近 200 条；达到上限后长度不变，但末条 id 仍会变化。
    if (added === 0 && logs.length > 0 && lastChanged) added = 1;
    const wasCleared = logs.length === 0 && previousCount > 0;
    previousLogCount.current = logs.length;
    previousLastLogId.current = lastLogId;

    if (wasCleared) {
      setUnreadCount(0);
      setAutoFollow(true);
    }
    if (collapsed || !bodyRef.current) return;
    if (autoFollow || wasCleared) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
      setUnreadCount(0);
    } else if (added > 0) {
      setUnreadCount(current => current + added);
    }
  }, [logs.length, lastLogId, collapsed, autoFollow]);

  const followLatest = useCallback(() => {
    setAutoFollow(true);
    setUnreadCount(0);
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, []);

  const handleLogScroll = useCallback(() => {
    const body = bodyRef.current;
    if (!body) return;
    const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 18;
    setAutoFollow(nearBottom);
    if (nearBottom) setUnreadCount(0);
  }, []);

  const copyLogs = useCallback(() => {
    const text = logs.map(l => `[${l.time}] [${l.level}] ${l.step}: ${l.message}`).join('\n');
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [logs]);

  return (
    <section className="panel process-log-panel">
      <div className="panel-header clickable" role="button" tabIndex={0} aria-expanded={!collapsed}
        onClick={onToggle}
        onKeyDown={event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onToggle?.();
          }
        }}>
        <h3>📋 运行日志 ({logs.length})</h3>
        <div className="panel-header-actions">
          {!collapsed && logs.length > 0 && (
            <>
              {!autoFollow && (
                <button className="btn btn-ghost btn-sm log-follow-button" onClick={e => { e.stopPropagation(); followLatest(); }}>
                  ↓ {unreadCount > 0 ? `${unreadCount} 条新日志` : '回到最新'}
                </button>
              )}
              <button className="btn btn-ghost btn-sm" onClick={e => { e.stopPropagation(); copyLogs(); }}>
                {copied ? '✓' : '📋'} 复制
              </button>
              {onClear && (
                <button className="btn btn-ghost btn-sm" onClick={e => { e.stopPropagation(); onClear(); }}>
                  🗑 清空
                </button>
              )}
            </>
          )}
          <span className="collapse-icon">{collapsed ? '▶' : '▼'}</span>
        </div>
      </div>

      {!collapsed && (
        <div className="process-log-body" ref={bodyRef} onScroll={handleLogScroll}
          role="log" aria-live="polite" aria-relevant="additions">
          <div className="process-log-scroll">
            {logs.map((log, i) => {
              const expandable = Boolean(log.detail || log.suggestion);
              return (
              <div key={log.id || i}
                className={`log-entry log-${log.level} ${showDetail === i ? 'log-detail-open' : ''}`}
                role={expandable ? 'button' : undefined}
                tabIndex={expandable ? 0 : undefined}
                aria-expanded={expandable ? showDetail === i : undefined}
                onClick={() => expandable && setShowDetail(showDetail === i ? null : i)}
                onKeyDown={event => {
                  if (expandable && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault();
                    setShowDetail(showDetail === i ? null : i);
                  }
                }}
              >
                <span className="log-level-icon">{LEVEL_ICON[log.level] || 'ℹ'}</span>
                <span className="log-time">{log.time}</span>
                <span className="log-step">[{log.step}]</span>
                <span className="log-msg">{log.message}</span>

                {showDetail === i && (log.detail || log.suggestion) && (
                  <div className="log-detail">
                    {log.detail && <div className="log-detail-text">{log.detail}</div>}
                    {log.suggestion && <div className="log-suggestion">💡 {log.suggestion}</div>}
                  </div>
                )}
              </div>
              );
            })}
          </div>

          {logs.length === 0 && (
            <div className="log-empty-text">等待操作...</div>
          )}
        </div>
      )}
    </section>
  );
}
