import {
  useCallback,
  useRef,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import * as api from '../../api/backend';
import type {
  ExportFormat,
  ProcessStep,
  ProcessingConfig,
  Project,
  SubtitleStyleSettings,
  TaskStatus,
} from '../../types';
import { projectExportFilename } from '../../utils/library';
import type { StartTaskFn } from './useTranscriptionActions';

interface UseMediaExportActionsOptions {
  activeProject: Project | null;
  activeProjectIdRef: MutableRefObject<string | null>;
  currentTask: TaskStatus | null;
  taskStarting: boolean;
  setTaskStarting: Dispatch<SetStateAction<boolean>>;
  config: ProcessingConfig;
  subtitleStyle: SubtitleStyleSettings;
  startTask: StartTaskFn;
  setActiveProject: Dispatch<SetStateAction<Project | null>>;
  setProjects: Dispatch<SetStateAction<Project[]>>;
  setCurrentTask: (task: TaskStatus | null) => void;
  setPollInterval: Dispatch<SetStateAction<number | null>>;
  setStepStatus: (
    stepId: string,
    status: ProcessStep['status'],
    progress?: number,
    error?: string,
    suggestion?: string,
  ) => void;
  resetBackendLogCursor: (taskId?: string) => void;
  syncProcessFromTask: (task: TaskStatus) => void;
  ingestTaskLogs: (task: TaskStatus) => void;
  addLog: (
    level: 'info' | 'warning' | 'error',
    step: string,
    message: string,
    detail?: string,
    suggestion?: string,
  ) => void;
  setToast: Dispatch<SetStateAction<string>>;
  showToast: (message: string, duration?: number) => void;
}

export function useMediaExportActions({
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
}: UseMediaExportActionsOptions) {
  const exportActionLock = useRef(false);

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
      setTaskStarting,
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
      setTaskStarting,
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
      setTaskStarting,
      setToast,
      syncProcessFromTask,
      taskStarting,
    ],
  );

  const downloadLocalCopy = useCallback(() => {
    void beginMaterialization('manual', '下载本地副本');
  }, [beginMaterialization]);

  return {
    doExport,
    retryDownload,
    changeProjectMediaMode,
    beginMaterialization,
    downloadLocalCopy,
  };
}
