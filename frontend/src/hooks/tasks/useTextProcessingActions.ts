import {
  useCallback,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import * as api from '../../api/backend';
import type { FailedCleanBatch, ProcessingConfig, Project, TaskStatus } from '../../types';
import type { StartTaskFn } from './useTranscriptionActions';

interface UseTextProcessingActionsOptions {
  activeProject: Project | null;
  activeProjectIdRef: MutableRefObject<string | null>;
  currentTask: TaskStatus | null;
  taskStarting: boolean;
  setTaskStarting: Dispatch<SetStateAction<boolean>>;
  config: ProcessingConfig;
  startTask: StartTaskFn;
  setCurrentTask: (task: TaskStatus | null) => void;
  setPollInterval: Dispatch<SetStateAction<number | null>>;
  setFailedCleanBatches: Dispatch<SetStateAction<FailedCleanBatch[]>>;
  setLibraryRefresh: Dispatch<SetStateAction<number>>;
  syncProcessFromTask: (task: TaskStatus) => void;
  ingestTaskLogs: (task: TaskStatus) => void;
  refreshSegments: (projectId: string) => Promise<void>;
  setToast: Dispatch<SetStateAction<string>>;
}

export function useTextProcessingActions({
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
}: UseTextProcessingActionsOptions) {
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
      setTaskStarting,
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

  return {
    doClean,
    retryFailedCleanBatch,
    undoClean,
    doTranslate,
  };
}
