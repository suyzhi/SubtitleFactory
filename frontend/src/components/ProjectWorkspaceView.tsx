import { lazy, Suspense, type ReactNode, type RefObject } from 'react';
import * as api from '../api/backend';
import { taskProgressLabel } from '../projectState';
import type {
  ExportFormat,
  ProcessLogEntry,
  ProcessStep,
  Project,
  SegmentUpdate,
  SubtitleSegment,
  SubtitleStyleSettings,
  TaskStatus,
} from '../types';
import DeferredPanel from './DeferredPanel';
import EditorWorkbench from './EditorWorkbench';
import ExportWorkspace from './ExportWorkspace';
import ProcessLogViewer from './ProcessLogViewer';
import ProcessTimeline from './ProcessTimeline';
import StyleTemplateBar from './StyleTemplateBar';
import type { PlayerPresentationMode, SubtitlePlayerHandle } from './SubtitlePlayer';
import SubtitleStylePanel from './SubtitleStylePanel';
import SubtitleTable from './SubtitleTable';
import SubtitleTimeline from './SubtitleTimeline';
import TaskWorkspace from './TaskWorkspace';
import WorkspacePanel from './WorkspacePanel';

const SubtitlePlayer = lazy(() => import('./SubtitlePlayer'));

interface Props {
  inProjectView: boolean;
  activeProject: Project | null;
  projectWorkspace: 'subtitles' | 'style' | 'export';
  subtitleFocus: boolean;
  workspaceTabsRef: RefObject<HTMLDivElement | null>;
  videoPlayerRef: RefObject<SubtitlePlayerHandle | null>;
  youtubeEnabled: boolean;
  canPlayMedia: boolean;
  hasLocalVideo: boolean;
  hasSegments: boolean;
  isProcessing: boolean;
  taskStarting: boolean;
  editorBusy: boolean;
  segments: SubtitleSegment[];
  segmentsLoading: boolean;
  segmentsError: string;
  currentTime: number;
  videoDuration: number;
  activeSegmentIdx: number;
  activeSegmentIndex: number;
  subtitleEntryFocusIndex: number;
  subtitleFocusRequest: number;
  autoScrollTable: boolean;
  editorSaveState: 'idle' | 'saving' | 'saved' | 'error';
  draftCount: number;
  draftIsStale: boolean;
  segmentInspector: number | null;
  subtitleStyle: SubtitleStyleSettings;
  presentationMode: PlayerPresentationMode;
  bilingual: boolean;
  toolsOpen: boolean;
  activeTaskCount: number;
  currentTask: TaskStatus | null;
  compactSteps: Array<{ id: string; label: string; icon: string; state: ProcessStep }>;
  activeProcessStep: string;
  processSteps: ProcessStep[];
  totalProgress: number;
  processLogs: ProcessLogEntry[];
  processSettingsNode: ReactNode;
  onSelectWorkspace: (workspace: 'subtitles' | 'style' | 'export') => void;
  onOpenTools: () => void;
  onCloseTools: () => void;
  onOpenTaskDrawer: () => void;
  onOpenWorkflowStep: (stepId: string) => void;
  onImportSubtitleFile: () => void;
  onImportLocal: () => void;
  onOpenLinkPopover: () => void;
  onTimeUpdate: (time: number) => void;
  onDurationChange: (duration: number) => void;
  onStyleChange: (style: SubtitleStyleSettings) => void;
  onPresentationModeChange: (mode: PlayerPresentationMode) => void;
  onRefreshSegments: (projectId: string) => void;
  onSeek: (time: number) => void;
  onInspectSegment: (index: number | null) => void;
  onUpdateSegment: (index: number, data: SegmentUpdate) => Promise<void>;
  onReplaceAll: (
    search: string,
    replacement: string,
    fields: Array<'clean_text' | 'translated_text'>,
    options: { matchCase: boolean; includeLocked: boolean },
  ) => Promise<void>;
  onSplit: (index: number, splitAt: number) => Promise<void>;
  onMerge: (indices: number[]) => Promise<void>;
  onDelete: (indices: number[]) => Promise<void>;
  onUndo: () => Promise<void>;
  onRedo: () => Promise<void>;
  onPreviewDraft: () => void;
  onCommitDraft: () => void;
  onDiscardDraft: () => void;
  onAutoScrollChange: (enabled: boolean) => void;
  onBilingualChange: (bilingual: boolean) => void;
  onExport: (format: ExportFormat) => void;
  onSelectStep: (stepId: string) => void;
  onPauseTask: () => void;
  onCancelTask: () => void;
  onClearLogs: () => void;
}

