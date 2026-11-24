import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type RefObject,
  type SetStateAction,
} from 'react';
import * as api from '../api/backend';
import type { SubtitlePlayerHandle } from '../components/SubtitlePlayer';
import { emptyProcess } from '../processSteps';
import { loadSubtitleStyle } from '../subtitleStyle';
import type {
  AppSettings,
  ProcessStep,
  Project,
  SegmentSearchHit,
  SubtitleSegment,
  SubtitleStyleSettings,
  TaskStatus,
} from '../types';

interface UseProjectSessionOptions {
  backendStatus: 'connecting' | 'connected' | 'error';
  appSettings: AppSettings;
  projects: Project[];
  activeProject: Project | null;
  activeProjectIdRef: MutableRefObject<string | null>;
  segments: SubtitleSegment[];
  videoPlayerRef: RefObject<SubtitlePlayerHandle | null>;
  setActiveProject: Dispatch<SetStateAction<Project | null>>;
  setSegments: Dispatch<SetStateAction<SubtitleSegment[]>>;
  setSubtitleStyle: Dispatch<SetStateAction<SubtitleStyleSettings>>;
  setShowProjectWorkspace: Dispatch<SetStateAction<boolean>>;
  setProjectWorkspace: Dispatch<SetStateAction<'subtitles' | 'style' | 'export'>>;
  setToolsOpen: Dispatch<SetStateAction<boolean>>;
  setSegmentInspector: Dispatch<SetStateAction<number | null>>;
  setInspectorMode: Dispatch<SetStateAction<'style' | 'step' | null>>;
  setProcessSteps: Dispatch<SetStateAction<ProcessStep[]>>;
  waitForPendingWrites: () => Promise<void>;
  resetDraftState: (nextRevision?: number) => void;
  resetTaskTracking: () => void;
  refreshProcessSteps: (project: Project | null) => void;
  refreshSegments: (projectId: string) => Promise<void>;
  attachTask: (task: TaskStatus, interval?: number) => void;
  addLog: (
    level: 'info' | 'warning' | 'error',
    step: string,
    message: string,
    detail?: string,
    suggestion?: string,
  ) => void;
  setToast: Dispatch<SetStateAction<string>>;
}

