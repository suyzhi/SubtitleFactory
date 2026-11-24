import {
  useCallback,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import * as api from '../api/backend';
import { taskIsActive, taskProgressLabel } from '../projectState';
import {
  recoveryAction,
  recoveryActionLabel as taskRecoveryActionLabel,
} from '../taskRecovery';
import type {
  ProcessLogEntry,
  ProcessingConfig,
  Project,
  SubtitleSegment,
  SubtitleStyleSettings,
} from '../types';
import { useMediaExportActions } from './tasks/useMediaExportActions';
import { useTextProcessingActions } from './tasks/useTextProcessingActions';
import { useTranscriptionActions } from './tasks/useTranscriptionActions';
import { useTaskMonitor } from './useTaskMonitor';

interface UseProjectTasksOptions {
  activeProject: Project | null;
  activeProjectIdRef: MutableRefObject<string | null>;
  activeProjectIsYoutube: boolean;
  config: ProcessingConfig;
  subtitleStyle: SubtitleStyleSettings;
  flowOptions: {
    enable_clean: boolean;
    enable_translate: boolean;
    target_language: string;
    clean_target_length: number;
    text_processing_consent: boolean;
  };
  editorRevision: MutableRefObject<number>;
  setActiveProject: Dispatch<SetStateAction<Project | null>>;
  setProjects: Dispatch<SetStateAction<Project[]>>;
  setSegments: Dispatch<SetStateAction<SubtitleSegment[]>>;
  setLibraryRefresh: Dispatch<SetStateAction<number>>;
  setToolsTab: Dispatch<SetStateAction<'process' | 'quality' | 'smart' | 'content'>>;
  setToolsOpen: Dispatch<SetStateAction<boolean>>;
  setShowAISettings: Dispatch<SetStateAction<boolean>>;
  setSelectedStep: Dispatch<SetStateAction<string | null>>;
  setProcessLogs: Dispatch<SetStateAction<ProcessLogEntry[]>>;
  addLog: (
    level: 'info' | 'warning' | 'error',
    step: string,
    message: string,
    detail?: string,
    suggestion?: string,
  ) => void;
  compatibleModel: () => string | null;
  runtimeForModel: (model: string) => string;
  requireRuntime: (model: string) => string;
  refreshSegments: (projectId: string) => Promise<void>;
  setToast: Dispatch<SetStateAction<string>>;
  showToast: (message: string, duration?: number) => void;
}

export function useProjectTasks({
  activeProject,
  activeProjectIdRef,
  activeProjectIsYoutube,
  config,
  subtitleStyle,
  flowOptions,
  editorRevision,
  setActiveProject,
  setProjects,
  setSegments,
  setLibraryRefresh,
  setToolsTab,
  setToolsOpen,
  setShowAISettings,
  setSelectedStep,
  setProcessLogs,
  addLog,
  compatibleModel,
  runtimeForModel,
  requireRuntime,
  refreshSegments,
  setToast,
  showToast,
}: UseProjectTasksOptions) {
  const monitor = useTaskMonitor({
    activeProject,
    activeProjectIdRef,
    activeProjectIsYoutube,
    editorRevision,
    setActiveProject,
    setSegments,
    setLibraryRefresh,
    setSelectedStep,
    setProcessLogs,
    addLog,
  });

  const {
    currentTask,
    setCurrentTask,
    activeTaskCount,
    pollInterval,
    setPollInterval,
    processSteps,
    setProcessSteps,
    setSubtitleStats,
    failedCleanBatches,
    setFailedCleanBatches,
    compactSteps,
    totalProgress,
    ingestTaskLogs,
    setStepStatus,
    syncProcessFromTask,
    refreshProcessSteps,
    resetBackendLogCursor,
    resetTaskCursors,
    resetTaskTracking,
    attachTask,
  } = monitor;

  const [taskStarting, setTaskStarting] = useState(false);
  const taskStartLock = useRef(false);

  const startTask = useCallback(
    async (
      name: string,
      stepId: string,
      fn: () => Promise<{ task_id: string }>,
      interval: number = 1000,
    ) => {
      if (taskStartLock.current) return;
      const projectId = activeProjectIdRef.current;
      taskStartLock.current = true;
      setTaskStarting(true);
      setStepStatus(stepId, 'running', 0);
      try {
        const { task_id } = await fn();
        if (activeProjectIdRef.current !== projectId) return;
        resetTaskCursors(task_id);
        addLog('info', name, `${name} 任务已创建`);
        const status = await api.getTaskStatus(task_id);
        if (activeProjectIdRef.current !== projectId) return;
        setCurrentTask(status);
        syncProcessFromTask(status);
        ingestTaskLogs(status);
        setPollInterval(interval);
      } catch (e: any) {
        if (activeProjectIdRef.current !== projectId) return;
        addLog('error', name, `${name} 失败: ${e.message}`);
        setStepStatus(stepId, 'failed', 0, e.message);
        const suggestionMap: Record<string, string> = {
          下载视频: '请查看任务错误码；App 会区分权限、网络、格式、运行时和存储问题',
          提取音频: '请查看 FFmpeg、源媒体和输出目录的具体诊断',
          转写: '检查模型是否已下载，网络是否正常',
          'AI 整理': '检查 API Key、余额、网络连接',
          'AI 翻译': '检查 API Key、余额、网络连接',
          导出: '检查 ffmpeg 和输出目录权限',
        };
        addLog(
          'error',
          name,
          `建议: ${suggestionMap[name] || '请查看详细日志'}`,
          undefined,
          suggestionMap[name],
        );
      } finally {
        taskStartLock.current = false;
        setTaskStarting(false);
      }
    },
    [
      activeProjectIdRef,
      addLog,
      ingestTaskLogs,
      resetTaskCursors,
      setCurrentTask,
      setPollInterval,
      setStepStatus,
      syncProcessFromTask,
    ],
  );

  const { doExtractAudio, doTranscribe, doGenerateSubtitles, recoverTranscription } =
    useTranscriptionActions({
      activeProject,
      currentTask,
      config,
      flowOptions,
      startTask,
      compatibleModel,
      runtimeForModel,
      requireRuntime,
      setSubtitleStats,
      setSelectedStep,
      setToolsTab,
      setToolsOpen,
      setToast,
    });

  const { doClean, retryFailedCleanBatch, undoClean, doTranslate } = useTextProcessingActions({
    activeProject,
    activeProjectIdRef,
    currentTask,
    taskStarting,
    setTaskStarting,
    config,
    startTask,
    setCurrentTask,
    setPollInterval,
    setFailedCleanBatches,
    setLibraryRefresh,
    syncProcessFromTask,
    ingestTaskLogs,
    refreshSegments,
    setToast,
  });

  const {
    doExport,
    retryDownload,
    changeProjectMediaMode,
    beginMaterialization,
    downloadLocalCopy,
  } = useMediaExportActions({
    activeProject,
    activeProjectIdRef,
    currentTask,
    taskStarting,
    setTaskStarting,
    config,
    subtitleStyle,
    startTask,
    setActiveProject,
    setProjects,
    setCurrentTask,
    setPollInterval,
    setStepStatus,
    resetBackendLogCursor,
    syncProcessFromTask,
    ingestTaskLogs,
    addLog,
    setToast,
    showToast,
  });

  const retryCurrentFailure = useCallback(() => {
    if (!currentTask?.recoverable) return;
    const action = recoveryAction(currentTask);
    if (currentTask.type === 'materialize_video') {
      void beginMaterialization('manual', '重新下载本地副本');
      return;
    }
    if (currentTask.type === 'switch_media_mode') {
      void changeProjectMediaMode('local');
      return;
    }
    if (action === 'workflow' && activeProject) {
      const payload = currentTask.details?.resume_payload;
      if (payload) {
        void startTask('从失败步骤重试', 'transcribe', () =>
          api.startWorkflow(activeProject.id, {
            model: payload.model,
            language: payload.language,
            runtime: payload.runtime,
            ...payload.options,
            resume_task_id: currentTask.id,
          }),
        );
      } else {
        doGenerateSubtitles();
      }
      return;
    }
    if (action === 'download') return retryDownload();
    if (action === 'transcription') {
      void recoverTranscription(currentTask.error_code === 'APP_INTERRUPTED');
      return;
    }
    if (action === 'extract_audio') return doExtractAudio();
    if (action === 'clean') return doClean();
    if (action === 'translate') return doTranslate();
    if (action === 'render') {
      const format = currentTask.details?.format === 'mkv' ? 'mkv' : 'mp4';
      void doExport(format);
      return;
    }
    if (action === 'smart_tools') {
      setToolsTab('smart');
      setToolsOpen(true);
      setToast('已打开智能工具；请检查范围与模型后重新开始');
      return;
    }
    if (action === 'content') {
      setToolsTab('content');
      setToolsOpen(true);
      setToast('已打开内容工作区；已完成结果仍在，请手动重试中断的部分');
      return;
    }
    if (action === 'settings') {
      setShowAISettings(true);
      return;
    }
    setToolsTab('process');
    setToolsOpen(true);
    setToast('已打开处理流程，请检查参数后重新开始');
  }, [
    activeProject,
    beginMaterialization,
    changeProjectMediaMode,
    currentTask,
    doClean,
    doExport,
    doExtractAudio,
    doGenerateSubtitles,
    doTranslate,
    recoverTranscription,
    retryDownload,
    setShowAISettings,
    setToast,
    setToolsOpen,
    setToolsTab,
    startTask,
  ]);

  const toggleTaskPause = useCallback(async () => {
    if (!currentTask) return;
    const taskId = currentTask.id;
    const projectId = activeProjectIdRef.current;
    try {
      const next =
        currentTask.status === 'paused'
          ? await api.resumeTask(taskId)
          : await api.pauseTask(taskId);
      if (activeProjectIdRef.current !== projectId || next.id !== taskId) return;
      setCurrentTask(next);
      syncProcessFromTask(next);
      addLog('info', next.type, next.status === 'paused' ? '任务已暂停' : '任务已继续');
    } catch (error: any) {
      addLog('error', currentTask.type, error.message);
    }
  }, [activeProjectIdRef, addLog, currentTask, setCurrentTask, syncProcessFromTask]);

  const cancelCurrentTask = useCallback(async () => {
    if (!currentTask || !['pending', 'running', 'paused'].includes(currentTask.status)) return;
    const taskId = currentTask.id;
    const projectId = activeProject?.id || null;
    try {
      const next = await api.cancelTask(taskId);
      if (activeProjectIdRef.current !== projectId || next.id !== taskId) return;
      setCurrentTask(next);
      ingestTaskLogs(next);
      setPollInterval(taskIsActive(next) ? 1000 : null);
      syncProcessFromTask(next);
      addLog('warning', next.type, taskProgressLabel(next));
      setToast(taskProgressLabel(next));
      window.setTimeout(() => setToast(''), 2600);
      if (projectId) {
        api
          .getProject(projectId)
          .then(project => {
            if (activeProjectIdRef.current === projectId) setActiveProject(project);
          })
          .catch(() => {});
        setLibraryRefresh(value => value + 1);
        api
          .getSegments(projectId)
          .then(result => {
            if (activeProjectIdRef.current === projectId) setSegments(result.segments);
          })
          .catch(() => {});
      }
    } catch (error: any) {
      addLog('error', currentTask.type, `终止失败：${error.message}`);
      setToast('无法终止当前任务');
      window.setTimeout(() => setToast(''), 3000);
    }
  }, [
    activeProject,
    activeProjectIdRef,
    addLog,
    currentTask,
    ingestTaskLogs,
    setActiveProject,
    setCurrentTask,
    setLibraryRefresh,
    setPollInterval,
    setSegments,
    setToast,
    syncProcessFromTask,
  ]);

  const recoveryActionLabel = taskRecoveryActionLabel(currentTask);

  return {
    currentTask,
    setCurrentTask,
    activeTaskCount,
    pollInterval,
    setPollInterval,
    taskStarting,
    processSteps,
    setProcessSteps,
    failedCleanBatches,
    compactSteps,
    totalProgress,
    recoveryActionLabel,
    ingestTaskLogs,
    syncProcessFromTask,
    refreshProcessSteps,
    resetTaskTracking,
    attachTask,
    doExtractAudio,
    doTranscribe,
    doGenerateSubtitles,
    doClean,
    retryFailedCleanBatch,
    undoClean,
    doTranslate,
    doExport,
    retryDownload,
    downloadLocalCopy,
    retryCurrentFailure,
    toggleTaskPause,
    cancelCurrentTask,
  };
}
