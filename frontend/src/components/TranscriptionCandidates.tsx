import { useEffect, useState } from 'react';
import * as api from '../api/backend';
import type { SubtitleSegment, EditorOperationResponse } from '../types';

export default function TranscriptionCandidates({projectId,revision,segments,onAccept,onSeek,hasDraft}: {
  projectId:string; revision:number; segments:SubtitleSegment[]; hasDraft:boolean;
  onAccept:(result:EditorOperationResponse) => void; onSeek:(time:number) => void;
}) {
  const [candidates,setCandidates] = useState<api.TranscriptionCandidate[]>([]);
  const [selected,setSelected] = useState('');
  const [rows,setRows] = useState<Array<{start:number;end:number;text:string}>>([]);
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [page,setPage] = useState(0);
  const [reload,setReload]=useState(0);
  useEffect(() => { let cancelled=false; setError(''); void api.listTranscriptionCandidates(projectId).then(result => { if (!cancelled) setCandidates(result.candidates); }).catch(error => { if (!cancelled) setError(error.message); }); return () => {cancelled=true;}; },[projectId,reload]);
  useEffect(() => { setRows([]); setPage(0); if (!selected) { setBusy(false); return; } let cancelled=false; setError(''); setBusy(true); void api.getTranscriptionCandidate(projectId,selected).then(result => { if (!cancelled) setRows(result.segments); }).catch(error => { if (!cancelled) setError(error.message); }).finally(() => {if (!cancelled) setBusy(false);}); return () => {cancelled=true;}; },[projectId,selected,reload]);
  const partial = ['cancelled','failed'].includes(candidates.find(item => item.id === selected)?.status || '');
  return <section className="candidate-review"><h3>转写候选结果</h3><p>重新转写保留原字幕。比较后替换，可通过撤销恢复人工修改。</p>
    {!candidates.length && <p>暂无待比较的结果。</p>}
    <select aria-label="转写候选结果" value={selected} onChange={event => setSelected(event.target.value)}><option value="">选择候选结果</option>{candidates.map(item => <option key={item.id} value={item.id}>{item.model}{['cancelled','failed'].includes(item.status || '') ? ' · 未完成草稿' : ''} · {item.segments_count} 条 · {item.finished_at}</option>)}</select>
    {error && <p role="alert">{error}<button onClick={() => setReload(value => value+1)}>重新加载候选</button></p>}
    {selected && <>{partial && <p role="note">这是取消或失败前保存的部分转写，未覆盖整段音频，也未完成字幕后处理。采用后请校对，可撤销。</p>}<p>当前字幕 {segments.length} 条 / 新结果 {rows.length} 条</p><div className="candidate-comparison">{rows.slice(page*30,(page+1)*30).map((row,index) => { const prior = segments.filter(item => item.start < row.end && item.end > row.start); return <div key={index}><button onClick={() => onSeek(row.start)}>{row.start.toFixed(2)} 秒</button><p><small>当前</small>{prior.map(item => item.clean_text || item.raw_text).join(' ') || '此时段没有字幕'}</p><p><small>候选</small>{row.text}</p></div>; })}</div><button disabled={page === 0} onClick={() => setPage(value => value-1)}>上一页</button><span> {page+1} / {Math.max(1,Math.ceil(rows.length/30))} </span><button disabled={(page+1)*30 >= rows.length} onClick={() => setPage(value => value+1)}>下一页</button><p>{hasDraft ? '请先保存或放弃当前编辑草稿，再替换。' : '替换会生成一条可撤销的编辑记录。'}</p><button className="button primary" disabled={busy || !rows.length || hasDraft} onClick={() => { setBusy(true); void api.acceptTranscriptionCandidate(projectId,selected,revision).then(result => { onAccept(result); setCandidates(current => current.filter(item => item.id !== selected)); setSelected(''); }).catch(error => setError(error.message)).finally(() => setBusy(false)); }}>使用候选结果替换当前字幕</button></>}
  </section>;
}
