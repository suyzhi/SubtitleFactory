export default function LibraryControls({ page,pages,total,loading,error,compact,status,onPage,onCompact,onStatus,onRetry }: {
  page:number; pages:number; total:number; loading:boolean; error:string; compact:boolean; status:string;
  onPage:(page:number) => void; onCompact:() => void; onStatus:(value:string) => void; onRetry:() => void;
}) {
  return <section className="library-pagination" aria-label="项目列表控制">
    <label>项目状态<select value={status} onChange={event => onStatus(event.target.value)}><option value="">全部</option><option value="subtitles">已有字幕</option><option value="transcribe">待转写</option><option value="media">待准备素材</option></select></label>
    <button className="button secondary" aria-pressed={compact} onClick={onCompact}>{compact ? '卡片视图' : '紧凑列表'}</button>
    <span role="status">{loading ? '正在加载…' : `共 ${total} 个项目 · ${page} / ${pages} 页`}</span>
    <button className="button secondary" disabled={loading || page <= 1} onClick={() => onPage(page-1)}>上一页</button>
    <button className="button secondary" disabled={loading || page >= pages} onClick={() => onPage(page+1)}>下一页</button>
    {error && <span role="alert">{error}<button onClick={onRetry}>重试加载</button></span>}
  </section>;
}
