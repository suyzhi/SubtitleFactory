import {
  useCallback,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import * as api from '../api/backend';
import { loadSubtitleStyle, saveSubtitleStyle } from '../subtitleStyle';
import type {
  AppSettings,
  Project,
  SegmentOperationRequest,
  SegmentUpdate,
  SubtitleSegment,
  SubtitleStyleSettings,
} from '../types';
import { useSubtitleDrafts } from './editor/useSubtitleDrafts';

interface UseSubtitleEditorOptions {
  activeProject: Project | null;
  activeProjectIdRef: MutableRefObject<string | null>;
  appSettings: AppSettings;
  setProjects: Dispatch<SetStateAction<Project[]>>;
  setActiveProject: Dispatch<SetStateAction<Project | null>>;
  setProjectWorkspace: Dispatch<SetStateAction<'subtitles' | 'style' | 'export'>>;
  setShowProjectWorkspace: Dispatch<SetStateAction<boolean>>;
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

export function useSubtitleEditor({
  activeProject,
  activeProjectIdRef,
  appSettings,
  setProjects,
  setActiveProject,
  setProjectWorkspace,
  setShowProjectWorkspace,
  addLog,
  setToast,
  showToast,
}: UseSubtitleEditorOptions) {
  const [segments, setSegments] = useState<SubtitleSegment[]>([]);
  const [segmentsLoading, setSegmentsLoading] = useState(false);
  const [segmentsError, setSegmentsError] = useState('');
  const [editorSaveState, setEditorSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>(
    'idle',
  );
  const [subtitleStyle, setSubtitleStyle] = useState<SubtitleStyleSettings>(loadSubtitleStyle);

  const editorRevision = useRef(0);
  const editorQueue = useRef<Promise<unknown>>(Promise.resolve());
  const editorWriteFailed = useRef(false);
  const styleSaveTimer = useRef<number | null>(null);
  const draftItemsMirrorRef = useRef<Record<number, SegmentUpdate>>({});
  const setDraftIsStaleMirrorRef = useRef<Dispatch<SetStateAction<boolean>>>(() => undefined);

  const refreshSegments = useCallback(
    async (projectId: string) => {
      if (activeProjectIdRef.current === projectId) {
        setSegmentsLoading(true);
        setSegmentsError('');
      }
      try {
        const result = await api.getSegments(projectId);
        if (activeProjectIdRef.current === projectId) setSegments(result.segments);
      } catch (error) {
        if (activeProjectIdRef.current === projectId) {
          setSegmentsError(error instanceof Error ? error.message : '字幕加载失败');
        }
      } finally {
        if (activeProjectIdRef.current === projectId) setSegmentsLoading(false);
      }
    },
    [activeProjectIdRef],
  );

  const refreshActiveProject = useCallback(
    async (projectId: string) => {
      try {
        const project = await api.getProject(projectId);
        if (activeProjectIdRef.current !== projectId) return;
        setActiveProject(project);
        editorRevision.current = Number(project.edit_revision || 0);
        await refreshSegments(projectId);
      } catch {
        /* the initiating panel already reports its own operation error */
      }
    },
    [activeProjectIdRef, refreshSegments, setActiveProject],
  );

  const acceptEditorResult = useCallback(
    (
      projectId: string,
      result: Awaited<ReturnType<typeof api.applySegmentOperation>>,
    ) => {
      setProjects(current =>
        current.map(project =>
          project.id === projectId
            ? { ...project, edit_revision: result.revision, segments_count: result.segments.length }
            : project,
        ),
      );
      if (activeProjectIdRef.current !== projectId) return;
      editorRevision.current = result.revision;
      setSegments(result.segments);
      setActiveProject(current =>
        current?.id === projectId
          ? { ...current, edit_revision: result.revision, segments_count: result.segments.length }
          : current,
      );
      if (Object.keys(draftItemsMirrorRef.current).length > 0) {
        setDraftIsStaleMirrorRef.current(true);
        setEditorSaveState('error');
      } else {
        setEditorSaveState('saved');
      }
    },
    [activeProjectIdRef, setActiveProject, setProjects],
  );

  const drafts = useSubtitleDrafts({
    activeProject,
    activeProjectIdRef,
    editorRevision,
    editorQueue,
    setSegments,
    setEditorSaveState,
    setProjectWorkspace,
    setShowProjectWorkspace,
    refreshSegments,
    refreshActiveProject,
    acceptEditorResult,
    addLog,
    setToast,
  });

  draftItemsMirrorRef.current = drafts.draftItemsRef.current;
  setDraftIsStaleMirrorRef.current = drafts.setDraftIsStale;

  const waitForPendingWrites = useCallback(async () => {
    await Promise.all([
      editorQueue.current,
      drafts.draftWriteQueue.current,
      drafts.draftMutationPromise.current,
    ]);
  }, [drafts.draftMutationPromise, drafts.draftWriteQueue]);

  const runEditorOperation = useCallback(
    (data: Omit<SegmentOperationRequest, 'expected_revision'>) => {
      if (!activeProject) return Promise.reject(new Error('请先选择项目'));
      const projectId = activeProject.id;
      const execute = async () => {
        editorWriteFailed.current = false;
        setEditorSaveState('saving');
        try {
          const result = await api.applySegmentOperation(projectId, {
            ...data,
            expected_revision: editorRevision.current,
          });
          acceptEditorResult(projectId, result);
          return result;
        } catch (error: any) {
          editorWriteFailed.current = true;
          if (activeProjectIdRef.current === projectId) setEditorSaveState('error');
          addLog('error', '编辑', `字幕操作失败: ${error.message}`);
          if (activeProjectIdRef.current === projectId) setToast(error.message || '字幕操作失败');
          if (error.code === 'EDIT_REVISION_CONFLICT') {
            const [project, latest] = await Promise.all([
              api.getProject(projectId),
              api.getSegments(projectId),
            ]);
            if (activeProjectIdRef.current !== projectId) throw error;
            editorRevision.current = Number(project.edit_revision || 0);
            setActiveProject(project);
            setSegments(latest.segments);
            setToast('字幕已刷新，请重新执行刚才的操作');
          }
          throw error;
        }
      };
      const queued = editorQueue.current.then(execute, execute);
      editorQueue.current = queued.then(() => undefined, () => undefined);
      return queued;
    },
    [acceptEditorResult, activeProject, activeProjectIdRef, addLog, setActiveProject, setToast],
  );

  const handleUpdateSegment = useCallback(
    async (idx: number, data: SegmentUpdate) => {
      if (!activeProject) return;
      if (drafts.draftMutationLock.current) {
        setToast('正在保存或放弃草稿，请稍候');
        return;
      }
      if (drafts.draftIsStale) {
        setToast('旧草稿与当前字幕版本不同；请先预览并确认恢复，或放弃旧草稿');
        return;
      }
      const projectId = activeProject.id;
      const manualDraft =
        appSettings.auto_save === false || Object.keys(drafts.draftItemsRef.current).length > 0;
      if (manualDraft) {
        await drafts.saveManualDraftUpdate(projectId, idx, data);
        return;
      }
      setSegments(current =>
        current.map(segment => (segment.index === idx ? { ...segment, ...data } : segment)),
      );
      await runEditorOperation({
        operation: 'update_many',
        items: [{ index: idx, ...data }],
        include_locked: data.locked !== undefined,
      }).catch(() => undefined);
    },
    [
      activeProject,
      appSettings.auto_save,
      drafts,
      runEditorOperation,
      setToast,
    ],
  );

  const replaceSegments = useCallback(
    async (
      search: string,
      replacement: string,
      fields: Array<'clean_text' | 'translated_text'>,
      options: { matchCase: boolean; includeLocked: boolean },
    ) => {
      await runEditorOperation({
        operation: 'replace',
        search,
        replacement,
        fields,
        match_case: options.matchCase,
        include_locked: options.includeLocked,
      });
    },
    [runEditorOperation],
  );

  const splitSegment = useCallback(
    async (index: number, splitAt: number) => {
      await runEditorOperation({ operation: 'split', split_index: index, split_at: splitAt });
    },
    [runEditorOperation],
  );

  const mergeSegments = useCallback(
    async (indices: number[]) => {
      await runEditorOperation({ operation: 'merge', indices });
    },
    [runEditorOperation],
  );

  const deleteSegments = useCallback(
    async (indices: number[]) => {
      await runEditorOperation({ operation: 'delete', indices });
    },
    [runEditorOperation],
  );

  const runHistoryStep = useCallback(
    async (direction: 'undo' | 'redo') => {
      if (!activeProject) return;
      if (drafts.draftMutationLock.current) {
        setToast('正在保存或放弃草稿，请稍候');
        return;
      }
      const projectId = activeProject.id;
      const execute = async () => {
        if (activeProjectIdRef.current !== projectId) return;
        const result =
          direction === 'undo'
            ? await api.undoEditorOperation(projectId, editorRevision.current)
            : await api.redoEditorOperation(projectId, editorRevision.current);
        acceptEditorResult(projectId, result);
      };
      const queued = editorQueue.current.then(execute, execute);
      editorQueue.current = queued.then(() => undefined, () => undefined);
      try {
        await queued;
      } catch (error: any) {
        if (activeProjectIdRef.current === projectId) setToast(error.message);
      }
    },
    [acceptEditorResult, activeProject, activeProjectIdRef, drafts.draftMutationLock, setToast],
  );

  const undoEditor = useCallback(() => runHistoryStep('undo'), [runHistoryStep]);
  const redoEditor = useCallback(() => runHistoryStep('redo'), [runHistoryStep]);

  const importSubtitleFile = useCallback(() => {
    if (!activeProject) return;
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.srt,.vtt,.ass';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        const result = await api.importSubtitleFile(activeProject.id, file, editorRevision.current);
        acceptEditorResult(activeProject.id, result);
        showToast(`已导入 ${result.affected_count} 条字幕，可使用撤销恢复`);
      } catch (error: any) {
        showToast(error.message, 4200);
      }
    };
    input.click();
  }, [acceptEditorResult, activeProject, showToast]);

  const handleStyleChange = useCallback(
    (style: SubtitleStyleSettings) => {
      setSubtitleStyle(style);
      saveSubtitleStyle(style);
      if (!activeProject) return;
      if (styleSaveTimer.current !== null) window.clearTimeout(styleSaveTimer.current);
      const projectId = activeProject.id;
      styleSaveTimer.current = window.setTimeout(() => {
        styleSaveTimer.current = null;
        void api
          .saveProjectStyle(projectId, style as unknown as Record<string, unknown>)
          .catch(error => {
            if (activeProjectIdRef.current !== projectId) return;
            setToast(`样式保存失败：${error.message}`);
            window.setTimeout(() => setToast(''), 3000);
          });
      }, 350);
    },
    [activeProject, activeProjectIdRef, setToast],
  );

  return {
    segments,
    setSegments,
    segmentsLoading,
    segmentsError,
    draftItems: drafts.draftItems,
    draftItemsRef: drafts.draftItemsRef,
    draftIsStale: drafts.draftIsStale,
    editorSaveState,
    editorRevision,
    editorQueue,
    editorWriteFailed,
    subtitleStyle,
    setSubtitleStyle,
    waitForPendingWrites,
    resetDraftState: drafts.resetDraftState,
    refreshSegments,
    refreshActiveProject,
    acceptEditorResult,
    handleUpdateSegment,
    commitDraft: drafts.commitDraft,
    discardDraft: drafts.discardDraft,
    previewDraft: drafts.previewDraft,
    replaceSegments,
    splitSegment,
    mergeSegments,
    deleteSegments,
    undoEditor,
    redoEditor,
    importSubtitleFile,
    handleStyleChange,
  };
}
