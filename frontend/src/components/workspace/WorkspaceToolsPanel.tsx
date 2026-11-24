import { lazy, Suspense, type ReactNode, type RefObject } from 'react';
import type {
  EditorOperationResponse,
  ProcessLogEntry,
  ProcessStep,
  Project,
  SubtitleSegment,
  TaskStatus,
} from '../../types';
import DeferredPanel from '../DeferredPanel';
import GlossaryPanel from '../GlossaryPanel';
import ProcessLogViewer from '../ProcessLogViewer';
import ProcessTimeline from '../ProcessTimeline';
import QualityPanel from '../QualityPanel';
import SmartToolsPanel from '../SmartToolsPanel';
import TaskWorkspace from '../TaskWorkspace';
import WorkspacePanel from '../WorkspacePanel';

const ContentCenter = lazy(() => import('../ContentCenter'));

interface Props {
  activeProject: Project | null;
  toolTabsRef: RefObject<HTMLElement | null>;
  toolsTab: 'process' | 'quality' | 'smart' | 'content';
  activeTaskCount: number;
  currentTask: TaskStatus | null;
  segments: SubtitleSegment[];
  hasSegments: boolean;
  editorRevision: number;
  videoDuration: number;
  compactSteps: Array<{ id: string; label: string; icon: string; state: ProcessStep }>;
  activeProcessStep: string;
  processSteps: ProcessStep[];
  totalProgress: number;
  processLogs: ProcessLogEntry[];
  processSettingsNode: ReactNode;
  onClose: () => void;
  onOpenTaskDrawer: () => void;
  onToolsTabChange: (tab: 'process' | 'quality' | 'smart' | 'content') => void;
  onSelectWorkspace: (workspace: 'subtitles' | 'style' | 'export') => void;
  onAcceptEditorResult: (projectId: string, result: EditorOperationResponse) => void;
  onRefreshActiveProject: (projectId: string) => void;
  onSeek: (time: number) => void;
  onSelectStep: (stepId: string) => void;
  onPauseTask: () => void;
  onCancelTask: () => void;
  onClearLogs: () => void;
  onPreviewClipRange: (start: number, end: number) => void;
  onMessage: (message: string, duration?: number) => void;
}

export default function WorkspaceToolsPanel({
  activeProject,
  toolTabsRef,
  toolsTab,
  activeTaskCount,
  currentTask,
  segments,
  hasSegments,
  editorRevision,
  videoDuration,
  compactSteps,
  activeProcessStep,
  processSteps,
  totalProgress,
  processLogs,
  processSettingsNode,
  onClose,
  onOpenTaskDrawer,
  onToolsTabChange,
  onSelectWorkspace,
  onAcceptEditorResult,
  onRefreshActiveProject,
  onSeek,
  onSelectStep,
  onPauseTask,
  onCancelTask,
  onClearLogs,
  onPreviewClipRange,
  onMessage,
}: Props) {
  return (
    <WorkspacePanel
      title="任务与工具"
      onClose={onClose}
      actions={
        <button type="button" className="panel-link" onClick={onOpenTaskDrawer}>
          全部后台任务{activeTaskCount ? <b>{activeTaskCount}</b> : null}
        </button>
      }
    >
      <nav ref={toolTabsRef} className="tool-panel-tabs sliding-tabs" aria-label="工具分类">
        {(
          [
            ['process', '字幕流程'],
            ['quality', '质检与术语'],
            ['smart', 'OCR 与说话人'],
            ['content', '内容'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            aria-pressed={toolsTab === id}
            onClick={() => onToolsTabChange(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      {toolsTab === 'quality' && activeProject && (
        <section className="task-page quality-task-page">
          <div className="quality-page-grid">
            <QualityPanel
              projectId={activeProject.id}
              segments={segments}
              revision={editorRevision}
              onEditorResult={result => onAcceptEditorResult(activeProject.id, result)}
              onSeek={time => {
                onSeek(time);
                onSelectWorkspace('subtitles');
              }}
            />
            <GlossaryPanel projectId={activeProject.id} />
          </div>
        </section>
      )}

      {toolsTab === 'smart' && activeProject && (
        <section className="task-page smart-task-page">
          <SmartToolsPanel
            projectId={activeProject.id}
            revision={editorRevision}
            duration={videoDuration}
            onEditorResult={result => onAcceptEditorResult(activeProject.id, result)}
            onProjectChanged={() => onRefreshActiveProject(activeProject.id)}
          />
        </section>
      )}

      {toolsTab === 'process' && (
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
      )}

      {toolsTab === 'content' && activeProject && (
        <Suspense fallback={<DeferredPanel label="正在打开内容工作区…" />}>
          <ContentCenter
            project={activeProject}
            projectRevision={editorRevision}
            hasSegments={hasSegments}
            onPreview={onPreviewClipRange}
            onMessage={message => onMessage(message, 4200)}
          />
        </Suspense>
      )}
    </WorkspacePanel>
  );
}
