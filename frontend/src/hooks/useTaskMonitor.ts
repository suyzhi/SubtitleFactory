import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import * as api from '../api/backend';
import { deriveProcessSteps, emptyProcess } from '../processSteps';
import { taskIsActive } from '../projectState';
import { useActiveTaskCount, useSelectedTask } from '../taskStore';
import type {
  FailedCleanBatch,
  ProcessLogEntry,
  ProcessStep,
  Project,
  SubtitleSegment,
  SubtitleStats,
  TaskStatus,
  TaskStepStatus,
} from '../types';
import { projectExportFilename } from '../utils/library';

const STEP_TASK_MAP: Record<string, string> = {
  download: 'download',
  extract_audio: 'extract_audio',
  transcribe: 'transcribe',
  clean: 'clean',
  translate: 'translate',
  export: 'export',
  render: 'render',
};

interface UseTaskMonitorOptions {
  activeProject: Project | null;
  activeProjectIdRef: MutableRefObject<string | null>;
  activeProjectIsYoutube: boolean;
  editorRevision: MutableRefObject<number>;
  setActiveProject: Dispatch<SetStateAction<Project | null>>;
  setSegments: Dispatch<SetStateAction<SubtitleSegment[]>>;
  setLibraryRefresh: Dispatch<SetStateAction<number>>;
  setSelectedStep: Dispatch<SetStateAction<string | null>>;
  setProcessLogs: Dispatch<SetStateAction<ProcessLogEntry[]>>;
  addLog: (
    level: 'info' | 'warning' | 'error',
    step: string,
    message: string,
    detail?: string,
    suggestion?: string,
  ) => void;
}

