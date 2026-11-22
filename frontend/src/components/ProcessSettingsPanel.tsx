import type { ReactNode } from 'react';
import type * as api from '../api/backend';
import type {
  EditorOperationResponse,
  FailedCleanBatch,
  HealthStatus,
  ProcessingConfig,
  Project,
  SubtitleSegment,
  TaskStatus,
} from '../types';
import LanguagePicker from './LanguagePicker';
import MediaSelectionPanel from './MediaSelectionPanel';
import TranscriptionCandidates from './TranscriptionCandidates';

interface Props {
  activeProcessStep: string;
  activeProject: Project | null;
  activeProjectIsYoutube: boolean;
  hasAudio: boolean;
  hasSegments: boolean;
  hasLocalVideo: boolean;
  isProcessing: boolean;
  taskStarting: boolean;
  currentTask: TaskStatus | null;
  recoveryActionLabel: string;
  config: ProcessingConfig;
  health: HealthStatus | null;
  youtubeUrl: string;
  aiProviderState: api.AIProvidersResponse | null;
  cleanAIProvider?: api.AIProviderCard;
  cleanAIReady: boolean;
  translateAIReady: boolean;
  failedCleanBatches: FailedCleanBatch[];
  segments: SubtitleSegment[];
  editorRevision: number;
  hasDraft: boolean;
  inspectorRuntime: string;
  quickImport: string;
  transcriptionSetup: ReactNode;
  flowOptionsPanel: ReactNode;
  onYoutubeUrlChange: (url: string) => void;
  onConfigChange: (updater: (current: ProcessingConfig) => ProcessingConfig) => void;
  onMediaSelectionChanged: (changed: boolean) => void;
  onRetryDownload: () => void;
  onDownloadLocalCopy: () => void;
  onExtractAudio: () => void;
  onTranscribe: () => void;
  onGenerateSubtitles: () => void;
  onResetQuickImport: () => void;
  onClean: () => void;
  onUndoClean: () => void;
  onRetryFailedCleanBatch: (batchIndex: number) => void;
  onUpdateTargetLanguage: (lang: string) => void;
  onTranslate: () => void;
  onGoToExport: () => void;
  onOpenSettings: () => void;
  onRetryCurrentFailure: () => void;
  onSeek: (time: number) => void;
  onAcceptCandidate: (result: EditorOperationResponse) => void;
}

function stepBlocker(...conditions: Array<[unknown, string]>): string {
  return conditions.find(([blocked]) => Boolean(blocked))?.[1] || '';
}

function StepActions({
  primary,
  blocker,
  extra,
}: {
  primary: ReactNode;
  blocker: string;
  extra?: ReactNode;
}) {
  return (
    <footer className="step-actions">
      {blocker && <p className="step-blocker">{blocker}</p>}
      <div>
        {primary}
        {extra}
      </div>
    </footer>
  );
}