export default function ProjectWorkspaceView({
  inProjectView,
  activeProject,
  projectWorkspace,
  subtitleFocus,
  workspaceTabsRef,
  videoPlayerRef,
  youtubeEnabled,
  canPlayMedia,
  hasLocalVideo,
  hasSegments,
  isProcessing,
  taskStarting,
  editorBusy,
  segments,
  segmentsLoading,
  segmentsError,
  currentTime,
  videoDuration,
  activeSegmentIdx,
  activeSegmentIndex,
  subtitleEntryFocusIndex,
  subtitleFocusRequest,
  autoScrollTable,
  editorSaveState,
  draftCount,
  draftIsStale,
  segmentInspector,
  subtitleStyle,
  presentationMode,
  bilingual,
  toolsOpen,
  activeTaskCount,
  currentTask,
  compactSteps,
  activeProcessStep,
  processSteps,
  totalProgress,
  processLogs,
  processSettingsNode,
  onSelectWorkspace,
  onOpenTools,
  onCloseTools,
  onOpenTaskDrawer,
  onOpenWorkflowStep,
  onImportSubtitleFile,
  onImportLocal,
  onOpenLinkPopover,
  onTimeUpdate,
  onDurationChange,
  onStyleChange,
  onPresentationModeChange,
  onRefreshSegments,
  onSeek,
  onInspectSegment,
  onUpdateSegment,
  onReplaceAll,
  onSplit,
  onMerge,
  onDelete,
  onUndo,
  onRedo,
  onPreviewDraft,
  onCommitDraft,
  onDiscardDraft,
  onAutoScrollChange,
  onBilingualChange,
  onExport,
  onSelectStep,
  onPauseTask,
  onCancelTask,
  onClearLogs,
}: Props) {
  const inspectedSegment =
    segmentInspector !== null
      ? segments.find(segment => segment.index === segmentInspector) || null
      : null;

  return (
    <main
      className={`editor-workspace ${projectWorkspace === 'subtitles' ? 'workspace-editor' : ''} ${subtitleFocus ? 'subtitle-focus' : ''}`}
    >
      {/* The workspace is hidden behind the library; rendering it there only slows every library update. */}
      {inProjectView && (
        <>
          <nav className="project-workspace-nav" aria-label="项目工作区">
            <div ref={workspaceTabsRef} className="workspace-segments sliding-tabs">
              {(
                [
                  ['subtitles', '编辑', `${segments.length} 条字幕`],
                  ['style', '样式', '外观与位置'],
                  ['export', '导出', '文件与视频'],
                ] as const
              ).map(([id, label, detail]) => (
                <button
                  key={id}
                  className={projectWorkspace === id ? 'active' : ''}
                  aria-current={projectWorkspace === id ? 'page' : undefined}
                  onClick={() => onSelectWorkspace(id)}
                >
                  <span>{label}</span>
                  <small>{detail}</small>
                </button>
              ))}
            </div>
            <button
              className={`workspace-tools-button ${toolsOpen ? 'open' : ''} ${isProcessing ? 'busy' : ''}`}
              aria-expanded={toolsOpen}
              onClick={onOpenTools}
            >
              <span>任务与流程</span>
              <small>{currentTask ? currentTask.message || '运行中' : '转写、整理、翻译与日志'}</small>
            </button>
          </nav>

          <header className="workspace-page-heading">
            <div>
              <small>{activeProject?.title}</small>
              <h1>
                {projectWorkspace === 'subtitles'
                  ? '字幕编辑'
                  : projectWorkspace === 'style'
                    ? '字幕样式'
                    : '导出交付'}
              </h1>
            </div>
            {isProcessing && (
              <div className="page-task-status">
                <i />
                <span>{currentTask ? taskProgressLabel(currentTask) : '正在处理'}</span>
              </div>
            )}
          </header>

          <div className="workbench-split">
            {projectWorkspace === 'subtitles' && (
              <EditorWorkbench
                toolbar={
                  <>
                    <button
                      className="button primary"
                      onClick={() => onOpenWorkflowStep('transcribe')}
                    >
                      {hasSegments ? '重新转写' : '生成字幕'}
                    </button>
                    <button className="button secondary" onClick={onImportSubtitleFile}>
                      导入字幕
                    </button>
                    <button
                      className="button secondary"
                      onClick={() => onOpenWorkflowStep('clean')}
                    >
                      整理
                    </button>
                    <button
                      className="button secondary"
                      onClick={() => onOpenWorkflowStep('translate')}
                    >
                      翻译
                    </button>
                    {segmentsLoading && (
                      <span className="editor-save-status" role="status">
                        正在加载字幕…
                      </span>
                    )}
                  </>
                }
                player={
                  activeProject && canPlayMedia && activeProject.video_url ? (
                    <Suspense fallback={<DeferredPanel kind="player" label="正在加载本地播放器…" />}>
                      <SubtitlePlayer
                        initialTime={currentTime}
                        ref={videoPlayerRef}
                        projectId={activeProject.id}
                        videoUrl={api.getBackendMediaUrl(activeProject.video_url) || ''}
                        segments={segments}
                        style={subtitleStyle}
                        activeIdx={activeSegmentIdx}
                        onTimeUpdate={onTimeUpdate}
                        onDurationChange={onDurationChange}
                        onStyleChange={onStyleChange}
                        presentationMode={presentationMode}
                        onPresentationModeChange={onPresentationModeChange}
                      />
                    </Suspense>
                  ) : (
                    <div className="viewer-welcome">
                      <span>▶</span>
                      <h2>开始创作字幕</h2>
                      <p>
                        {youtubeEnabled ? '导入视频或粘贴 YouTube 链接' : '导入您有权处理的本地视频'}
                      </p>
                      <div>
                        <button className="button primary" onClick={onImportLocal}>
                          导入视频
                        </button>
                        {youtubeEnabled && (
                          <button className="button secondary" onClick={onOpenLinkPopover}>
                            添加链接
                          </button>
                        )}
                      </div>
                    </div>
                  )
                }
                editor={
                  segmentsLoading ? (
                    <div className="editor-load-state" role="status">
                      正在载入字幕…
                    </div>
                  ) : segmentsError ? (
                    <div className="editor-load-state" role="alert">
                      {segmentsError}
                      <button onClick={() => activeProject && onRefreshSegments(activeProject.id)}>
                        重新加载
                      </button>
                    </div>
                  ) : (
                    <SubtitleTable
                      segments={segments}
                      currentTime={currentTime}
                      activeIdx={activeSegmentIndex}
                      entryFocusIdx={subtitleEntryFocusIndex}
                      entryFocusRequest={subtitleFocusRequest}
                      onSeek={onSeek}
                      onInspect={onInspectSegment}
                      onUpdate={onUpdateSegment}
                      onReplaceAll={onReplaceAll}
                      onSplit={onSplit}
                      onMerge={onMerge}
                      onDelete={onDelete}
                      onUndo={onUndo}
                      onRedo={onRedo}
                      saveState={editorSaveState}
                      draftCount={draftCount}
                      draftIsStale={draftIsStale}
                      onPreviewDraft={onPreviewDraft}
                      onCommitDraft={onCommitDraft}
                      onDiscardDraft={onDiscardDraft}
                      onAutoScrollChange={onAutoScrollChange}
                      autoScroll={autoScrollTable}
                      disabled={editorBusy || segmentsLoading}
                    />
                  )
                }
                timeline={
                  activeProject && (
                    <SubtitleTimeline
                      projectId={activeProject.id}
                      segments={segments}
                      currentTime={currentTime}
                      duration={videoDuration}
                      onSeek={onSeek}
                      onUpdateTime={(index, update) => {
                        if (!editorBusy) void onUpdateSegment(index, update);
                      }}
                    />
                  )
                }
                inspector={
                  inspectedSegment && (
                    <>
                      <button className="button secondary" onClick={() => onInspectSegment(null)}>
                        关闭属性
                      </button>
                      <h3>字幕 {inspectedSegment.index}</h3>
                      <label>
                        开始时间（秒）
                        <input
                          key={`start-${inspectedSegment.id}-${inspectedSegment.start}`}
                          type="number"
                          min={0}
                          step={0.01}
                          defaultValue={inspectedSegment.start}
                          disabled={editorBusy}
                          onBlur={event => {
                            const start = Number(event.target.value);
                            if (
                              Number.isFinite(start) &&
                              start >= 0 &&
                              start < inspectedSegment.end &&
                              start !== inspectedSegment.start
                            ) {
                              void onUpdateSegment(inspectedSegment.index, { start });
                            }
                          }}
                        />
                      </label>
                      <label>
                        结束时间（秒）
                        <input
                          key={`end-${inspectedSegment.id}-${inspectedSegment.end}`}
                          type="number"
                          min={inspectedSegment.start}
                          step={0.01}
                          defaultValue={inspectedSegment.end}
                          disabled={editorBusy}
                          onBlur={event => {
                            const end = Number(event.target.value);
                            if (
                              Number.isFinite(end) &&
                              end > inspectedSegment.start &&
                              end !== inspectedSegment.end
                            ) {
                              void onUpdateSegment(inspectedSegment.index, { end });
                            }
                          }}
                        />
                      </label>
                      <button
                        className="button secondary"
                        onClick={() => onSelectWorkspace('style')}
                      >
                        调整字幕样式
                      </button>
                    </>
                  )
                }
              />
            )}

            {projectWorkspace === 'style' && (
              <section className="task-page style-task-page">
                <div className="style-canvas">
                  <StyleTemplateBar style={subtitleStyle} onApply={onStyleChange} />
                  <header>
                    <h2>实时外观预览</h2>
                    <p>在接近成片的画面比例中调整字幕，不受其他工具干扰。</p>
                  </header>
                  <div className="style-canvas-stage">
                    {activeProject && canPlayMedia && activeProject.video_url ? (
                      <Suspense fallback={<DeferredPanel kind="player" label="正在加载样式预览…" />}>
                        <SubtitlePlayer
                          initialTime={currentTime}
                          ref={videoPlayerRef}
                          projectId={activeProject.id}
                          videoUrl={api.getBackendMediaUrl(activeProject.video_url) || ''}
                          segments={segments}
                          style={subtitleStyle}
                          activeIdx={activeSegmentIdx}
                          onTimeUpdate={onTimeUpdate}
                          onDurationChange={onDurationChange}
                          onStyleChange={onStyleChange}
                          presentationMode={presentationMode}
                          onPresentationModeChange={onPresentationModeChange}
                        />
                      </Suspense>
                    ) : (
                      <div className="style-preview-card">
                        <span>为每一句话找到恰好的位置。</span>
                        <small>Give every line its perfect place.</small>
                      </div>
                    )}
                  </div>
                </div>
                <aside className="style-controls-page">
                  <header>
                    <small>字幕检查器</small>
                    <h2>字体与排版</h2>
                  </header>
                  <SubtitleStylePanel style={subtitleStyle} onChange={onStyleChange} />
                </aside>
              </section>
            )}

            {projectWorkspace === 'export' && (
              <ExportWorkspace
                bilingual={bilingual}
                onBilingual={onBilingualChange}
                hasSegments={hasSegments}
                hasVideo={hasLocalVideo}
                busy={
                  taskStarting ||
                  (isProcessing && currentTask?.type === 'render') ||
                  draftCount > 0
                }
                onExport={onExport}
                task={
                  <>
                    {currentTask && ['render', 'export'].includes(currentTask.type) && (
                      <div className={`export-task-card ${currentTask.status}`}>
                        <div>
                          <strong>{currentTask.message || '导出任务'}</strong>
                          <small>
                            {currentTask.status === 'success'
                              ? '文件已准备完成'
                              : '可离开此页面，任务会继续运行'}
                          </small>
                        </div>
                        <progress max={100} value={currentTask.progress || 0} />
                        <span>{Math.round(currentTask.progress || 0)}%</span>
                      </div>
                    )}
                  </>
                }
              />
            )}
          </div>

          {toolsOpen && (
            <WorkspacePanel
              title="任务与流程"
              onClose={onCloseTools}
              actions={
                <button type="button" className="panel-link" onClick={onOpenTaskDrawer}>
                  全部后台任务{activeTaskCount ? <b>{activeTaskCount}</b> : null}
                </button>
              }
            >
              <TaskWorkspace
                steps={compactSteps}
                selected={activeProcessStep}
                onSelect={onSelectStep}
                task={currentTask}
                settings={processSettingsNode}
                onPause={onPauseTask}
                onCancel={onCancelTask}
                diagnostics={
                  <details className="process-diagnostics">
                    <summary>
                      任务日志与诊断 <span>{processLogs.length}</span>
                    </summary>
                    <div>
                      <ProcessTimeline
                        steps={processSteps}
                        currentStepId={activeProcessStep}
                        totalProgress={totalProgress}
                        onStepClick={onSelectStep}
                      />
                      <ProcessLogViewer
                        logs={processLogs}
                        collapsed={false}
                        onToggle={() => undefined}
                        onClear={onClearLogs}
                      />
                    </div>
                  </details>
                }
              />
            </WorkspacePanel>
          )}
        </>
      )}
    </main>
  );
}