export function useTaskMonitor({
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
}: UseTaskMonitorOptions) {
  const [currentTask, setCurrentTask] = useSelectedTask();
  const activeTaskCount = useActiveTaskCount();
  const [pollInterval, setPollInterval] = useState<number | null>(null);
  const [processSteps, setProcessSteps] = useState<ProcessStep[]>(emptyProcess);
  const [, setSubtitleStats] = useState<SubtitleStats | null>(null);
  const [failedCleanBatches, setFailedCleanBatches] = useState<FailedCleanBatch[]>([]);

  const lastTaskMessage = useRef('');
  const lastTaskBatch = useRef('');
  const lastTaskStep = useRef('');
  const lastTaskProgressBucket = useRef(-1);
  const backendLogTaskId = useRef('');
  const lastBackendLogCount = useRef(0);
  const downloadedRenderTask = useRef('');

  // 后端任务日志通常包含更具体的 detail / suggestion。按任务和游标增量合并。
  const ingestTaskLogs = useCallback(
    (task: TaskStatus) => {
      if (backendLogTaskId.current !== task.id) {
        backendLogTaskId.current = task.id;
        lastBackendLogCount.current = 0;
      }
      const logs = task.logs || [];
      if (logs.length < lastBackendLogCount.current) lastBackendLogCount.current = 0;
      const start = lastBackendLogCount.current;
      const fresh = logs.slice(start);
      lastBackendLogCount.current = logs.length;
      if (!fresh.length) return;
      const entries: ProcessLogEntry[] = fresh.map((log, offset) => ({
        id: `backend:${task.id}:${start + offset}`,
        time: log.time?.slice(-8) || new Date().toLocaleTimeString(),
        level: log.level,
        step: log.step || task.type,
        message: log.message,
        detail: log.detail || undefined,
        suggestion: log.suggestion || undefined,
      }));
      setProcessLogs(previous => [...previous, ...entries].slice(-200));
    },
    [setProcessLogs],
  );

  const setStepStatus = useCallback(
    (stepId: string, status: TaskStepStatus, progress = 0, error?: string, suggestion?: string) => {
      setProcessSteps(prev =>
        prev.map(s => (s.id === stepId ? { ...s, status, progress, error, suggestion } : s)),
      );
    },
    [],
  );

  const syncProcessFromTask = useCallback(
    (task: TaskStatus) => {
      if (task.type === 'workflow' && task.details?.stages) {
        const stageMap = task.details.stages as Record<string, TaskStepStatus>;
        for (const [stepId, status] of Object.entries(stageMap)) {
          const displayedStatus =
            status === 'running' && task.status === 'paused' ? 'paused' : status;
          setStepStatus(
            stepId,
            displayedStatus,
            status === 'success' ? 100 : status === 'running' ? task.progress : 0,
          );
        }
        return;
      }
      if (task.type === 'prepare_audio') {
        const status =
          task.status === 'success'
            ? 'success'
            : task.status === 'failed'
              ? 'failed'
              : task.status === 'cancelled'
                ? 'cancelled'
                : task.status === 'paused'
                  ? 'paused'
                  : 'running';
        setStepStatus(
          'download',
          status,
          task.progress,
          task.error || undefined,
          task.suggestion || undefined,
        );
        setStepStatus(
          'extract_audio',
          status,
          task.progress,
          task.error || undefined,
          task.suggestion || undefined,
        );
        return;
      }
      if (task.type === 'materialize_video' || task.type === 'switch_media_mode') {
        const status =
          task.status === 'success'
            ? 'success'
            : task.status === 'failed'
              ? 'failed'
              : task.status === 'cancelled'
                ? 'cancelled'
                : task.status === 'paused'
                  ? 'paused'
                  : 'running';
        setStepStatus(
          'download',
          status,
          task.progress,
          task.error || undefined,
          task.suggestion || undefined,
        );
        return;
      }
      for (const [stepId, taskType] of Object.entries(STEP_TASK_MAP)) {
        if (taskType === task.type || (task.type === 'render' && stepId === 'export')) {
          const taskStatusMap: Record<string, TaskStepStatus> = {
            pending: 'waiting',
            running: 'running',
            paused: 'paused',
            success: 'success',
            failed: 'failed',
            cancelled: 'cancelled',
            partial: 'partial',
          };
          const st = taskStatusMap[task.status] || 'waiting';
          setStepStatus(
            stepId,
            st,
            task.progress,
            task.error || undefined,
            task.suggestion || undefined,
          );
        }
      }
    },
    [setStepStatus],
  );

  const refreshProcessSteps = useCallback((proj: Project | null) => {
    setProcessSteps(deriveProcessSteps(proj));
  }, []);

  const resetBackendLogCursor = useCallback((taskId = '') => {
    backendLogTaskId.current = taskId;
    lastBackendLogCount.current = 0;
  }, []);

  const resetTaskCursors = useCallback(
    (taskId = '') => {
      lastTaskMessage.current = '';
      lastTaskBatch.current = '';
      lastTaskStep.current = '';
      lastTaskProgressBucket.current = -1;
      resetBackendLogCursor(taskId);
    },
    [resetBackendLogCursor],
  );

  const resetTaskTracking = useCallback(() => {
    resetTaskCursors('');
    setCurrentTask(null);
    setPollInterval(null);
    setSubtitleStats(null);
    setSelectedStep(null);
  }, [resetTaskCursors, setCurrentTask, setSelectedStep]);

  const attachTask = useCallback(
    (status: TaskStatus, interval = 1000) => {
      setCurrentTask(status);
      syncProcessFromTask(status);
      ingestTaskLogs(status);
      if (taskIsActive(status)) setPollInterval(interval);
    },
    [ingestTaskLogs, setCurrentTask, syncProcessFromTask],
  );

  // Poll task status while active
  const pollingTaskId = currentTask?.id;
  useEffect(() => {
    if (!pollInterval || !pollingTaskId) return;
    let cancelled = false;
    let requestedSequence = 0;
    let appliedSequence = 0;
    const id = window.setInterval(async () => {
      const sequence = ++requestedSequence;
      try {
        const status = await api.getTaskStatus(pollingTaskId);
        if (
          cancelled ||
          sequence < appliedSequence ||
          (status.project_id && activeProjectIdRef.current !== status.project_id)
        ) {
          return;
        }
        appliedSequence = sequence;
        setCurrentTask(status);
        syncProcessFromTask(status);
        ingestTaskLogs(status);
        if (status.message && status.message !== lastTaskMessage.current) {
          const progressBucket = Math.floor(Math.max(0, status.progress) / 10);
          const stepChanged = status.step !== lastTaskStep.current;
          const terminal = ['success', 'failed', 'cancelled', 'partial', 'paused'].includes(
            status.status,
          );
          const hasBatchProgress = status.details?.current_batch !== undefined;
          lastTaskMessage.current = status.message;
          lastTaskStep.current = status.step;
          if (
            terminal ||
            stepChanged ||
            (!hasBatchProgress && progressBucket > lastTaskProgressBucket.current)
          ) {
            lastTaskProgressBucket.current = progressBucket;
            addLog('info', status.type, status.message || status.step);
          }
        }

        if (
          ['transcribe', 'workflow'].includes(status.type) &&
          (status.status === 'running' || status.status === 'paused') &&
          activeProject
        ) {
          const projectId = activeProject.id;
          api
            .getSegments(projectId)
            .then(result => {
              if (activeProjectIdRef.current === projectId) setSegments(result.segments);
            })
            .catch(() => {});
        }

        if (status.details) {
          const d = status.details;
          if (d.current_batch !== undefined && d.total_batches !== undefined) {
            const batchKey = `${status.id}:${d.current_batch}/${d.total_batches}`;
            if (batchKey !== lastTaskBatch.current) {
              lastTaskBatch.current = batchKey;
              addLog('info', status.type, `正在处理批次 ${d.current_batch}/${d.total_batches}`);
            }
          }
          if (
            (status.type === 'transcribe' || status.type === 'workflow') &&
            status.status === 'success' &&
            d.total_segments
          ) {
            setSubtitleStats({
              totalSegments: d.total_segments,
              audioDuration: d.audio_duration,
              averageDuration: d.avg_duration,
              minDuration: d.min_duration,
              maxDuration: d.max_duration,
              mergedShortSegments: d.merged_short,
              splitLongSegments: d.split_long,
              tooShortCount: d.too_short_count,
              tooLongCount: d.too_long_count,
            });
          }
          if (
            (status.type === 'export' || status.type === 'render') &&
            status.status === 'success' &&
            d.output_path
          ) {
            addLog(
              'info',
              status.type,
              `输出文件已生成${d.output_size ? ` (${(d.output_size / 1024 / 1024).toFixed(1)}MB)` : ''}`,
            );
            if (status.type === 'render' && downloadedRenderTask.current !== status.id) {
              downloadedRenderTask.current = status.id;
              const format = String(d.format || 'mp4');
              void api
                .downloadExport(
                  activeProject?.id || status.project_id || '',
                  format,
                  String(d.output_path),
                  projectExportFilename(activeProject?.title, format, '带字幕'),
                )
                .then(saved => {
                  addLog('info', status.type, saved ? '成片已保存到所选位置' : '已取消保存成片');
                })
                .catch(error => {
                  addLog('error', status.type, `成片保存失败：${error.message}`);
                });
            }
          }
        }

        if (!taskIsActive(status)) {
          setPollInterval(null);
          if (activeProject) {
            const projectId = activeProject.id;
            api
              .getSegments(projectId)
              .then(result => {
                if (activeProjectIdRef.current === projectId) setSegments(result.segments);
              })
              .catch(() => {});
            api
              .getProject(projectId)
              .then(project => {
                if (activeProjectIdRef.current !== projectId) return;
                setActiveProject(project);
                editorRevision.current = Number(project.edit_revision || 0);
              })
              .catch(() => {});
            setLibraryRefresh(value => value + 1);
          }
        }
      } catch (e: any) {
        if (cancelled) return;
        addLog('error', '系统', `状态查询失败: ${e.message}`);
        setPollInterval(null);
      }
    }, pollInterval);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [
    activeProject,
    activeProjectIdRef,
    addLog,
    editorRevision,
    ingestTaskLogs,
    pollInterval,
    pollingTaskId,
    setActiveProject,
    setCurrentTask,
    setLibraryRefresh,
    setSegments,
    syncProcessFromTask,
  ]);

  useEffect(() => {
    let cancelled = false;
    const shouldLoad =
      currentTask?.type === 'clean' &&
      (currentTask.status === 'partial' || Number(currentTask.details?.failed_batches || 0) > 0);
    if (!shouldLoad || !currentTask) {
      setFailedCleanBatches([]);
      return;
    }
    api
      .getFailedCleanBatches(currentTask.id)
      .then(result => {
        if (!cancelled) setFailedCleanBatches(result.batches);
      })
      .catch(() => {
        if (!cancelled) setFailedCleanBatches([]);
      });
    return () => {
      cancelled = true;
    };
  }, [currentTask]);

  const compactSteps = useMemo(() => {
    const find = (id: string) => processSteps.find(step => step.id === id);
    const combine = (...ids: string[]): ProcessStep => {
      const values = ids.map(find).filter(Boolean) as ProcessStep[];
      const failed = values.find(step => step.status === 'failed');
      const running = values.find(step => step.status === 'running' || step.status === 'paused');
      const allDone =
        values.length > 0 &&
        (ids.includes('export')
          ? values.some(step => step.status === 'success')
          : values.every(step => step.status === 'success'));
      const interrupted = values.find(step => ['cancelled', 'partial'].includes(step.status));
      return (
        failed ||
        running ||
        interrupted || {
          ...(values.at(-1) || emptyProcess()[0]),
          status: allDone ? 'success' : 'waiting',
          progress: allDone ? 100 : 0,
        }
      );
    };
    const media = combine('download', 'extract_audio');
    const mediaState =
      media.status === 'waiting' && find('download')?.status === 'success'
        ? { ...media, status: 'success' as TaskStepStatus, progress: 100 }
        : media;
    return [
      { id: 'download', label: activeProjectIsYoutube ? '下载' : '媒体', icon: '⇩', state: mediaState },
      { id: 'transcribe', label: '转写', icon: '⌁', state: combine('transcribe') },
      { id: 'clean', label: '整理', icon: '✦', state: combine('clean') },
      { id: 'translate', label: '翻译', icon: '文', state: combine('translate') },
      { id: 'export', label: '导出', icon: '↗', state: combine('export', 'render') },
    ];
  }, [activeProjectIsYoutube, processSteps]);

  const totalProgress = Math.round(
    (processSteps.reduce((sum, s) => {
      const weights: Record<TaskStepStatus, number> = {
        waiting: 0,
        skipped: 0,
        running: s.progress * 0.01,
        paused: s.progress * 0.01,
        success: 1,
        failed: 1,
        cancelled: 0,
        partial: 0.5,
      };
      return sum + (weights[s.status] || 0);
    }, 0) /
      processSteps.length) *
      100,
  );

  return {
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
  };
}
