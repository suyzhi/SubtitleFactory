import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../api/backend';

interface Props { projectId: string; onChanged: (changed:boolean) => void; }

export default function MediaSelectionPanel({ projectId, onChanged }: Props) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['media-info', projectId], queryFn: () => api.getMediaInfo(projectId) });
  const [track, setTrack] = useState(0); const [start, setStart] = useState(''); const [end, setEnd] = useState('');
  const [error,setError] = useState('');
  const [previewUrl, setPreviewUrl] = useState(''); const [previewBusy, setPreviewBusy] = useState(false);
  useEffect(() => { if (!query.data) return; setTrack(query.data.selection.audio_track_index); setStart(query.data.selection.range_start?.toString() || ''); setEnd(query.data.selection.range_end?.toString() || ''); }, [query.data]);
  const save = useMutation({
    mutationFn: () => api.updateMediaSelection(projectId, { audio_track_index: track, range_start: start === '' ? null : Number(start), range_end: end === '' ? null : Number(end) }),
    onSuccess: async result => { await client.invalidateQueries({ queryKey: ['media-info', projectId] }); onChanged(result.audio_reextract_required); },
  });
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);
  const preview = async () => { setPreviewBusy(true); try { const url = await api.getMediaTrackPreview(projectId, track, Number(start) || 0); setPreviewUrl(current => { if (current) URL.revokeObjectURL(current); return url; }); } catch(error) {setError(error instanceof Error ? error.message : '试听失败');} finally { setPreviewBusy(false); } };
  if (query.isError) return <p role="alert">无法读取素材音轨。<button onClick={() => void query.refetch()}>重新加载</button></p>;
  if (query.isLoading) return <p role="status">正在读取音轨与范围…</p>;
  return <div className="media-selection-panel"><label>音轨<select value={track} onChange={event => { setTrack(Number(event.target.value)); setPreviewUrl(''); }}>{query.data?.audio_tracks.map(item => <option value={item.index} key={item.index}>{item.title} · {item.language} · {item.codec}</option>)}</select></label><div><label>入点（秒）<input type="number" min="0" step="0.1" value={start} placeholder="完整视频" onChange={event => setStart(event.target.value)}/></label><label>出点（秒）<input type="number" min="0" step="0.1" max={query.data?.duration || undefined} value={end} placeholder="完整视频" onChange={event => setEnd(event.target.value)}/></label></div><button className="button" disabled={previewBusy} onClick={() => void preview()}>{previewBusy ? '正在准备试听…' : '试听所选音轨 15 秒'}</button>{previewUrl && <audio src={previewUrl} controls autoPlay aria-label="所选音轨试听"/>}<p>修改音轨或截取范围后，音频需重新准备，转写会生成新的候选字幕。原视频、现有字幕、译文和编辑历史均保留；采用新候选后，请重新检查译文和时间边界。</p>{(error || save.error) && <p role="alert">{error || String(save.error)}</p>}<button className="button secondary" disabled={save.isPending || (!!start && !!end && Number(end) <= Number(start))} onClick={() => save.mutate()}>保存音轨与范围</button>{save.isSuccess && <small>设置已保存；下次生成字幕会自动校验并准备所需音频。</small>}</div>;
}
