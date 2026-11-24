import type { HealthStatus, Project } from '../../types';
import MediaSelectionPanel from '../MediaSelectionPanel';

interface Props {
  activeProject: Project | null;
  activeProjectIsYoutube: boolean;
  hasLocalVideo: boolean;
  isProcessing: boolean;
  health: HealthStatus | null;
  youtubeUrl: string;
  onYoutubeUrlChange: (url: string) => void;
  onMediaSelectionChanged: (changed: boolean) => void;
  onRetryDownload: () => void;
  onDownloadLocalCopy: () => void;
  onExtractAudio: () => void;
}

export default function DownloadStepPanel({
  activeProject,
  activeProjectIsYoutube,
  hasLocalVideo,
  isProcessing,
  health,
  youtubeUrl,
  onYoutubeUrlChange,
  onMediaSelectionChanged,
  onRetryDownload,
  onDownloadLocalCopy,
  onExtractAudio,
}: Props) {
  return (
    <section className="inspector-section media-mode-inspector step-panel">
      <header className="step-heading">
        <h3>媒体与音轨</h3>
        <p>
          {activeProject?.source_type === 'local'
            ? '导入的视频只保存在这个项目里，可离线播放；需要时可以换音轨或只截取一段。'
            : '完整视频保存在本机，可离线播放；需要时可以换音轨或只截取一段。'}
        </p>
      </header>
      {activeProjectIsYoutube && (
        <label>
          项目链接
          <input
            value={activeProject?.source_url || youtubeUrl}
            placeholder="YouTube URL"
            onChange={event => onYoutubeUrlChange(event.target.value)}
          />
        </label>
      )}
      <div className="runtime-mini">
        <span className={health?.runtime?.ffmpeg?.ok ? 'ok' : 'error'}>
          FFmpeg {health?.runtime?.ffmpeg?.ok ? '可用' : '需检查'}
        </span>
        {activeProjectIsYoutube && (
          <span className={health?.runtime?.yt_dlp?.ok ? 'ok' : 'error'}>
            yt-dlp {health?.runtime?.yt_dlp?.ok ? '可用' : '需检查'}
          </span>
        )}
        {activeProjectIsYoutube && (
          <span className={health?.runtime?.deno?.ok && health?.runtime?.ejs?.ok ? 'ok' : 'error'}>
            YouTube 挑战组件 {health?.runtime?.deno?.ok && health?.runtime?.ejs?.ok ? '可用' : '需检查'}
          </span>
        )}
      </div>
      {activeProject?.video_path && (
        <MediaSelectionPanel projectId={activeProject.id} onChanged={onMediaSelectionChanged} />
      )}
      <div className="media-mode-actions">
        {activeProjectIsYoutube && (
          <button
            className="button primary"
            disabled={!activeProject?.source_url || isProcessing}
            onClick={onRetryDownload}
          >
            重新下载视频
          </button>
        )}
        {activeProjectIsYoutube && !hasLocalVideo && (
          <button
            className="button secondary"
            disabled={isProcessing}
            onClick={onDownloadLocalCopy}
          >
            下载本地副本
          </button>
        )}
        {activeProject?.video_path && (
          <button className="button secondary" disabled={isProcessing} onClick={onExtractAudio}>
            重新提取音频
          </button>
        )}
      </div>
    </section>
  );
}
