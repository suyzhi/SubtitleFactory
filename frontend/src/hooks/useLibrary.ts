import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MouseEvent as ReactMouseEvent,
  type SetStateAction,
} from 'react';
import * as api from '../api/backend';
import type { PlaylistBatchDetail, Project, SegmentSearchHit, TaskStatus } from '../types';

export interface ProjectContextMenuState {
  x: number;
  y: number;
  project: Project;
  trashed: boolean;
}

interface UseLibraryOptions {
  backendStatus: 'connecting' | 'connected' | 'error';
  youtubeEnabled: boolean;
  motionEnabled: boolean;
  showProjectWorkspace: boolean;
  projects: Project[];
  setProjects: Dispatch<SetStateAction<Project[]>>;
  libraryRefresh: number;
  setLibraryRefresh: Dispatch<SetStateAction<number>>;
  activeProject: Project | null;
  currentTask: TaskStatus | null;
  setActiveProject: Dispatch<SetStateAction<Project | null>>;
  waitForPendingWrites: () => Promise<void>;
  resetActiveProject: () => void;
  setToast: Dispatch<SetStateAction<string>>;
  showToast: (message: string, duration?: number) => void;
}

export function useLibrary({
  backendStatus,
  youtubeEnabled,
  motionEnabled,
  showProjectWorkspace,
  projects,
  setProjects,
  libraryRefresh,
  setLibraryRefresh,
  activeProject,
  currentTask,
  setActiveProject,
  waitForPendingWrites,
  resetActiveProject,
  setToast,
  showToast,
}: UseLibraryOptions) {
  const [trashProjects, setTrashProjects] = useState<Project[]>([]);
  const [libraryView, setLibraryView] = useState<'projects' | 'trash'>('projects');
  const [librarySearch, setLibrarySearch] = useState('');
  const [librarySearchHits, setLibrarySearchHits] = useState<SegmentSearchHit[]>([]);
  const [librarySearchTotal, setLibrarySearchTotal] = useState(0);
  const [librarySearchPage, setLibrarySearchPage] = useState(1);
  const [librarySearchLoading, setLibrarySearchLoading] = useState(false);
  const [librarySearchSelection, setLibrarySearchSelection] = useState(-1);
  const [librarySearchFacets, setLibrarySearchFacets] = useState<api.SegmentSearchFacets>({});
  const [librarySearchFilters, setLibrarySearchFilters] = useState<api.SegmentSearchFilters>({});
  const [libraryPage, setLibraryPage] = useState(1);
  const [libraryTotal, setLibraryTotal] = useState(0);
  const [libraryPages, setLibraryPages] = useState(1);
  const [libraryLoading, setLibraryLoading] = useState(false);
  // False until the first project list arrives, so a slow first load shows a skeleton instead of the empty-library welcome.
  const [libraryLoaded, setLibraryLoaded] = useState(false);
  const [libraryError, setLibraryError] = useState('');
  const [libraryStatus, setLibraryStatus] = useState('');
  const [librarySort, setLibrarySort] = useState('updated_desc');

  const [playlistBatches, setPlaylistBatches] = useState<PlaylistBatchDetail[]>([]);
  const [contextMenu, setContextMenu] = useState<ProjectContextMenuState | null>(null);
  const [renameProjectState, setRenameProjectState] = useState<Project | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [removingProjectIds, setRemovingProjectIds] = useState<Set<string>>(() => new Set());
  const [collapsedProjectGroups, setCollapsedProjectGroups] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem('subtitle_factory_collapsed_groups') || '[]'));
    } catch {
      return new Set();
    }
  });
  const [openPlaylistBatches, setOpenPlaylistBatches] = useState<Record<string, boolean>>(() => {
    try {
      const saved = localStorage.getItem('subtitle_factory_playlist_batches_open');
      if (saved) return JSON.parse(saved);
      const legacy: string[] = JSON.parse(
        localStorage.getItem('subtitle_factory_collapsed_playlist_batches') || '[]',
      );
      return Object.fromEntries(legacy.map(id => [id, false]));
    } catch {
      return {};
    }
  });
  const [groupEditorProjectId, setGroupEditorProjectId] = useState<string | null>(null);
  const [groupDraft, setGroupDraft] = useState('');

  const contextMenuRef = useRef<HTMLDivElement>(null);
  const contextMenuReturnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    localStorage.setItem('subtitle_factory_collapsed_groups', JSON.stringify([...collapsedProjectGroups]));
  }, [collapsedProjectGroups]);

  useEffect(() => {
    try {
      localStorage.setItem('subtitle_factory_playlist_batches_open', JSON.stringify(openPlaylistBatches));
    } catch {
      /* storage unavailable */
    }
  }, [openPlaylistBatches]);

  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      close();
      window.requestAnimationFrame(() => contextMenuReturnFocus.current?.focus());
    };
    const menuElement = contextMenuRef.current;
    const frame = window.requestAnimationFrame(() =>
      contextMenuRef.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus(),
    );
    window.addEventListener('pointerdown', close);
    window.addEventListener('keydown', escape);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('pointerdown', close);
      window.removeEventListener('keydown', escape);
      window.removeEventListener('resize', close);
      window.cancelAnimationFrame(frame);
      if (menuElement?.contains(document.activeElement)) {
        window.requestAnimationFrame(() => contextMenuReturnFocus.current?.focus());
      }
    };
  }, [contextMenu]);

  const refreshPlaylistBatches = useCallback(async () => {
    if (!youtubeEnabled) {
      setPlaylistBatches([]);
      return;
    }
    if (backendStatus !== 'connected') return;
    const result = await api.getPlaylistBatches();
    setPlaylistBatches(result.batches);
  }, [backendStatus, youtubeEnabled]);

  const refreshLibraries = useCallback(async () => {
    setLibraryRefresh(value => value + 1);
    const result = await api
      .getPlaylistBatches()
      .catch(() => ({ batches: [] as PlaylistBatchDetail[] }));
    setPlaylistBatches(result.batches);
  }, [setLibraryRefresh]);

  const libraryFirstLoad = backendStatus === 'connected' && !libraryLoaded && !libraryError;
  const hasActivePlaylistBatch = playlistBatches.some(
    ({ batch }) => batch.status === 'running' || batch.status === 'pending',
  );

  useEffect(() => {
    setLibraryPage(1);
  }, [librarySearch, librarySort, libraryStatus, libraryView]);

  useEffect(() => {
    if (backendStatus !== 'connected' || showProjectWorkspace) return;
    let cancelled = false;
    setLibraryLoading(true);
    setLibraryError('');
    const timer = window.setTimeout(() => {
      void api
        .listProjects({
          deleted: libraryView === 'trash',
          search: librarySearch.trim(),
          sort: librarySort,
          page_size: 40,
          page: libraryPage,
          readiness: libraryStatus,
        })
        .then(result => {
          if (cancelled) return;
          (libraryView === 'trash' ? setTrashProjects : setProjects)(result.projects);
          setLibraryTotal(result.total ?? result.projects.length);
          setLibraryPages(result.pages || 1);
          if (libraryPage > (result.pages || 1)) setLibraryPage(result.pages || 1);
          setLibraryLoaded(true);
        })
        .catch(error => {
          if (!cancelled) setLibraryError(error.message);
        })
        .finally(() => {
          if (!cancelled) setLibraryLoading(false);
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [
    backendStatus,
    librarySearch,
    librarySort,
    libraryStatus,
    libraryView,
    libraryPage,
    showProjectWorkspace,
    libraryRefresh,
    setProjects,
  ]);

  useEffect(() => {
    setLibrarySearchPage(1);
  }, [librarySearch, librarySearchFilters]);

  useEffect(() => {
    const query = librarySearch.trim();
    setLibrarySearchSelection(-1);
    if (backendStatus !== 'connected' || libraryView !== 'projects' || query.length < 2) {
      setLibrarySearchHits([]);
      setLibrarySearchTotal(0);
      setLibrarySearchLoading(false);
      if (!query) {
        setLibrarySearchFacets(current => (Object.keys(current).length ? {} : current));
        setLibrarySearchFilters(current => (Object.keys(current).length ? {} : current));
      }
      return;
    }
    let cancelled = false;
    setLibrarySearchLoading(true);
    const timer = window.setTimeout(() => {
      void api
        .searchSegments(query, { ...librarySearchFilters, page: librarySearchPage, page_size: 50 })
        .then(result => {
          if (cancelled) return;
          setLibrarySearchHits(result.hits);
          setLibrarySearchTotal(result.total);
          setLibrarySearchFacets(result.facets || {});
        })
        .catch(() => {
          if (!cancelled) {
            setLibrarySearchHits([]);
            setLibrarySearchTotal(0);
          }
        })
        .finally(() => {
          if (!cancelled) setLibrarySearchLoading(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [backendStatus, librarySearch, librarySearchFilters, librarySearchPage, libraryView]);

  useEffect(() => {
    if (!youtubeEnabled || backendStatus !== 'connected' || libraryView !== 'projects') return;
    void refreshPlaylistBatches().catch(() => undefined);
    const interval = hasActivePlaylistBatch ? 2000 : 30000;
    const timer = window.setInterval(
      () => void refreshPlaylistBatches().catch(() => undefined),
      interval,
    );
    return () => window.clearInterval(timer);
  }, [backendStatus, hasActivePlaylistBatch, libraryView, refreshPlaylistBatches, youtubeEnabled]);

  const projectGroups = useMemo(() => {
    const groups = new Map<
      string,
      { key: string; label: string; projects: Project[]; rank: number }
    >();
    for (const project of projects.slice(0, 40)) {
      const namedGroup = project.source_type === 'youtube' ? project.group_name?.trim() : '';
      const key =
        project.source_type === 'local'
          ? 'local'
          : namedGroup
            ? `youtube:named:${encodeURIComponent(namedGroup)}`
            : 'youtube:ungrouped';
      const label = project.source_type === 'local' ? '本地视频' : namedGroup || '未分组';
      const rank = project.source_type === 'local' ? 2 : namedGroup ? 0 : 1;
      const group = groups.get(key) || { key, label, projects: [], rank };
      group.projects.push(project);
      groups.set(key, group);
    }
    return [...groups.values()].sort(
      (left, right) => left.rank - right.rank || left.label.localeCompare(right.label, 'zh-CN'),
    );
  }, [projects]);

  const knownProjectGroups = useMemo(
    () =>
      Array.from(
        new Set(
          projects
            .filter(project => project.source_type === 'youtube' && project.group_name)
            .map(project => project.group_name as string),
        ),
      ).sort((left, right) => left.localeCompare(right, 'zh-CN')),
    [projects],
  );

  const toggleProjectGroup = useCallback((key: string) => {
    setCollapsedProjectGroups(current => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const togglePlaylistBatch = useCallback((id: string, open: boolean) => {
    setOpenPlaylistBatches(current => ({ ...current, [id]: open }));
  }, []);

  const openProjectGroupEditor = useCallback(
    (project: Project) => {
      if (groupEditorProjectId === project.id) {
        setGroupEditorProjectId(null);
        return;
      }
      setGroupDraft(project.group_name || '');
      setGroupEditorProjectId(project.id);
    },
    [groupEditorProjectId],
  );

  const saveProjectGroup = useCallback(
    async (project: Project) => {
      const normalized = groupDraft.trim();
      try {
        const updated = await api.updateProjectGroup(project.id, normalized || null);
        setProjects(current => current.map(item => (item.id === updated.id ? updated : item)));
        setActiveProject(current => (current?.id === updated.id ? updated : current));
        setGroupEditorProjectId(null);
        window.requestAnimationFrame(() =>
          document.getElementById(`project-group-action-${project.id}`)?.focus(),
        );
        setToast(normalized ? `已移至“${normalized}”` : '已移至“未分组”');
        window.setTimeout(() => setToast(''), 2400);
      } catch (error: any) {
        setToast(`分组保存失败：${error.message}`);
        window.setTimeout(() => setToast(''), 3200);
      }
    },
    [groupDraft, setActiveProject, setProjects, setToast],
  );

  const openProjectMenu = useCallback(
    (event: ReactMouseEvent, project: Project, trashed = false) => {
      event.preventDefault();
      event.stopPropagation();
      const menuWidth = 218;
      const menuHeight = trashed ? 132 : 190;
      const trigger =
        event.target instanceof HTMLElement ? event.target.closest<HTMLElement>('button') : null;
      contextMenuReturnFocus.current = trigger;
      const triggerRect = trigger?.getBoundingClientRect();
      const requestedX = event.clientX || triggerRect?.right || 10;
      const requestedY = event.clientY || triggerRect?.bottom || 10;
      setContextMenu({
        project,
        trashed,
        x: Math.min(requestedX, window.innerWidth - menuWidth - 10),
        y: Math.min(requestedY, window.innerHeight - menuHeight - 10),
      });
    },
    [],
  );

  const animateProjectRemoval = useCallback(
    async (projectIds: string[]) => {
      setRemovingProjectIds(current => new Set([...current, ...projectIds]));
      const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (motionEnabled && !reducedMotion) {
        await new Promise(resolve => window.setTimeout(resolve, 180));
      }
    },
    [motionEnabled],
  );

  const moveProjectToTrash = useCallback(
    async (project: Project) => {
      const activeTask =
        activeProject?.id === project.id &&
        !!currentTask &&
        ['pending', 'running', 'paused'].includes(currentTask.status);
      let terminate = false;
      if (activeTask) {
        terminate = window.confirm('这个项目正在处理。移入回收站会先终止当前任务，是否继续？');
        if (!terminate) return;
      }
      if (activeProject?.id === project.id) {
        await waitForPendingWrites();
      }
      try {
        await api.trashProject(project.id, terminate);
      } catch (error: any) {
        if (!terminate && String(error.message).includes('ACTIVE_TASKS')) {
          if (!window.confirm('这个项目仍有运行中的任务。是否终止任务并移入回收站？')) return;
          await api.trashProject(project.id, true);
        } else {
          showToast(`无法移入回收站：${error.message}`, 3600);
          return;
        }
      }
      await animateProjectRemoval([project.id]);
      if (activeProject?.id === project.id) resetActiveProject();
      if (localStorage.getItem('subtitle_factory_last_project_id') === project.id) {
        localStorage.removeItem('subtitle_factory_last_project_id');
      }
      await refreshLibraries();
      showToast('项目已移入回收站');
      setRemovingProjectIds(current => {
        const next = new Set(current);
        next.delete(project.id);
        return next;
      });
    },
    [
      activeProject?.id,
      animateProjectRemoval,
      currentTask,
      refreshLibraries,
      resetActiveProject,
      showToast,
      waitForPendingWrites,
    ],
  );

  const restoreProject = useCallback(
    async (project: Project) => {
      try {
        await api.restoreProject(project.id);
        await animateProjectRemoval([project.id]);
        await refreshLibraries();
        setRemovingProjectIds(current => {
          const next = new Set(current);
          next.delete(project.id);
          return next;
        });
        showToast('项目已恢复');
      } catch (error: any) {
        showToast(`恢复失败：${error.message}`, 3600);
      }
    },
    [animateProjectRemoval, refreshLibraries, showToast],
  );

  const deleteProjectForever = useCallback(
    async (project: Project) => {
      if (
        !window.confirm(
          `永久删除“${project.title}”？\n\n视频、音频、字幕、任务和导出文件都会被清理，此操作无法撤销。`,
        )
      ) {
        return;
      }
      try {
        await api.permanentlyDeleteProject(project.id);
        await animateProjectRemoval([project.id]);
        await refreshLibraries();
        setRemovingProjectIds(current => {
          const next = new Set(current);
          next.delete(project.id);
          return next;
        });
        showToast('项目已永久删除');
      } catch (error: any) {
        showToast(`永久删除失败：${error.message}`, 3600);
      }
    },
    [animateProjectRemoval, refreshLibraries, showToast],
  );

  const clearTrash = useCallback(async () => {
    if (
      !trashProjects.length ||
      !window.confirm(`永久删除回收站中的 ${trashProjects.length} 个项目？此操作无法撤销。`)
    ) {
      return;
    }
    try {
      const result = await api.emptyTrash();
      await animateProjectRemoval(trashProjects.slice(0, 40).map(project => project.id));
      await refreshLibraries();
      setRemovingProjectIds(new Set());
      showToast(result.message || '回收站已清空');
    } catch (error: any) {
      showToast(`清空失败：${error.message}`, 3600);
    }
  }, [animateProjectRemoval, refreshLibraries, showToast, trashProjects]);

  const saveRename = useCallback(async () => {
    if (!renameProjectState || !renameDraft.trim()) return;
    try {
      const updated = await api.renameProject(renameProjectState.id, renameDraft.trim());
      setProjects(current =>
        current.map(project => (project.id === updated.id ? updated : project)),
      );
      setActiveProject(current => (current?.id === updated.id ? updated : current));
      setRenameProjectState(null);
      showToast('项目已重命名');
    } catch (error: any) {
      showToast(`重命名失败：${error.message}`, 3600);
    }
  }, [renameDraft, renameProjectState, setActiveProject, setProjects, showToast]);

  return {
    projects,
    setProjects,
    trashProjects,
    libraryView,
    setLibraryView,
    librarySearch,
    setLibrarySearch,
    librarySearchHits,
    librarySearchTotal,
    librarySearchPage,
    setLibrarySearchPage,
    librarySearchLoading,
    librarySearchSelection,
    setLibrarySearchSelection,
    librarySearchFacets,
    librarySearchFilters,
    setLibrarySearchFilters,
    libraryPage,
    setLibraryPage,
    libraryTotal,
    libraryPages,
    libraryLoading,
    libraryFirstLoad,
    libraryError,
    libraryStatus,
    setLibraryStatus,
    libraryRefresh,
    setLibraryRefresh,
    librarySort,
    setLibrarySort,
    playlistBatches,
    refreshPlaylistBatches,
    refreshLibraries,
    projectGroups,
    knownProjectGroups,
    collapsedProjectGroups,
    toggleProjectGroup,
    openPlaylistBatches,
    togglePlaylistBatch,
    groupEditorProjectId,
    setGroupEditorProjectId,
    groupDraft,
    setGroupDraft,
    openProjectGroupEditor,
    saveProjectGroup,
    contextMenu,
    setContextMenu,
    contextMenuRef,
    openProjectMenu,
    renameProjectState,
    setRenameProjectState,
    renameDraft,
    setRenameDraft,
    saveRename,
    removingProjectIds,
    moveProjectToTrash,
    restoreProject,
    deleteProjectForever,
    clearTrash,
  };
}
