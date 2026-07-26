import { useEffect, useMemo, useState } from 'react';
import * as api from '../api/backend';
import type { AppSettings, HealthStatus, PlaylistPreview } from '../types';

interface Props {
  url: string;
  workflow: { model: string; language: string; target_language: string; runtime?: string; clean_target_length: number };
  appSettings: AppSettings;
  health: HealthStatus | null;
  aiReady: boolean;
  onClose: () => void;
  onCreated: (message: string) => void;
}

function durationLabel(seconds: number) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours ? `${hours} 小时 ${minutes} 分钟` : `${minutes} 分钟`;
}

export default function PlaylistBatchDialog({ url, workflow, appSettings, health, aiReady, onClose, onCreated }: Props) {
  const [preview, setPreview] = useState<PlaylistPreview | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [transcribe, setTranscribe] = useState(true);
  const [clean, setClean] = useState(false);
  const [translate, setTranslate] = useState(false);
  const [aiAuthorized, setAiAuthorized] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    api.previewPlaylist(url).then(result => {
      if (!cancelled) setPreview(result);
    }).catch(reason => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [url]);

  const blockers = useMemo(() => {
    const values: string[] = [];
    if (!health?.runtime?.ffmpeg?.ok) values.push('FFmpeg 不可用');
    if (!health?.runtime?.yt_dlp?.ok) values.push('yt-dlp 不可用');
    if (transcribe && !workflow.runtime) values.push('尚未选择转写运行设备');
    if ((clean || translate) && !aiReady) values.push('AI 服务尚未配置可用的 API Key');
    if (translate && workflow.target_language === 'none') values.push('AI 翻译需要目标语言');
    if ((clean || translate) && !aiAuthorized) values.push('请确认 AI 内容授权');
    return values;
  }, [aiAuthorized, aiReady, clean, health, transcribe, translate, workflow.runtime, workflow.target_language]);

  async function create() {
    if (!preview || blockers.length) return;
    setBusy(true); setError('');
    try {
      const result = await api.createPlaylistBatch(url, {
        model: workflow.model,
        runtime: workflow.runtime,
        language: workflow.language,
        target_language: workflow.target_language,
        clean_target_length: workflow.clean_target_length,
        download_quality: appSettings.download_quality || 'best',
        download_container: appSettings.download_container || 'mp4',
        ai_authorized: aiAuthorized,
        stages: { transcribe, clean, translate },
      });
      onCreated(result.action === 'created'
        ? `已创建“${preview.playlist.title}”，${result.added_count} 个视频开始排队`
        : `已同步“${preview.playlist.title}”，新增 ${result.added_count} 个视频`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setBusy(false);
    }
  }

  return <div className="production-overlay playlist-dialog-overlay" role="dialog" aria-modal="true" aria-labelledby="playlist-dialog-title" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="playlist-dialog">
      <header><div><small>播放列表批量任务</small><h2 id="playlist-dialog-title">{preview?.playlist.title || '正在解析播放列表'}</h2><p>{preview ? `${preview.playlist.channel || 'YouTube'} · ${preview.playlist.item_count} 个视频 · ${durationLabel(preview.playlist.total_duration)}` : '正在读取标题、顺序和视频时长…'}</p></div><button aria-label="关闭" disabled={busy} onClick={onClose}>×</button></header>
      {busy && !preview && <div className="playlist-loading"><i/><span>正在解析播放列表元数据</span></div>}
      {error && <div className="playlist-error" role="alert">{error}</div>}
      {preview && <>
        <div className="playlist-summary">
          <span className="playlist-cover">{preview.playlist.thumbnail_url ? <img src={preview.playlist.thumbnail_url} alt=""/> : '▶'}</span>
          <div><strong>{preview.playlist.available_count} 个视频可下载</strong><small>画质 {appSettings.download_quality || 'best'} · 容器 {(appSettings.download_container || 'mp4').toUpperCase()}</small><small>{health?.runtime?.disk?.message || '启动前请确认磁盘空间充足'}</small></div>
        </div>
        <fieldset className="playlist-pipeline"><legend>批量流水线</legend>
          <label className="always"><input type="checkbox" checked disabled/><span><strong>下载并提取音频</strong><small>全部可用视频，按播放列表顺序归组</small></span></label>
          <label><input type="checkbox" checked={transcribe} onChange={event => { setTranscribe(event.target.checked); if (!event.target.checked) { setClean(false); setTranslate(false); } }}/><span><strong>语音转写</strong><small>{workflow.model} · {workflow.runtime || '未选择设备'}</small></span></label>
          <label><input type="checkbox" checked={clean} onChange={event => { setClean(event.target.checked); if (event.target.checked) setTranscribe(true); }}/><span><strong>AI 整理</strong><small>保守修正断句、标点和明确错词</small></span></label>
          <label><input type="checkbox" checked={translate} onChange={event => { setTranslate(event.target.checked); if (event.target.checked) setTranscribe(true); }}/><span><strong>AI 翻译</strong><small>目标语言：{workflow.target_language}</small></span></label>
        </fieldset>
        {(clean || translate) && <label className="playlist-ai-consent"><input type="checkbox" checked={aiAuthorized} onChange={event => setAiAuthorized(event.target.checked)}/><span><strong>确认批量调用 AI 服务</strong><small>所选 {preview.playlist.available_count} 个项目的字幕内容会发送给当前配置的 AI 服务，可能产生费用。</small></span></label>}
        <details className="playlist-preview-list"><summary>查看全部 {preview.items.length} 个条目</summary><ol>{preview.items.map(item => <li key={item.source_id} className={item.availability}><span>{item.position}</span><strong>{item.title}</strong><small>{item.availability === 'unavailable' ? '不可用' : durationLabel(item.duration)}</small></li>)}</ol></details>
        {preview.warnings.map(warning => <div className="playlist-warning" key={warning}>{warning}</div>)}
        {!!blockers.length && <div className="playlist-blockers">{blockers.map(value => <span key={value}>! {value}</span>)}</div>}
      </>}
      <footer><button className="button secondary" disabled={busy} onClick={onClose}>取消</button><button className="button primary" disabled={!preview || busy || !!blockers.length} onClick={() => void create()}>{busy && preview ? '正在创建…' : preview ? `创建并处理 ${preview.playlist.available_count} 个视频` : '等待解析'}</button></footer>
    </section>
  </div>;
}
