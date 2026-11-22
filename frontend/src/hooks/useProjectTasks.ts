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
  ExportFormat,
  ProcessLogEntry,
  ProcessingConfig,
  Project,
  SubtitleSegment,
  SubtitleStyleSettings,
} from '../types';
import { projectExportFilename } from '../utils/library';
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
  const exportActionLock = useRef(false);

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

  const doExtractAudio = useCallback(() => {
    if (!activeProject) return;
    void startTask('提取音频', 'extract_audio', () => api.startExtractAudio(activeProject.id));
  }, [activeProject, startTask]);

  const doTranscribe = useCallback(() => {
    if (!activeProject) return;
    const model = compatibleModel();
    if (!model) return;
    setSubtitleStats(null);
    const runtime = requireRuntime(model);
    if (!runtime) return;
    void startTask('转写', 'transcribe', () =>
      api.startTranscribe(activeProject.id, config.language, model, runtime),
    );
  }, [activeProject, compatibleModel, config.language, requireRuntime, setSubtitleStats, startTask]);

  const doGenerateSubtitles = useCallback(() => {
    if (!activeProject) return;
    const model = compatibleModel();
    if (!model) return;
    const runtime = requireRuntime(model);
    if (!runtime) return;
    setSubtitleStats(null);
    void startTask('自动生成字幕', 'transcribe', () =>
      api.startWorkflow(activeProject.id, {
        model,
        language: config.language,
        runtime,
        ...flowOptions,
      }),
    );
  }, [
    activeProject,
    compatibleModel,
    config.language,
    flowOptions,
    requireRuntime,
    setSubtitleStats,
    startTask,
  ]);

  const recoverTranscription = useCallback(
    async (preserveModel = false) => {
      if (!activeProject || !currentTask?.recoverable) return;
      try {
        const status = await api.getTranscriptionModels(activeProject.id, config.language);
        const failedModel = String(
          currentTask.details?.model_id ||
            currentTask.details?.model_resolution?.model_id ||
            currentTask.details?.resume_payload?.model ||
            currentTask.details?.resolved_model ||
            config.model,
        );
        if (preserveModel) {
          const selected = status.models.find(item => item.id === failedModel);
          if (!selected?.ready) {
            setSelectedStep('transcribe');
            setToolsOpen(true);
            setToast('原转写模型当前未就绪，请检查模型与运行设备后重试');
            return;
          }
          const runtime = String(currentTask.details?.runtime || runtimeForModel(failedModel) || '');
          if (!runtime) {
            setSelectedStep('transcribe');
            setToolsOpen(true);
            setToast('请重新选择转写运行设备后再重试');
            return;
          }
          if (
            !window.confirm(
              `上次转写在应用退出时被中断。原有已发布字幕未被覆盖。\n\n是否使用 ${selected.name} 从头重新转写？`,
            )
          ) {
            return;
          }
          await startTask('重新开始转写', 'transcribe', () =>
            api.retryTranscription(activeProject.id, {
              model: failedModel,
              language: String(currentTask.details?.language || config.language),
              runtime,
            }),
          );
          return;
        }
        const fallback =
          status.models.find(item => item.ready && item.id !== failedModel && item.id === 'small') ||
          status.models.find(item => item.ready && item.id !== failedModel);
        if (!fallback) {
          setToast('没有已就绪的备用模型，请打开转写参数选择模型');
          return;
        }
        const detail = fallback.download_required
          ? '该模型可能需要下载。'
          : '该模型已在本机就绪。';
        const runtime = requireRuntime(fallback.id);
        if (!runtime) return;
        if (
          !window.confirm(
            `当前转写失败：${currentTask.error || currentTask.message}\n\n是否改用 ${fallback.name} 重试？${detail}`,
          )
        ) {
          return;
        }
        await startTask('备用模型转写', 'transcribe', () =>
          api.retryTranscription(activeProject.id, {
            model: fallback.id,
            language: config.language,
            runtime,
          }),
        );
      } catch (error: any) {
        setToast(`无法启动恢复：${error.message}`);
      }
    },
    [
      activeProject,
      config.language,
      config.model,
      currentTask,
      requireRuntime,
      runtimeForModel,
      setSelectedStep,
      setToast,
      setToolsOpen,
      startTask,
    ],
  );

  const doClean = useCallback(() => {
    if (!activeProject) return;
    void startTask('AI 整理', 'clean', () =>
      api.startClean(activeProject.id, config.clean_target_length),
    );
  }, [activeProject, config.clean_target_length, startTask]);

  const retryFailedCleanBatch = useCallback(
    async (batchIndex: number) => {
      if (
        !currentTask ||
        taskStarting ||
        ['running', 'pending', 'paused'].includes(currentTask.status)
      ) {
        return;
      }
      const originalTaskId = currentTask.id;
      const projectId = activeProjectIdRef.current;
      setTaskStarting(true);
      try {
        const result = await api.retryFailedCleanBatch(originalTaskId, batchIndex);
        const status = await api.getTaskStatus(result.task_id);
        if (
          activeProjectIdRef.current !== projectId ||
          (status.project_id && status.project_id !== projectId)
        ) {
          return;
        }
        setCurrentTask(status);
        syncProcessFromTask(status);
        ingestTaskLogs(status);
        setFailedCleanBatches(items => items.filter(item => item.batch_index !== batchIndex));
        setPollInterval(1000);
        setToast(`已启动第 ${batchIndex} 批的单独重试`);
        window.setTimeout(() => setToast(''), 3000);
      } catch (error: any) {
        setToast(`无法重试第 ${batchIndex} 批：${error.message}`);
        window.setTimeout(() => setToast(''), 4500);
      } finally {
        setTaskStarting(false);
      }
    },
    [
      activeProjectIdRef,
      currentTask,
      ingestTaskLogs,
      setCurrentTask,
      setFailedCleanBatches,
      setPollInterval,
      setToast,
      syncProcessFromTask,
      taskStarting,
    ],
  );

  const undoClean = useCallback(async () => {
    if (
      !activeProject ||
      (currentTask && ['running', 'pending', 'paused'].includes(currentTask.status))
    ) {
      return;
    }
    try {
      const result = await api.undoClean(activeProject.id);
      await refreshSegments(activeProject.id);
      setLibraryRefresh(value => value + 1);
      setToast(result.message);
      window.setTimeout(() => setToast(''), 3000);
    } catch (error: any) {
      setToast(error.message.includes('404') ? '没有可撤销的整理记录' : error.message);
      window.setTimeout(() => setToast(''), 3500);
    }
  }, [activeProject, currentTask, refreshSegments, setLibraryRefresh, setToast]);

  const doTranslate = useCallback(() => {
    if (!activeProject) return;
    void startTask('AI 翻译', 'translate', () =>
      api.startTranslate(activeProject.id, config.target_language),
    );
  }, [activeProject, config.target_language, startTask]);

  const doExport = useCallback(
    async (fmt: ExportFormat) => {
      if (!activeProject || exportActionLock.current) return;
      const projectId = activeProject.id;
      exportActionLock.current = true;
      setTaskStarting(true);
      setStepStatus('export', 'running', 10);
      try {
        if (fmt === 'mp4' || fmt === 'mkv') {
          const r = await api.exportSubtitles(projectId, {
            format: fmt,
            bilingual: config.bilingual,
            primary_language:
              subtitleStyle.mode === 'bilingual_translated_first' ? 'translated' : 'original',
            style: subtitleStyle,
          });
          if (r.task_id) {
            const s = await api.getTaskStatus(r.task_id);
            if (activeProjectIdRef.current !== projectId) return;
            resetBackendLogCursor(r.task_id);
            setCurrentTask(s);
            ingestTaskLogs(s);
            setPollInterval(2000);
            setStepStatus('render', 'running', 10);
            addLog('info', '压制视频', `${fmt.toUpperCase()} 视频导出任务已创建`);
          }
        } else {
          const result = await api.exportSubtitles(projectId, {
            format: fmt,
            bilingual: config.bilingual,
            primary_language:
              subtitleStyle.mode === 'bilingual_translated_first' ? 'translated' : 'original',
            style: subtitleStyle,
          });
          if (activeProjectIdRef.current !== projectId) return;
          setStepStatus('export', 'success', 100);
          const saved = await api.downloadExport(
            projectId,
            fmt,
            result.path,
            projectExportFilename(activeProject.title, fmt),
          );
          if (activeProjectIdRef.current !== projectId) return;
          addLog(
            'info',
            '导出',
            saved ? `${fmt.toUpperCase()} 已保存` : `已取消保存 ${fmt.toUpperCase()}`,
          );
          showToast(saved ? `${fmt.toUpperCase()} 字幕已导出` : '已取消保存');
        }
      } catch (e: any) {
        if (activeProjectIdRef.current !== projectId) return;
        addLog('error', '导出', `${fmt} 导出失败: ${e.message}`);
        showToast(`导出失败：${e.message}`, 5000);
        setStepStatus('export', 'failed', 0, e.message, '检查文件权限和 ffmpeg');
      } finally {
        exportActionLock.current = false;
        setTaskStarting(false);
      }
    },
    [
      activeProject,
      activeProjectIdRef,
      addLog,
      config.bilingual,
      ingestTaskLogs,
      resetBackendLogCursor,
      setCurrentTask,
      setPollInterval,
      setStepStatus,
      showToast,
      subtitleStyle,
    ],
  );

  const retryDownload = useCallback(() => {
    if (!activeProject?.source_url) return;
    void startTask('重新下载', 'download', () =>
      api.startDownload(activeProject.id, activeProject.source_url as string),
    );
  }, [activeProject, startTask]);

  const changeProjectMediaMode = useCallback(
    async (mediaMode: 'local') => {
      if (
        !activeProject ||
        activeProject.source_type !== 'youtube' ||
        taskStarting ||
        Boolean(currentTask && ['running', 'pending', 'paused'].includes(currentTask.status))
      ) {
        return;
      }
      const projectId = activeProject.id;
      setTaskStarting(true);
      try {
        const result = await api.updateProjectMediaMode(projectId, mediaMode);
        if (activeProjectIdRef.current !== projectId) return;
        setActiveProject(result.project);
        setProjects(current =>
          current.map(project => (project.id === result.project.id ? result.project : project)),
        );
        setToast(result.message);
        window.setTimeout(() => setToast(''), 3200);
        if (result.task_id) {
          const status = await api.getTaskStatus(result.task_id);
          if (activeProjectIdRef.current !== projectId) return;
          setCurrentTask(status);
          syncProcessFromTask(status);
          ingestTaskLogs(status);
          setPollInterval(1000);
        }
      } catch (error: any) {
        if (activeProjectIdRef.current !== projectId) return;
        setToast(`媒体模式切换失败：${error.message}`);
        window.setTimeout(() => setToast(''), 4200);
      } finally {
        setTaskStarting(false);
      }
    },
    [
      activeProject,
      activeProjectIdRef,
      currentTask,
      ingestTaskLogs,
      setActiveProject,
      setCurrentTask,
      setPollInterval,
      setProjects,
      setToast,
      syncProcessFromTask,
      taskStarting,
    ],
  );

  const beginMaterialization = useCallback(
    async (reason: 'manual' | 'player_fallback' | 'offline', label: string) => {
      if (!activeProject || activeProject.source_type !== 'youtube' || taskStarting) return;
      const projectId = activeProject.id;
      setTaskStarting(true);
      setStepStatus('download', 'running', 0);
      try {
        const result = await api.materializeProjectVideo(projectId, reason);
        if (activeProjectIdRef.current !== projectId) return;
        if (result.task_id) {
          const status = await api.getTaskStatus(result.task_id);
          if (activeProjectIdRef.current !== projectId) return;
          setCurrentTask(status);
          syncProcessFromTask(status);
          ingestTaskLogs(status);
          setPollInterval(1000);
          return;
        }
        const project = result.project || (await api.getProject(projectId));
        if (activeProjectIdRef.current !== projectId) return;
        setActiveProject(project);
        setProjects(current => current.map(item => (item.id === project.id ? project : item)));
        setStepStatus('download', 'success', 100);
        setToast(result.message || `${label}已完成`);
        window.setTimeout(() => setToast(''), 3200);
      } catch (error: any) {
        if (activeProjectIdRef.current !== projectId) return;
        setStepStatus('download', 'failed', 0, error.message);
        setToast(`${label}失败：${error.message}`);
        window.setTimeout(() => setToast(''), 4200);
      } finally {
        setTaskStarting(false);
      }
    },
    [
      activeProject,
      activeProjectIdRef,
      ingestTaskLogs,
      setActiveProject,
      setCurrentTask,
      setPollInterval,
      setProjects,
      setStepStatus,
      setToast,
      syncProcessFromTask,
      taskStarting,
    ],
  );

  const downloadLocalCopy = useCallback(() => {
    void beginMaterialization('manual', '下载本地副本');
  }, [beginMaterialization]);

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
    if (action === 'download') {
      retryDownload();
      return;
    }
    if (action === 'transcription') {
      void recoverTranscription(currentTask.error_code === 'APP_INTERRUPTED');
      return;
    }
    if (action === 'extract_audio') {
      doExtractAudio();
      return;
    }
    if (action === 'clean') {
      doClean();
      return;
    }
    if (action === 'translate') {
      doTranslate();
      return;
    }
    if (action === 'render') {
      const format = currentTask.details?.format === 'mkv' ? 'mkv' : 'mp4';
      void doExport(format);
      return;
    }
    if (action === 'settings') {
      setShowAISettings(true);
      return;
    }
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
