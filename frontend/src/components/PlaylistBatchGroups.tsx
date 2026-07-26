import { useState } from 'react';
import * as api from '../api/backend';
import type { PlaylistBatchDetail, PlaylistBatchItem, Project } from '../types';

interface Props {
  batches: PlaylistBatchDetail[];
  search: string;
  collapsed: Set<string>;
  workflow: Record<string, unknown>;
  onToggle: (id: string) => void;
  onOpenProject: (project: Project) => void;
  onChanged: () => void;
  onMessage: (message: string) => void;
}

const stageLabels = { download: '下载', extract_audio: '音频', transcribe: '转写', clean: '整理', translate: '翻译' } as const;

function visibleItems(batch: PlaylistBatchDetail, search: string) {
  const query = search.trim().toLocaleLowerCase();
  if (!query || batch.batch.title.toLocaleLowerCase().includes(query)) return batch.items;
  return batch.items.filter(item => item.title.toLocaleLowerCase().includes(query));
}

export default function PlaylistBatchGroups({ batches, search, collapsed, workflow, onToggle, onOpenProject, onChanged, onMessage }: Props) {
  const matching = batches.map(detail => ({ detail, items: visibleItems(detail, search) })).filter(value => value.items.length);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; title: string; itemCount: number } | null>(null);
  const [deletePhrase, setDeletePhrase] = useState('');
  const [pendingAction, setPendingAction] = useState<{ title: string; message: string; confirmLabel: string; danger?: boolean; success: string; action: () => Promise<unknown> } | null>(null);

  async function act(label: string, action: () => Promise<unknown>) {
    try { await action(); onMessage(label); onChanged(); }
    catch (error) { onMessage(error instanceof Error ? error.message : String(error)); }
  }

  function retryItem(batchId: string, item: PlaylistBatchItem) {
    void act(`正在重试“${item.title}”`, () => api.retryPlaylistItem(batchId, item.id));
  }

  async function permanentlyDeletePlaylist() {
    if (!deleteTarget || deletePhrase !== '删除') return;
    try {
      await api.deletePlaylistBatch(deleteTarget.id);
      onMessage('播放列表及本地缓存已永久删除');
      setDeleteTarget(null);
      setDeletePhrase('');
      onChanged();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function runConfirmedAction() {
    if (!pendingAction) return;
    try {
      await pendingAction.action();
      onMessage(pendingAction.success);
      setPendingAction(null);
      onChanged();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : String(error));
    }
  }

  return <>{matching.length ? <section className="playlist-batch-section" aria-label="播放列表批量任务">
    <div className="playlist-batch-section-title"><span>播放列表批量任务</span><small>{matching.length}</small></div>
    {matching.map(({ detail, items }) => {
      const batch = detail.batch;
      const isCollapsed = collapsed.has(batch.id) && !search.trim();
      return <article className={`playlist-batch-group status-${batch.status}`} key={batch.id}>
        <header>
          <button className="playlist-batch-toggle" aria-expanded={!isCollapsed} onClick={() => onToggle(batch.id)}>
            <span className="playlist-batch-cover">{batch.thumbnail_url ? <img src={batch.thumbnail_url} alt=""/> : '▶'}</span>
            <span><strong>{batch.title}</strong><small>{batch.channel || 'YouTube'} · {batch.completed_count}/{batch.item_count} 完成{batch.failed_count ? ` · ${batch.failed_count} 项需处理` : ''}</small><i><b style={{ width: `${batch.progress}%` }}/></i></span>
            <em>{Math.round(batch.progress)}%</em><u>{isCollapsed ? '›' : '⌄'}</u>
          </button>
          <div className="playlist-batch-actions">
            {batch.status === 'paused' ? <button onClick={() => void act('批次已继续', () => api.resumePlaylistBatch(batch.id))}>继续</button> : <button disabled={!['running','pending'].includes(batch.status)} onClick={() => void act('批次已暂停', () => api.pausePlaylistBatch(batch.id))}>暂停</button>}
            <button onClick={() => void act('已提交失败项重试', () => api.retryPlaylistBatch(batch.id))}>重试失败项</button>
            <button onClick={() => void act('播放列表已同步', () => api.syncPlaylistBatch(batch.id))}>同步</button>
            <button onClick={() => void act('已启动批量转写', () => api.runPlaylistStage(batch.id, 'transcribe', workflow))}>批量转写</button>
            <button onClick={() => setPendingAction({ title: '确认批量 AI 整理', message: '将把该播放列表中符合条件的字幕发送到当前 AI 整理服务，可能产生费用。', confirmLabel: '授权并开始整理', success: '已启动 AI 整理', action: () => api.runPlaylistStage(batch.id, 'clean', { ...workflow, ai_authorized: true }) })}>AI 整理</button>
            <button onClick={() => setPendingAction({ title: '确认批量 AI 翻译', message: '将把该播放列表中符合条件的字幕发送到当前 AI 翻译服务，可能产生费用。', confirmLabel: '授权并开始翻译', success: '已启动 AI 翻译', action: () => api.runPlaylistStage(batch.id, 'translate', { ...workflow, ai_authorized: true }) })}>AI 翻译</button>
            <button onClick={() => setPendingAction({ title: '取消未完成任务？', message: '将终止尚未完成的任务；已下载媒体、已有字幕和项目记录都会保留。', confirmLabel: '取消未完成任务', danger: true, success: '未完成任务已取消', action: () => api.cancelPlaylistBatch(batch.id) })}>取消</button>
            <button className="danger" onClick={() => { setDeletePhrase(''); setDeleteTarget({ id: batch.id, title: batch.title, itemCount: batch.item_count }); }}>删除播放列表</button>
          </div>
        </header>
        {!isCollapsed && <div className="playlist-batch-items">{items.map(item => <div className={`playlist-batch-item ${item.status}`} key={item.id}>
          <button className="playlist-item-main" disabled={!item.project} onClick={() => item.project && onOpenProject(item.project)}>
            <span>{item.thumbnail_url ? <img src={item.thumbnail_url} alt="" loading="lazy"/> : item.position}</span>
            <span><strong>{item.position}. {item.title}</strong><small>{item.source_state === 'removed' ? '已从源播放列表移除' : item.source_state === 'unavailable' ? '视频不可用' : item.status}</small></span>
          </button>
          <div className="playlist-stage-pills">{Object.entries(stageLabels).map(([stage, label]) => {
            const state = item.stages[stage as keyof typeof item.stages]?.status || 'skipped';
            return <span className={state} title={item.stages[stage as keyof typeof item.stages]?.error || ''} key={stage}>{label}</span>;
          })}</div>
          {['failed','partial','cancelled'].includes(item.status) && <button className="playlist-item-retry" onClick={() => retryItem(batch.id, item)}>重试</button>}
        </div>)}</div>}
      </article>;
    })}
  </section> : null}
    {pendingAction && <div className="production-overlay playlist-delete-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setPendingAction(null); }}>
      <section className="playlist-action-dialog" role="dialog" aria-modal="true" aria-labelledby="playlist-action-title">
        <header><h2 id="playlist-action-title">{pendingAction.title}</h2><button type="button" aria-label="关闭操作确认" onClick={() => setPendingAction(null)}>×</button></header>
        <p>{pendingAction.message}</p>
        <footer>
          <button type="button" onClick={() => setPendingAction(null)}>返回</button>
          <button type="button" className={pendingAction.danger ? 'danger' : 'primary'} onClick={() => void runConfirmedAction()}>{pendingAction.confirmLabel}</button>
        </footer>
      </section>
    </div>}
    {deleteTarget && <div className="production-overlay playlist-delete-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) { setDeleteTarget(null); setDeletePhrase(''); } }}>
      <section className="playlist-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="playlist-delete-title">
        <header>
          <div><small>不可撤销的操作</small><h2 id="playlist-delete-title">永久删除播放列表？</h2></div>
          <button type="button" aria-label="关闭删除确认" onClick={() => { setDeleteTarget(null); setDeletePhrase(''); }}>×</button>
        </header>
        <p>将删除“<strong>{deleteTarget.title}</strong>”及其全部 {deleteTarget.itemCount} 个子项目。</p>
        <div className="playlist-delete-warning">
          本地下载视频、音频、字幕、封面、导出文件和任务记录都会彻底删除，并且无法恢复。
        </div>
        <label>
          <span>请输入“删除”以确认</span>
          <input autoFocus value={deletePhrase} onChange={event => setDeletePhrase(event.target.value)} placeholder="删除" />
        </label>
        <footer>
          <button type="button" onClick={() => { setDeleteTarget(null); setDeletePhrase(''); }}>保留播放列表</button>
          <button type="button" className="danger" disabled={deletePhrase !== '删除'} onClick={() => void permanentlyDeletePlaylist()}>永久删除全部文件</button>
        </footer>
      </section>
    </div>}
  </>;
}
