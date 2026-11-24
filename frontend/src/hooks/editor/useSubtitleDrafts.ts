import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import * as api from '../../api/backend';
import {
  clearRecoveredSegmentDraft,
  readRecoveredSegmentDraft,
  writeRecoveredSegmentDraft,
} from '../../draftRecovery';
import type { Project, SegmentUpdate, SubtitleSegment } from '../../types';

interface UseSubtitleDraftsOptions {
  activeProject: Project | null;
  activeProjectIdRef: MutableRefObject<string | null>;
  editorRevision: MutableRefObject<number>;
  editorQueue: MutableRefObject<Promise<unknown>>;
  setSegments: Dispatch<SetStateAction<SubtitleSegment[]>>;
  setEditorSaveState: Dispatch<SetStateAction<'idle' | 'saving' | 'saved' | 'error'>>;
  setProjectWorkspace: Dispatch<SetStateAction<'subtitles' | 'style' | 'export'>>;
  setShowProjectWorkspace: Dispatch<SetStateAction<boolean>>;
  refreshSegments: (projectId: string) => Promise<void>;
  refreshActiveProject: (projectId: string) => Promise<void>;
  acceptEditorResult: (
    projectId: string,
    result: Awaited<ReturnType<typeof api.applySegmentOperation>>,
  ) => void;
  addLog: (
    level: 'info' | 'warning' | 'error',
    step: string,
    message: string,
    detail?: string,
    suggestion?: string,
  ) => void;
  setToast: Dispatch<SetStateAction<string>>;
}

export function useSubtitleDrafts({
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
}: UseSubtitleDraftsOptions) {
  const [draftItems, setDraftItems] = useState<Record<number, SegmentUpdate>>({});
  const [draftIsStale, setDraftIsStale] = useState(false);

  const draftWriteQueue = useRef<Promise<unknown>>(Promise.resolve());
  const draftWriteGeneration = useRef<Record<string, number>>({});
  const draftMutationLock = useRef(false);
  const draftMutationPromise = useRef<Promise<void>>(Promise.resolve());
  const draftItemsRef = useRef<Record<number, SegmentUpdate>>({});
  const draftBaseRevisionRef = useRef<number | null>(null);

  const resetDraftState = useCallback(
    (nextRevision = 0) => {
      editorRevision.current = nextRevision;
      draftItemsRef.current = {};
      draftBaseRevisionRef.current = null;
      setDraftItems({});
      setDraftIsStale(false);
      setEditorSaveState('idle');
    },
    [editorRevision, setEditorSaveState],
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
  }, [activeProject?.id, activeProjectIdRef, editorRevision, setEditorSaveState, setSegments, setToast]);

  const saveManualDraftUpdate = useCallback(
    async (projectId: string, idx: number, data: SegmentUpdate) => {
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
    },
    [activeProjectIdRef, addLog, editorRevision, setEditorSaveState, setSegments],
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
    editorQueue,
    refreshActiveProject,
    setEditorSaveState,
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
  }, [activeProject, activeProjectIdRef, editorQueue, refreshSegments, setEditorSaveState, setToast]);

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
  }, [activeProject, draftIsStale, setProjectWorkspace, setSegments, setShowProjectWorkspace, setToast]);

  return {
    draftItems,
    draftItemsRef,
    draftIsStale,
    setDraftIsStale,
    draftMutationLock,
    draftWriteQueue,
    draftMutationPromise,
    resetDraftState,
    saveManualDraftUpdate,
    commitDraft,
    discardDraft,
    previewDraft,
  };
}
