import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../api/backend';
import type { EditorOperationResponse } from '../types';

interface Props {
  projectId: string;
  revision: number;
  duration: number;
  onEditorResult: (result: EditorOperationResponse) => void;
  onProjectChanged: () => void;
}

const COLORS = ['#5b8cff', '#e46f91', '#55b98f', '#d99c48', '#9b78df', '#4da9c7'];

export default function SmartToolsPanel({ projectId, revision, duration, onEditorResult, onProjectChanged }: Props) {
  const client = useQueryClient();
  const speakers = useQuery({ queryKey: ['speakers', projectId], queryFn: () => api.getSpeakers(projectId) });
  const authorizations = useQuery({ queryKey: ['cloud-authorizations'], queryFn: api.getCloudAuthorizations });
  const managedModels = useQuery({ queryKey: ['speaker-models'], queryFn: api.getSpeakerModelStatus });
  const [speakerName, setSpeakerName] = useState('说话人');
  const [sourceSpeaker, setSourceSpeaker] = useState('');
  const [targetSpeaker, setTargetSpeaker] = useState('');
  const [assignSpeaker, setAssignSpeaker] = useState('');
  const [assignIndices, setAssignIndices] = useState('');
  const [segmentationModel, setSegmentationModel] = useState('');
  const [embeddingModel, setEmbeddingModel] = useState('');
  const [numSpeakers, setNumSpeakers] = useState('');
  const [taskId, setTaskId] = useState('');
  const [message, setMessage] = useState('');
  const [ocrRegion, setOcrRegion] = useState({ x: 0, y: 65, width: 100, height: 35 });
  const [ocrStart, setOcrStart] = useState(0);
  const [ocrEnd, setOcrEnd] = useState(Math.max(1, duration || 60));
  const [ocrInterval, setOcrInterval] = useState(.5);
  const [ocrCues, setOcrCues] = useState<api.OCRCue[]>([]);
  const handledTask = useRef('');
  const task = useQuery({
    queryKey: ['smart-task', taskId],
    queryFn: () => api.getTaskStatus(taskId),
    enabled: Boolean(taskId),
    refetchInterval: query => ['success', 'failed', 'cancelled'].includes(query.state.data?.status || '') ? false : 800,
  });

  useEffect(() => {
    if (!task.data) return;
    if (task.data.status === 'success') {
      if (handledTask.current === task.data.id) return;
      handledTask.current = task.data.id;
      const preview = task.data.details?.ocr_preview;
      if (Array.isArray(preview)) setOcrCues(preview as api.OCRCue[]);
      if (task.data.type === 'speaker_diarization') {
        void client.invalidateQueries({ queryKey: ['speakers', projectId] });
        onProjectChanged();
      }
    }
  }, [client, onProjectChanged, projectId, task.data]);

  useEffect(() => {
    if (duration > 0) setOcrEnd(duration);
  }, [duration]);
  useEffect(() => {
    if (!managedModels.data?.ready) return;
    if (managedModels.data.segmentation_model) setSegmentationModel(managedModels.data.segmentation_model);
    if (managedModels.data.embedding_model) setEmbeddingModel(managedModels.data.embedding_model);
  }, [managedModels.data]);

  const list = speakers.data?.speakers || [];
  const canMerge = sourceSpeaker && targetSpeaker && sourceSpeaker !== targetSpeaker;
  const averageConfidence = useMemo(() => ocrCues.length
    ? ocrCues.reduce((sum, cue) => sum + Number(cue.confidence || 0), 0) / ocrCues.length
    : 0, [ocrCues]);

  const createMutation = useMutation({
    mutationFn: () => api.createSpeaker(projectId, speakerName.trim(), COLORS[list.length % COLORS.length]),
    onSuccess: () => { setSpeakerName('说话人'); void client.invalidateQueries({ queryKey: ['speakers', projectId] }); },
  });

  async function chooseModel(kind: 'segmentation' | 'embedding') {
    try {
      const { open } = await import('@tauri-apps/plugin-dialog');
      const value = await open({ multiple: false, directory: false, title: kind === 'segmentation' ? '选择说话人分割模型' : '选择说话人嵌入模型', filters: [{ name: 'ONNX 模型', extensions: ['onnx'] }] });
      if (typeof value === 'string') (kind === 'segmentation' ? setSegmentationModel : setEmbeddingModel)(value);
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }

  async function startDiarization() {
    try {
      setMessage('');
      const result = await api.startSpeakerDiarization(projectId, segmentationModel, embeddingModel, Number(numSpeakers) || undefined);
      setTaskId(result.task_id);
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }

  async function prepareManagedModels() {
    try {
      const created = await api.prepareSpeakerModels();
      if (created.ready) { await managedModels.refetch(); return; }
      if (!created.task_id) return;
      setTaskId(created.task_id);
      const poll = async () => {
        const current = await api.getTaskStatus(created.task_id!);
        if (current.status === 'success') { await managedModels.refetch(); return; }
        if (['failed','cancelled'].includes(current.status)) { setMessage(current.error || current.message); return; }
        window.setTimeout(() => void poll(), 800);
      };
      void poll();
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }

  async function startOCR() {
    try {
      setMessage(''); setOcrCues([]);
      const result = await api.startOCR(projectId, {
        region: { x: ocrRegion.x / 100, y: ocrRegion.y / 100, width: ocrRegion.width / 100, height: ocrRegion.height / 100 },
        start: ocrStart, end: ocrEnd, interval: ocrInterval,
      });
      setTaskId(result.task_id);
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }

  async function commitOCR() {
    if (!ocrCues.length || !window.confirm(`将 ${ocrCues.length} 条 OCR 结果作为新字幕轨导入？当前字幕可通过撤销恢复。`)) return;
    try {
      const result = await api.commitOCR(projectId, revision, ocrCues);
      onEditorResult(result); setOcrCues([]); setMessage('OCR 字幕已导入，可随时撤销');
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }

  return <div className="smart-tools-grid">
    <section className="smart-tool-card" aria-labelledby="speaker-tools-title">
      <header><div><small>本地离线</small><h2 id="speaker-tools-title">说话人识别</h2></div><span>{list.length} 位说话人</span></header>
      <div className="speaker-list">
        {list.map(speaker => <div className="speaker-row" key={speaker.id}>
          <input type="color" value={speaker.color} aria-label={`${speaker.name}颜色`} onChange={event => void api.updateSpeaker(projectId, speaker.id, speaker.name, event.target.value).then(() => client.invalidateQueries({ queryKey: ['speakers', projectId] }))}/>
          <input defaultValue={speaker.name} aria-label="说话人名称" onBlur={event => { const name = event.target.value.trim(); if (name && name !== speaker.name) void api.updateSpeaker(projectId, speaker.id, name, speaker.color).then(() => { void client.invalidateQueries({ queryKey: ['speakers', projectId] }); onProjectChanged(); }); }}/>
        </div>)}
        {!list.length && <p>尚未识别或创建说话人。</p>}
      </div>
      <div className="inline-form"><input value={speakerName} onChange={event => setSpeakerName(event.target.value)} aria-label="新说话人名称"/><button onClick={() => createMutation.mutate()} disabled={!speakerName.trim() || createMutation.isPending}>添加</button></div>
      {list.length > 1 && <div className="speaker-merge"><select value={sourceSpeaker} onChange={event => setSourceSpeaker(event.target.value)} aria-label="要合并的说话人"><option value="">选择来源</option>{list.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select><span>→</span><select value={targetSpeaker} onChange={event => setTargetSpeaker(event.target.value)} aria-label="合并到"><option value="">选择目标</option>{list.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select><button disabled={!canMerge} onClick={() => void api.mergeSpeakers(projectId, sourceSpeaker, targetSpeaker).then(() => { setSourceSpeaker(''); setTargetSpeaker(''); void client.invalidateQueries({ queryKey: ['speakers', projectId] }); onProjectChanged(); })}>合并</button></div>}
      {list.length > 0 && <div className="speaker-assign"><input value={assignIndices} onChange={event => setAssignIndices(event.target.value)} placeholder="字幕序号，如 1,2,5-8" aria-label="要指定的字幕序号"/><select value={assignSpeaker} onChange={event => setAssignSpeaker(event.target.value)} aria-label="指定说话人"><option value="">选择说话人</option>{list.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select><button disabled={!assignSpeaker || !assignIndices.trim()} onClick={() => { const indices = new Set<number>(); for (const part of assignIndices.split(',')) { const [a,b] = part.trim().split('-').map(Number); if (Number.isInteger(a) && Number.isInteger(b)) for (let value=a; value<=b; value+=1) indices.add(value); else if (Number.isInteger(a)) indices.add(a); } void api.applySegmentOperation(projectId, { expected_revision: revision, operation: 'assign_speaker', indices: [...indices], speaker_id: assignSpeaker }).then(result => { onEditorResult(result); setAssignIndices(''); }); }}>批量指定</button></div>}
      <div className="managed-model-card"><span><strong>{managedModels.data?.ready ? '托管模型已就绪' : '首次使用需准备离线模型'}</strong><small>来自 sherpa-onnx 官方模型发布；支持中断后继续下载。</small></span><button disabled={managedModels.data?.ready || task.data?.status === 'running'} onClick={() => void prepareManagedModels()}>{managedModels.data?.ready ? '已准备' : '下载并准备'}</button></div><details><summary>使用自定义模型文件</summary><div className="model-file-grid"><label>分割模型<input value={segmentationModel} onChange={event => setSegmentationModel(event.target.value)}/><button onClick={() => void chooseModel('segmentation')}>选择…</button></label><label>嵌入模型<input value={embeddingModel} onChange={event => setEmbeddingModel(event.target.value)}/><button onClick={() => void chooseModel('embedding')}>选择…</button></label></div></details><label className="speaker-count-field">说话人数（可选）<input type="number" min="1" max="20" value={numSpeakers} onChange={event => setNumSpeakers(event.target.value)}/></label>
      <button className="button primary" disabled={!segmentationModel || !embeddingModel || task.data?.status === 'running'} onClick={() => void startDiarization()}>开始本地识别</button>
    </section>

    <section className="smart-tool-card" aria-labelledby="ocr-tools-title">
      <header><div><small>macOS Vision · 预览后导入</small><h2 id="ocr-tools-title">硬字幕 OCR</h2></div><span>{ocrCues.length ? `${ocrCues.length} 条预览` : '不会覆盖字幕'}</span></header>
      <div className="ocr-region-grid">{(['x', 'y', 'width', 'height'] as const).map(key => <label key={key}>{({x:'左',y:'上',width:'宽',height:'高'} as const)[key]}（%）<input type="number" min="0" max="100" value={ocrRegion[key]} onChange={event => setOcrRegion(current => ({ ...current, [key]: Number(event.target.value) }))}/></label>)}</div>
      <div className="ocr-region-preview"><span style={{ left: `${ocrRegion.x}%`, top: `${ocrRegion.y}%`, width: `${ocrRegion.width}%`, height: `${ocrRegion.height}%` }}>字幕识别区域</span></div>
      <div className="ocr-time-grid"><label>入点（秒）<input type="number" min="0" step="0.1" value={ocrStart} onChange={event => setOcrStart(Number(event.target.value))}/></label><label>出点（秒）<input type="number" min="0.1" step="0.1" value={ocrEnd} onChange={event => setOcrEnd(Number(event.target.value))}/></label><label>采样间隔<input type="number" min="0.2" max="3" step="0.1" value={ocrInterval} onChange={event => setOcrInterval(Number(event.target.value))}/></label></div>
      <button className="button primary" disabled={ocrEnd <= ocrStart || task.data?.status === 'running'} onClick={() => void startOCR()}>生成 OCR 预览</button>
      {ocrCues.length > 0 && <div className="ocr-preview-list"><header><strong>识别预览</strong><small>平均置信度 {Math.round(averageConfidence * 100)}%</small></header>{ocrCues.slice(0, 100).map((cue, index) => <div key={`${cue.start}-${index}`}><time>{cue.start.toFixed(2)}–{cue.end.toFixed(2)}</time><span>{cue.text}</span><em>{Math.round(Number(cue.confidence || 0) * 100)}%</em></div>)}</div>}
      {ocrCues.length > 0 && <button className="button" onClick={() => void commitOCR()}>确认导入为新字幕轨</button>}
    </section>
    <section className="cloud-consent-card"><header><div><small>默认关闭</small><h3>云端增强授权</h3></div><p>本地能力不会读取这些授权。开启后，也只有主动使用对应云端增强操作时才会上传所说明的范围。</p></header><div>{(['ocr','speaker','quality'] as const).map(capability => { const record = authorizations.data?.authorizations.find(item => item.capability === capability); const granted = Boolean(record?.granted); const label = capability === 'ocr' ? 'OCR' : capability === 'speaker' ? '说话人增强' : 'AI 质检'; return <label key={capability}><span><strong>{label}</strong><small>{granted ? `已授权 · ${record?.granted_at || ''}` : '仅本地运行'}</small></span><input type="checkbox" checked={granted} onChange={event => { const next = event.target.checked; if (next && !window.confirm(`启用${label}云端授权后，只有在你主动选择云端增强时才会上传相关${capability === 'speaker' ? '音频片段' : '内容'}。继续吗？`)) return; void api.setCloudAuthorization(capability, next).then(() => client.invalidateQueries({ queryKey: ['cloud-authorizations'] })); }}/></label>; })}</div></section>
    {(message || task.data) && <aside className={`smart-task-status ${task.data?.status || ''}`} role="status"><strong>{task.data?.message || message}</strong>{task.data && <span>{Math.round(task.data.progress || 0)}%</span>}{task.data?.error && <small>{task.data.error}</small>}</aside>}
  </div>;
}
