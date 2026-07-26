import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../api/backend';

interface Props { projectId: string; onChanged: () => void; }

export default function MediaSelectionPanel({ projectId, onChanged }: Props) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['media-info', projectId], queryFn: () => api.getMediaInfo(projectId) });
  const [track, setTrack] = useState(0); const [start, setStart] = useState(''); const [end, setEnd] = useState('');
  const [previewUrl, setPreviewUrl] = useState(''); const [previewBusy, setPreviewBusy] = useState(false);
  useEffect(() => { if (!query.data) return; setTrack(query.data.selection.audio_track_index); setStart(query.data.selection.range_start?.toString() || ''); setEnd(query.data.selection.range_end?.toString() || ''); }, [query.data]);
  const save = useMutation({
    mutationFn: () => api.updateMediaSelection(projectId, { audio_track_index: track, range_start: start === '' ? null : Number(start), range_end: end === '' ? null : Number(end) }),
    onSuccess: async () => { await client.invalidateQueries({ queryKey: ['media-info', projectId] }); onChanged(); },
  });
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);
  const preview = async () => { setPreviewBusy(true); try { const url = await api.getMediaTrackPreview(projectId, track, Number(start) || 0); setPreviewUrl(current => { if (current) URL.revokeObjectURL(current); return url; }); } finally { setPreviewBusy(false); } };
  if (query.isError) return null;
  return <div className="media-selection-panel"><label>音轨<select value={track} onChange={event => { setTrack(Number(event.target.value)); setPreviewUrl(''); }}>{query.data?.audio_tracks.map(item => <option value={item.index} key={item.index}>{item.title} · {item.language} · {item.codec}</option>)}</select></label><div><label>入点（秒）<input type="number" min="0" step="0.1" value={start} placeholder="完整视频" onChange={event => setStart(event.target.value)}/></label><label>出点（秒）<input type="number" min="0" step="0.1" max={query.data?.duration || undefined} value={end} placeholder="完整视频" onChange={event => setEnd(event.target.value)}/></label></div><button className="button" disabled={previewBusy} onClick={() => void preview()}>{previewBusy ? '正在准备试听…' : '试听所选音轨 15 秒'}</button>{previewUrl && <audio src={previewUrl} controls autoPlay aria-label="所选音轨试听"/>}<button className="button secondary" disabled={save.isPending || (!!start && !!end && Number(end) <= Number(start))} onClick={() => save.mutate()}>保存音轨与范围</button>{save.isSuccess && <small>设置已保存，请重新提取音频。</small>}</div>;
}