export default function ProcessSettingsPanel({
  activeProcessStep,
  activeProject,
  activeProjectIsYoutube,
  hasAudio,
  hasSegments,
  hasLocalVideo,
  isProcessing,
  taskStarting,
  currentTask,
  recoveryActionLabel,
  config,
  health,
  youtubeUrl,
  aiProviderState,
  cleanAIProvider,
  cleanAIReady,
  translateAIReady,
  failedCleanBatches,
  segments,
  editorRevision,
  hasDraft,
  inspectorRuntime,
  quickImport,
  transcriptionSetup,
  flowOptionsPanel,
  onYoutubeUrlChange,
  onConfigChange,
  onMediaSelectionChanged,
  onRetryDownload,
  onDownloadLocalCopy,
  onExtractAudio,
  onTranscribe,
  onGenerateSubtitles,
  onResetQuickImport,
  onClean,
  onUndoClean,
  onRetryFailedCleanBatch,
  onUpdateTargetLanguage,
  onTranslate,
  onGoToExport,
  onOpenSettings,
  onRetryCurrentFailure,
  onSeek,
  onAcceptCandidate,
}: Props) {
  return (
    <div className="process-settings-content">
      {activeProcessStep === 'download' && (
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
      )}

      {activeProcessStep === 'transcribe' && (
        <section className="inspector-section step-panel">
          <header className="step-heading">
            <h3>{hasSegments ? '重新转写' : '生成字幕'}</h3>
            <p>
              {hasSegments
                ? '重新转写会生成候选结果，当前人工字幕会保留。'
                : '在本机把语音转成带时间轴的字幕，完成后结果会出现在编辑页。'}
            </p>
          </header>
          {transcriptionSetup}
          {flowOptionsPanel}
          {activeProject && (
            <TranscriptionCandidates
              key={`${activeProject.id}-${currentTask?.status}-${currentTask?.details?.worker_stopped}`}
              projectId={activeProject.id}
              revision={editorRevision}
              segments={segments}
              hasDraft={hasDraft}
              onSeek={onSeek}
              onAccept={onAcceptCandidate}
            />
          )}
          <StepActions
            primary={
              <button
                className="button primary"
                disabled={isProcessing || !inspectorRuntime}
                onClick={
                  hasAudio && !config.enable_clean && !config.enable_translate
                    ? onTranscribe
                    : onGenerateSubtitles
                }
              >
                {hasSegments ? '开始重新转写' : '开始转写'}
              </button>
            }
            blocker={stepBlocker(
              [isProcessing, '有任务正在运行，完成或停止后再开始'],
              [!inspectorRuntime, '先在上方选择运行设备'],
            )}
            extra={
              quickImport ? (
                <button type="button" className="step-link" onClick={onResetQuickImport}>
                  下次导入时重新确认配置
                </button>
              ) : null
            }
          />
        </section>
      )}

      {activeProcessStep === 'clean' && (
        <section className="inspector-section step-panel">
          <header className="step-heading">
            <h3>AI 忠实整理</h3>
            <p>只修正明显的错词、标点和断句，不改变原意；可随时撤销。</p>
          </header>
          <div className="ai-summary-row">
            <span className="ai-logo">✦</span>
            <div>
              <strong>
                {cleanAIProvider?.name || (aiProviderState ? '未配置 AI 服务' : '正在读取 AI 服务')}
              </strong>
              <small>{cleanAIProvider?.model || '在设置 → AI 服务中配置'}</small>
            </div>
          </div>
          <label className="clean-length-field">
            参考单句长度 <span>{config.clean_target_length} 字</span>
            <input
              type="range"
              min={16}
              max={100}
              step={2}
              value={config.clean_target_length}
              onChange={event =>
                onConfigChange(current => ({
                  ...current,
                  clean_target_length: Number(event.target.value),
                }))
              }
            />
          </label>
          <StepActions
            primary={
              <button
                className="button primary"
                disabled={!hasSegments || isProcessing || !cleanAIReady}
                onClick={onClean}
              >
                开始整理
              </button>
            }
            blocker={stepBlocker(
              [!hasSegments, '先生成或导入字幕'],
              [isProcessing, '有任务正在运行，完成或停止后再开始'],
              [!cleanAIReady, '尚未配置用于整理的 AI 服务或 API 密钥'],
            )}
            extra={
              <>
                {!cleanAIReady && (
                  <button type="button" className="button secondary" onClick={onOpenSettings}>
                    配置 AI 服务
                  </button>
                )}
                <button
                  type="button"
                  className="step-link"
                  disabled={!hasSegments || isProcessing}
                  onClick={onUndoClean}
                >
                  撤销上次整理
                </button>
              </>
            }
          />
        </section>
      )}

      {activeProcessStep === 'clean' && failedCleanBatches.length > 0 && (
        <section className="failed-batch-recovery" aria-label="失败批次恢复">
          <header>
            <div>
              <strong>有 {failedCleanBatches.length} 个批次需要重试</strong>
              <small>只会重新整理对应时间范围，不会重跑整份字幕。</small>
            </div>
          </header>
          <div className="failed-batch-list">
            {failedCleanBatches.map(batch => (
              <article key={batch.batch_index}>
                <div>
                  <strong>
                    第 {batch.batch_index} 批 · {batch.segment_count} 条
                  </strong>
                  <small>
                    {batch.start === null || batch.end === null
                      ? '时间范围不可用'
                      : `${batch.start.toFixed(1)}s – ${batch.end.toFixed(1)}s`}{' '}
                    · 已尝试 {batch.attempts} 次
                  </small>
                  <p title={batch.error}>{batch.error || 'AI 未返回有效 JSON'}</p>
                </div>
                <button
                  type="button"
                  disabled={taskStarting || isProcessing}
                  onClick={() => onRetryFailedCleanBatch(batch.batch_index)}
                >
                  只重试这一批
                </button>
              </article>
            ))}
          </div>
        </section>
      )}

      {activeProcessStep === 'translate' && (
        <section className="inspector-section step-panel">
          <header className="step-heading">
            <h3>AI 翻译</h3>
            <p>译文单独保存，不覆盖原文，之后可以继续逐句校对。</p>
          </header>
          <label>
            目标语言
            <LanguagePicker
              mode="target"
              allowCustom
              allowNone
              value={config.target_language}
              onChange={onUpdateTargetLanguage}
            />
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={config.bilingual}
              onChange={event =>
                onConfigChange(current => ({ ...current, bilingual: event.target.checked }))
              }
            />{' '}
            导出时包含原文与译文
          </label>
          <StepActions
            primary={
              <button
                className="button primary"
                disabled={
                  !hasSegments ||
                  isProcessing ||
                  !translateAIReady ||
                  config.target_language === 'none'
                }
                onClick={onTranslate}
              >
                开始翻译
              </button>
            }
            blocker={stepBlocker(
              [!hasSegments, '先生成或导入字幕'],
              [isProcessing, '有任务正在运行，完成或停止后再开始'],
              [config.target_language === 'none', '先选择目标语言'],
              [!translateAIReady, '尚未配置用于翻译的 AI 服务或 API 密钥'],
            )}
            extra={
              !translateAIReady ? (
                <button type="button" className="button secondary" onClick={onOpenSettings}>
                  配置 AI 服务
                </button>
              ) : null
            }
          />
        </section>
      )}

      {activeProcessStep === 'export' && (
        <section className="inspector-section step-panel">
          <header className="step-heading">
            <h3>导出</h3>
            <p>字幕文件立即生成；带字幕视频在本机后台压制，不上传媒体。</p>
          </header>
          <StepActions
            primary={
              <button className="button primary" onClick={onGoToExport}>
                前往导出页
              </button>
            }
            blocker={stepBlocker([!hasSegments, '还没有字幕，导出前先生成或导入字幕'])}
          />
        </section>
      )}

      {currentTask?.status === 'failed' && (
        <section className="recovery-card">
          <strong>{currentTask.error_code || '任务失败'}</strong>
          <span>{currentTask.error || currentTask.message}</span>
          {currentTask.suggestion && <small>{currentTask.suggestion}</small>}
          <small>尝试次数：{currentTask.attempt || 1}</small>
          {currentTask.recoverable && (
            <button onClick={onRetryCurrentFailure}>{recoveryActionLabel}</button>
          )}
          {currentTask.available_actions?.includes('open_settings') && (
            <button onClick={onOpenSettings}>打开下载与存储设置</button>
          )}
        </section>
      )}
    </div>
  );
}
