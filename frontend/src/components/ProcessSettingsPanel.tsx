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
import CleanStepPanel from './process/CleanStepPanel';
import DownloadStepPanel from './process/DownloadStepPanel';
import StepActions from './process/StepActions';
import { stepBlocker } from './process/stepHelpers';
import TranslateStepPanel from './process/TranslateStepPanel';
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
        <DownloadStepPanel
          activeProject={activeProject}
          activeProjectIsYoutube={activeProjectIsYoutube}
          hasLocalVideo={hasLocalVideo}
          isProcessing={isProcessing}
          health={health}
          youtubeUrl={youtubeUrl}
          onYoutubeUrlChange={onYoutubeUrlChange}
          onMediaSelectionChanged={onMediaSelectionChanged}
          onRetryDownload={onRetryDownload}
          onDownloadLocalCopy={onDownloadLocalCopy}
          onExtractAudio={onExtractAudio}
        />
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
        <CleanStepPanel
          hasSegments={hasSegments}
          isProcessing={isProcessing}
          taskStarting={taskStarting}
          config={config}
          aiProviderState={aiProviderState}
          cleanAIProvider={cleanAIProvider}
          cleanAIReady={cleanAIReady}
          failedCleanBatches={failedCleanBatches}
          onConfigChange={onConfigChange}
          onClean={onClean}
          onUndoClean={onUndoClean}
          onRetryFailedCleanBatch={onRetryFailedCleanBatch}
          onOpenSettings={onOpenSettings}
        />
      )}

      {activeProcessStep === 'translate' && (
        <TranslateStepPanel
          hasSegments={hasSegments}
          isProcessing={isProcessing}
          config={config}
          translateAIReady={translateAIReady}
          onConfigChange={onConfigChange}
          onUpdateTargetLanguage={onUpdateTargetLanguage}
          onTranslate={onTranslate}
          onOpenSettings={onOpenSettings}
        />
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
