import { useSlidingIndicator } from '../utils/motion';

const GridIcon = () => <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><rect x="1.5" y="1.5" width="5.5" height="5.5" rx="1.4"/><rect x="9" y="1.5" width="5.5" height="5.5" rx="1.4"/><rect x="1.5" y="9" width="5.5" height="5.5" rx="1.4"/><rect x="9" y="9" width="5.5" height="5.5" rx="1.4"/></svg>;
const ListIcon = () => <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><rect x="1.5" y="2" width="3.5" height="3" rx="1"/><rect x="6.5" y="2.6" width="8" height="1.8" rx=".9"/><rect x="1.5" y="6.5" width="3.5" height="3" rx="1"/><rect x="6.5" y="7.1" width="8" height="1.8" rx=".9"/><rect x="1.5" y="11" width="3.5" height="3" rx="1"/><rect x="6.5" y="11.6" width="8" height="1.8" rx=".9"/></svg>;

export default function LibraryControls({ page,pages,total,loading,error,compact,status,onPage,onCompact,onStatus,onRetry }: {
  page:number; pages:number; total:number; loading:boolean; error:string; compact:boolean; status:string;
  onPage:(page:number) => void; onCompact:() => void; onStatus:(value:string) => void; onRetry:() => void;
}) {
  const viewRef = useSlidingIndicator<HTMLDivElement>(compact);
  return <section className="library-pagination" aria-label="项目列表控制">
    <label>项目状态<select value={status} onChange={event => onStatus(event.target.value)}><option value="">全部</option><option value="subtitles">已有字幕</option><option value="transcribe">待转写</option><option value="media">待准备素材</option></select></label>
    <div ref={viewRef} className="library-view-toggle sliding-tabs" role="group" aria-label="项目显示方式">
      <button type="button" aria-pressed={!compact} aria-label="卡片视图" title="卡片视图" onClick={() => compact && onCompact()}><GridIcon/></button>
      <button type="button" aria-pressed={compact} aria-label="紧凑列表" title="紧凑列表" onClick={() => !compact && onCompact()}><ListIcon/></button>
    </div>
    <span role="status">{loading ? '正在加载…' : `共 ${total} 个项目`}</span>
    {pages > 1 && <span className="library-pager">
      <button type="button" aria-label="上一页" disabled={loading || page <= 1} onClick={() => onPage(page-1)}><i className="chevron collapsed flip" aria-hidden="true"/></button>
      <span>{page} / {pages}</span>
      <button type="button" aria-label="下一页" disabled={loading || page >= pages} onClick={() => onPage(page+1)}><i className="chevron collapsed" aria-hidden="true"/></button>
    </span>}
    {error && <span role="alert">{error}<button onClick={onRetry}>重试加载</button></span>}
  </section>;
}
