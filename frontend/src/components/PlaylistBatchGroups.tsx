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

  async function act(label: string, action: () => Promise<unknown>) {
    try { await action(); onMessage(label); onChanged(); }
    catch (error) { onMessage(error instanceof Error ? error.message : String(error)); }
  }

  function retryItem(batchId: string, item: PlaylistBatchItem) {
    void act(`正在重试“${item.title}”`, () => api.retryPlaylistItem(batchId, item.id));
  }

  return matching.length ? <section className="playlist-batch-section" aria-label="播放列表批量任务">
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
            <button onClick={() => { if (window.confirm('将把该播放列表中符合条件的字幕发送到当前 AI 整理服务，可能产生费用。是否继续？')) void act('已启动 AI 整理', () => api.runPlaylistStage(batch.id, 'clean', { ...workflow, ai_authorized: true })); }}>AI 整理</button>
            <button onClick={() => { if (window.confirm('将把该播放列表中符合条件的字幕发送到当前 AI 翻译服务，可能产生费用。是否继续？')) void act('已启动 AI 翻译', () => api.runPlaylistStage(batch.id, 'translate', { ...workflow, ai_authorized: true })); }}>AI 翻译</button>
            <button onClick={() => { if (window.confirm('取消尚未完成的任务？已下载媒体和字幕会保留。')) void act('未完成任务已取消', () => api.cancelPlaylistBatch(batch.id)); }}>取消</button>
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
  </section> : null;
}
