import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import * as api from '../api/backend';
import {
  clearRecoveredSegmentDraft,
  readRecoveredSegmentDraft,
  writeRecoveredSegmentDraft,
} from '../draftRecovery';
import { loadSubtitleStyle, saveSubtitleStyle } from '../subtitleStyle';
import type {
  AppSettings,
  Project,
  SegmentOperationRequest,
  SegmentUpdate,
  SubtitleSegment,
  SubtitleStyleSettings,
} from '../types';

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
  const [draftItems, setDraftItems] = useState<Record<number, SegmentUpdate>>({});
  const [draftIsStale, setDraftIsStale] = useState(false);
  const [editorSaveState, setEditorSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [subtitleStyle, setSubtitleStyle] = useState<SubtitleStyleSettings>(loadSubtitleStyle);

  const editorRevision = useRef(0);
  const editorQueue = useRef<Promise<unknown>>(Promise.resolve());
  const editorWriteFailed = useRef(false);
  const draftWriteQueue = useRef<Promise<unknown>>(Promise.resolve());
  const draftWriteGeneration = useRef<Record<string, number>>({});
  const draftMutationLock = useRef(false);
  const draftMutationPromise = useRef<Promise<void>>(Promise.resolve());
  const draftItemsRef = useRef<Record<number, SegmentUpdate>>({});
  const draftBaseRevisionRef = useRef<number | null>(null);
  const styleSaveTimer = useRef<number | null>(null);

  const waitForPendingWrites = useCallback(async () => {
    await Promise.all([
      editorQueue.current,
      draftWriteQueue.current,
      draftMutationPromise.current,
    ]);
  }, []);

  const resetDraftState = useCallback((nextRevision = 0) => {
    editorRevision.current = nextRevision;
    draftItemsRef.current = {};
    draftBaseRevisionRef.current = null;
    setDraftItems({});
    setDraftIsStale(false);
    setEditorSaveState('idle');
  }, []);

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

  // Recover unsaved segment draft on project activation.
  useEffect(() => {
    if (!activeProject?.id) return;
    const projectId = activeProject.id;
    let cancelled = false;
    void api
      .getSegmentDraft(projectId)
      .then(async ({ draft }) => {
        if (cancelled || activeProjectIdRef.current !== projectId) return;
        const localDraft = readRecoveredSegmentDraft(projectId);
        if (draft?.invalid && !localDraft) {
          draftItemsRef.current = { 0: {} };
          draftBaseRevisionRef.current = draft.base_revision;
          setDraftItems({ 0: {} });
          setDraftIsStale(true);
          setEditorSaveState('error');
          setToast('字幕草稿文件已损坏；正式字幕没有改变，请放弃该草稿后重新编辑');
          return;
        }
        const backendUpdatedAt = draft?.updated_at
          ? Date.parse(draft.updated_at.replace(' ', 'T')) || 0
          : 0;
        const useLocal =
          !!localDraft &&
          (!!draft?.invalid || !draft?.items.length || localDraft.updatedAt > backendUpdatedAt);
        if (localDraft && !useLocal) clearRecoveredSegmentDraft(projectId);
        const baseRevision = useLocal ? localDraft.baseRevision : draft?.base_revision;
        const next = useLocal
          ? localDraft.items
          : Object.fromEntries(
              (draft?.items || []).map(item => {
                const { index, ...data } = item;
                return [index, data];
              }),
            );
        if (!Object.keys(next).length || baseRevision === undefined) return;
        draftItemsRef.current = next;
        draftBaseRevisionRef.current = baseRevision;
        setDraftItems(next);
        if (useLocal) {
          const generation = (draftWriteGeneration.current[projectId] || 0) + 1;
          draftWriteGeneration.current[projectId] = generation;
          const persist = () =>
            api.saveSegmentDraft(
              projectId,
              baseRevision,
              Object.entries(next).map(([index, data]) => ({ index: Number(index), ...data })),
            );
          const queued = draftWriteQueue.current.then(persist, persist);
          draftWriteQueue.current = queued.then(() => undefined, () => undefined);
          try {
            await queued;
            if (draftWriteGeneration.current[projectId] === generation) {
              clearRecoveredSegmentDraft(projectId);
            }
          } catch {
            // Keep the local recovery copy until the sidecar accepts it.
          }
        }
        if (cancelled || activeProjectIdRef.current !== projectId) return;
        if (baseRevision !== editorRevision.current) {
          setDraftIsStale(true);
          setEditorSaveState('error');
          setToast(
            useLocal
              ? '检测到异常退出前的旧字幕草稿；已保留但未自动套用，请确认后恢复或放弃'
              : '检测到旧版字幕草稿；已保留但未自动套用，请确认后恢复或放弃',
          );
          return;
        }
        setDraftIsStale(false);
        setSegments(current =>
          current.map(segment => ({ ...segment, ...(next[segment.index] || {}) })),
        );
        setEditorSaveState('saved');
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [activeProject?.id, activeProjectIdRef, setToast]);

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
      if (Object.keys(draftItemsRef.current).length > 0) {
        setDraftIsStale(true);
        setEditorSaveState('error');
      } else {
        setEditorSaveState('saved');
      }
    },
    [activeProjectIdRef, setActiveProject, setProjects],
  );

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
      if (draftMutationLock.current) {
        setToast('正在保存或放弃草稿，请稍候');
        return;
      }
      if (draftIsStale) {
        setToast('旧草稿与当前字幕版本不同；请先预览并确认恢复，或放弃旧草稿');
        return;
      }
      const projectId = activeProject.id;
      const manualDraft =
        appSettings.auto_save === false || Object.keys(draftItemsRef.current).length > 0;
      if (manualDraft) {
        const next = {
          ...draftItemsRef.current,
          [idx]: { ...(draftItemsRef.current[idx] || {}), ...data },
        };
        const baseRevision = draftBaseRevisionRef.current ?? editorRevision.current;
        const generation = (draftWriteGeneration.current[projectId] || 0) + 1;
        draftWriteGeneration.current[projectId] = generation;
        draftBaseRevisionRef.current = baseRevision;
        draftItemsRef.current = next;
        if (!writeRecoveredSegmentDraft(projectId, baseRevision, next)) {
          addLog('warning', '编辑', '浏览器恢复副本写入失败；仍会继续写入本机数据库');
        }
        setDraftItems(next);
        setSegments(current =>
          current.map(segment => (segment.index === idx ? { ...segment, ...data } : segment)),
        );
        setEditorSaveState('saving');
        const persist = async () => {
          await api.saveSegmentDraft(
            projectId,
            baseRevision,
            Object.entries(next).map(([index, value]) => ({ index: Number(index), ...value })),
          );
        };
        const queued = draftWriteQueue.current.then(persist, persist);
        draftWriteQueue.current = queued.then(() => undefined, () => undefined);
        try {
          await queued;
          if (draftWriteGeneration.current[projectId] === generation) {
            clearRecoveredSegmentDraft(projectId);
            if (activeProjectIdRef.current === projectId) setEditorSaveState('saved');
          }
        } catch (error: any) {
          if (activeProjectIdRef.current === projectId) setEditorSaveState('error');
          addLog('error', '编辑', `草稿保存失败: ${error.message}`);
        }
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
      activeProjectIdRef,
      addLog,
      appSettings.auto_save,
      draftIsStale,
      runEditorOperation,
      setToast,
    ],
  );

  const commitDraft = useCallback(async () => {
    if (!activeProject || !Object.keys(draftItemsRef.current).length || draftMutationLock.current) {
      return;
    }
    const projectId = activeProject.id;
    draftMutationLock.current = true;
    let releaseMutation: () => void = () => {};
    draftMutationPromise.current = new Promise(resolve => {
      releaseMutation = resolve;
    });
    setEditorSaveState('saving');
    try {
      if (draftItemsRef.current[0]) {
        throw new Error('损坏的草稿不能保存，请放弃后重新编辑');
      }
      if (
        draftIsStale &&
        !window.confirm(
          '当前正式字幕已在草稿保存后发生变化。\n\n确认把这份旧草稿按行号应用到当前字幕吗？此操作可撤销。',
        )
      ) {
        setEditorSaveState('error');
        return;
      }
      await Promise.all([editorQueue.current, draftWriteQueue.current]);
      if (activeProjectIdRef.current !== projectId) return;
      const recovered = readRecoveredSegmentDraft(projectId);
      if (recovered) {
        await api.saveSegmentDraft(
          projectId,
          recovered.baseRevision,
          Object.entries(recovered.items).map(([index, data]) => ({
            index: Number(index),
            ...data,
          })),
        );
        clearRecoveredSegmentDraft(projectId);
      }
      if (activeProjectIdRef.current !== projectId) return;
      const result = draftIsStale
        ? await api.rebaseSegmentDraft(projectId)
        : await api.commitSegmentDraft(projectId);
      clearRecoveredSegmentDraft(projectId);
      if (activeProjectIdRef.current !== projectId) return;
      draftItemsRef.current = {};
      draftBaseRevisionRef.current = null;
      setDraftItems({});
      setDraftIsStale(false);
      acceptEditorResult(projectId, result);
      setToast('字幕草稿已保存');
    } catch (error: any) {
      if (activeProjectIdRef.current === projectId) {
        setEditorSaveState('error');
        if (error?.code === 'EDIT_REVISION_CONFLICT') {
          setDraftIsStale(true);
          await refreshActiveProject(projectId);
          setToast('正式字幕已发生变化；草稿仍安全保留，请预览后确认恢复或放弃');
        } else {
          setToast(error.message);
        }
      }
    } finally {
      draftMutationLock.current = false;
      releaseMutation();
    }
  }, [
    acceptEditorResult,
    activeProject,
    activeProjectIdRef,
    draftIsStale,
    refreshActiveProject,
    setToast,
  ]);

  const discardDraft = useCallback(async () => {
    if (!activeProject || draftMutationLock.current) return;
    const projectId = activeProject.id;
    draftMutationLock.current = true;
    let releaseMutation: () => void = () => {};
    draftMutationPromise.current = new Promise(resolve => {
      releaseMutation = resolve;
    });
    try {
      await Promise.all([editorQueue.current, draftWriteQueue.current]);
      if (activeProjectIdRef.current !== projectId) return;
      await api.discardSegmentDraft(projectId);
      clearRecoveredSegmentDraft(projectId);
      if (activeProjectIdRef.current !== projectId) return;
      draftItemsRef.current = {};
      draftBaseRevisionRef.current = null;
      setDraftItems({});
      setDraftIsStale(false);
      await refreshSegments(projectId);
      setEditorSaveState('idle');
      setToast('字幕草稿已放弃');
    } catch (error: any) {
      if (activeProjectIdRef.current === projectId) {
        setEditorSaveState('error');
        setToast(error.message);
      }
    } finally {
      draftMutationLock.current = false;
      releaseMutation();
    }
  }, [activeProject, activeProjectIdRef, refreshSegments, setToast]);

  const previewDraft = useCallback(() => {
    if (!activeProject || !Object.keys(draftItemsRef.current).length) return;
    if (draftItemsRef.current[0]) {
      setToast('损坏的草稿无法预览；正式字幕未改变，可以安全放弃该草稿');
      return;
    }
    setSegments(current =>
      current.map(segment => ({
        ...segment,
        ...(draftItemsRef.current[segment.index] || {}),
      })),
    );
    setProjectWorkspace('subtitles');
    setShowProjectWorkspace(true);
    setToast(
      draftIsStale
        ? '正在预览旧草稿；数据库尚未改变，确认恢复后才会写入'
        : '正在预览本机草稿',
    );
  }, [activeProject, draftIsStale, setProjectWorkspace, setShowProjectWorkspace, setToast]);

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

  const undoEditor = useCallback(async () => {
    if (!activeProject) return;
    if (draftMutationLock.current) {
      setToast('正在保存或放弃草稿，请稍候');
      return;
    }
    const projectId = activeProject.id;
    const execute = async () => {
      if (activeProjectIdRef.current !== projectId) return;
      const result = await api.undoEditorOperation(projectId, editorRevision.current);
      acceptEditorResult(projectId, result);
    };
    const queued = editorQueue.current.then(execute, execute);
    editorQueue.current = queued.then(() => undefined, () => undefined);
    try {
      await queued;
    } catch (error: any) {
      if (activeProjectIdRef.current === projectId) setToast(error.message);
    }
  }, [acceptEditorResult, activeProject, activeProjectIdRef, setToast]);

  const redoEditor = useCallback(async () => {
    if (!activeProject) return;
    if (draftMutationLock.current) {
      setToast('正在保存或放弃草稿，请稍候');
      return;
    }
    const projectId = activeProject.id;
    const execute = async () => {
      if (activeProjectIdRef.current !== projectId) return;
      const result = await api.redoEditorOperation(projectId, editorRevision.current);
      acceptEditorResult(projectId, result);
    };
    const queued = editorQueue.current.then(execute, execute);
    editorQueue.current = queued.then(() => undefined, () => undefined);
    try {
      await queued;
    } catch (error: any) {
      if (activeProjectIdRef.current === projectId) setToast(error.message);
    }
  }, [acceptEditorResult, activeProject, activeProjectIdRef, setToast]);

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
    draftItems,
    draftItemsRef,
    draftIsStale,
    editorSaveState,
    editorRevision,
    editorQueue,
    editorWriteFailed,
    subtitleStyle,
    setSubtitleStyle,
    waitForPendingWrites,
    resetDraftState,
    refreshSegments,
    refreshActiveProject,
    acceptEditorResult,
    handleUpdateSegment,
    commitDraft,
    discardDraft,
    previewDraft,
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