export function useProjectSession({
  backendStatus,
  appSettings,
  projects,
  activeProject,
  activeProjectIdRef,
  segments,
  videoPlayerRef,
  setActiveProject,
  setSegments,
  setSubtitleStyle,
  setShowProjectWorkspace,
  setProjectWorkspace,
  setToolsOpen,
  setSegmentInspector,
  setInspectorMode,
  setProcessSteps,
  waitForPendingWrites,
  resetDraftState,
  resetTaskTracking,
  refreshProcessSteps,
  refreshSegments,
  attachTask,
  addLog,
  setToast,
}: UseProjectSessionOptions) {
  const [currentTime, setCurrentTime] = useState(0);
  const [videoDuration, setVideoDuration] = useState(0);
  const [activeSegmentIdx, setActiveSegmentIdx] = useState(-1);
  const [subtitleFocusRequest, setSubtitleFocusRequest] = useState(0);

  const restoredStartupProject = useRef(false);
  const projectSelectionIntent = useRef(0);
  const pendingSearchJump = useRef<SegmentSearchHit | null>(null);
  const playheadRef = useRef<{ segments: SubtitleSegment[]; time: number }>({
    segments: [],
    time: 0,
  });

  useEffect(() => {
    playheadRef.current = { segments, time: currentTime };
  }, [segments, currentTime]);

  const selectProject = useCallback(
    async (p: Project, requestedIntent?: number) => {
      const selectionIntent = requestedIntent ?? ++projectSelectionIntent.current;
      if (selectionIntent !== projectSelectionIntent.current) return;
      await waitForPendingWrites();
      if (selectionIntent !== projectSelectionIntent.current) return;
      const switchingProject = activeProjectIdRef.current !== p.id;
      activeProjectIdRef.current = p.id;
      setActiveProject(p);
      resetDraftState(Number(p.edit_revision || 0));
      setShowProjectWorkspace(true);
      setProjectWorkspace('subtitles');
      setToolsOpen(false);
      setSegmentInspector(null);
      setSegments([]);
      localStorage.setItem('subtitle_factory_last_project_id', p.id);
      resetTaskTracking();
      setActiveSegmentIdx(-1);
      if (switchingProject) {
        setCurrentTime(0);
        setVideoDuration(0);
      }
      refreshProcessSteps(p);
      await Promise.all([
        refreshSegments(p.id),
        api
          .getProjectStyle(p.id)
          .then(result => {
            if (activeProjectIdRef.current !== p.id) return;
            setSubtitleStyle(
              result.settings
                ? ({ ...loadSubtitleStyle(), ...result.settings } as SubtitleStyleSettings)
                : loadSubtitleStyle(),
            );
          })
          .catch(() => {
            if (activeProjectIdRef.current === p.id) setSubtitleStyle(loadSubtitleStyle());
          }),
      ]);
      try {
        const latestTask = await api.getLatestProjectTask(p.id);
        if (activeProjectIdRef.current !== p.id) return;
        if (latestTask) attachTask(latestTask, 1000);
      } catch {
        /* projects created by older builds may not have task history */
      }
      if (activeProjectIdRef.current === p.id) addLog('info', '项目', `打开项目: ${p.title}`);
    },
    [
      activeProjectIdRef,
      addLog,
      attachTask,
      refreshProcessSteps,
      refreshSegments,
      resetDraftState,
      resetTaskTracking,
      setActiveProject,
      setProjectWorkspace,
      setSegmentInspector,
      setSegments,
      setShowProjectWorkspace,
      setSubtitleStyle,
      setToolsOpen,
      waitForPendingWrites,
    ],
  );

  const selectProjectById = useCallback(
    async (projectId: string) => {
      const selectionIntent = ++projectSelectionIntent.current;
      try {
        const project = await api.getProject(projectId);
        if (selectionIntent !== projectSelectionIntent.current) return;
        await selectProject(project, selectionIntent);
      } catch (error) {
        if (selectionIntent === projectSelectionIntent.current) {
          setToast(error instanceof Error ? error.message : String(error));
        }
      }
    },
    [selectProject, setToast],
  );

  const resetActiveProject = useCallback(() => {
    projectSelectionIntent.current += 1;
    activeProjectIdRef.current = null;
    setActiveProject(null);
    setSegments([]);
    resetDraftState(0);
    resetTaskTracking();
    setProcessSteps(emptyProcess());
    setInspectorMode(null);
    setShowProjectWorkspace(false);
    setProjectWorkspace('subtitles');
  }, [
    activeProjectIdRef,
    resetDraftState,
    resetTaskTracking,
    setActiveProject,
    setInspectorMode,
    setProcessSteps,
    setProjectWorkspace,
    setSegments,
    setShowProjectWorkspace,
  ]);

  useEffect(() => {
    if (restoredStartupProject.current || backendStatus !== 'connected' || !projects.length) return;
    if (activeProject) {
      restoredStartupProject.current = true;
      return;
    }
    restoredStartupProject.current = true;
    if (appSettings.startup_behavior !== 'restore_last') return;
    const lastId = localStorage.getItem('subtitle_factory_last_project_id');
    const project = projects.find(item => item.id === lastId);
    if (project) void selectProject(project);
  }, [activeProject, appSettings.startup_behavior, backendStatus, projects, selectProject]);

  const handleTimeUpdate = useCallback(
    (time: number) => {
      setCurrentTime(time);
      setActiveSegmentIdx(segments.findLastIndex(s => s.start <= time && s.end >= time));
    },
    [segments],
  );

  const handleSeek = useCallback(
    (time: number) => {
      setCurrentTime(time);
      setActiveSegmentIdx(segments.findLastIndex(segment => segment.start <= time && segment.end >= time));
      videoPlayerRef.current?.seekTo(time);
    },
    [segments, videoPlayerRef],
  );

  const openLibrarySearchHit = useCallback(
    async (hit: SegmentSearchHit) => {
      const selectionIntent = ++projectSelectionIntent.current;
      try {
        pendingSearchJump.current = hit;
        const project =
          projects.find(item => item.id === hit.project_id) ||
          (await api.getProject(hit.project_id));
        if (selectionIntent !== projectSelectionIntent.current) return;
        await selectProject(project, selectionIntent);
      } catch (error) {
        pendingSearchJump.current = null;
        setToast(error instanceof Error ? error.message : String(error));
      }
    },
    [projects, selectProject, setToast],
  );

  useEffect(() => {
    const hit = pendingSearchJump.current;
    if (!hit || activeProject?.id !== hit.project_id) return;
    if (
      !segments.some(
        segment => segment.id === hit.segment_id || segment.index === hit.segment_index,
      )
    ) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      setProjectWorkspace('subtitles');
      setShowProjectWorkspace(true);
      setActiveSegmentIdx(
        Math.max(
          0,
          segments.findIndex(segment => segment.id === hit.segment_id),
        ),
      );
      handleSeek(hit.start);
      setSubtitleFocusRequest(request => request + 1);
      pendingSearchJump.current = null;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeProject?.id, handleSeek, segments, setProjectWorkspace, setShowProjectWorkspace]);

  const previewClipRange = useCallback(
    (start: number, end: number) => {
      setProjectWorkspace('subtitles');
      setShowProjectWorkspace(true);
      window.requestAnimationFrame(() => videoPlayerRef.current?.previewRange(start, end));
    },
    [setProjectWorkspace, setShowProjectWorkspace, videoPlayerRef],
  );

  return {
    currentTime,
    videoDuration,
    setVideoDuration,
    activeSegmentIdx,
    subtitleFocusRequest,
    setSubtitleFocusRequest,
    playheadRef,
    selectProject,
    selectProjectById,
    resetActiveProject,
    handleTimeUpdate,
    handleSeek,
    openLibrarySearchHit,
    previewClipRange,
  };
}
