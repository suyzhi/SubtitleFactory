import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react';
import * as api from '../api/backend';
import type { PlaylistBatchDetail, Project, TaskStatus } from '../types';
import { useLibrarySearch } from './library/useLibrarySearch';
import {
  useProjectMutations,
  type ProjectContextMenuState,
} from './library/useProjectMutations';

export type { ProjectContextMenuState };

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
  const [libraryPage, setLibraryPage] = useState(1);
  const [libraryTotal, setLibraryTotal] = useState(0);
  const [libraryPages, setLibraryPages] = useState(1);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryLoaded, setLibraryLoaded] = useState(false);
  const [libraryError, setLibraryError] = useState('');
  const [libraryStatus, setLibraryStatus] = useState('');
  const [librarySort, setLibrarySort] = useState('updated_desc');
  const [playlistBatches, setPlaylistBatches] = useState<PlaylistBatchDetail[]>([]);
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

  const search = useLibrarySearch({ backendStatus, libraryView });

  useEffect(() => {
    localStorage.setItem(
      'subtitle_factory_collapsed_groups',
      JSON.stringify([...collapsedProjectGroups]),
    );
  }, [collapsedProjectGroups]);

  useEffect(() => {
    try {
      localStorage.setItem(
        'subtitle_factory_playlist_batches_open',
        JSON.stringify(openPlaylistBatches),
      );
    } catch {
      /* storage unavailable */
    }
  }, [openPlaylistBatches]);

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

  const mutations = useProjectMutations({
    motionEnabled,
    trashProjects,
    activeProject,
    currentTask,
    setProjects,
    setActiveProject,
    waitForPendingWrites,
    resetActiveProject,
    refreshLibraries,
    setToast,
    showToast,
  });

  const libraryFirstLoad = backendStatus === 'connected' && !libraryLoaded && !libraryError;
  const hasActivePlaylistBatch = playlistBatches.some(
    ({ batch }) => batch.status === 'running' || batch.status === 'pending',
  );

  useEffect(() => {
    setLibraryPage(1);
  }, [search.librarySearch, librarySort, libraryStatus, libraryView]);

  useEffect(() => {
    if (backendStatus !== 'connected' || showProjectWorkspace) return;
    let cancelled = false;
    setLibraryLoading(true);
    setLibraryError('');
    const timer = window.setTimeout(() => {
      void api
        .listProjects({
          deleted: libraryView === 'trash',
          search: search.librarySearch.trim(),
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
    search.librarySearch,
    librarySort,
    libraryStatus,
    libraryView,
    libraryPage,
    showProjectWorkspace,
    libraryRefresh,
    setProjects,
  ]);

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

  return {
    projects,
    setProjects,
    trashProjects,
    libraryView,
    setLibraryView,
    ...search,
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
    ...mutations,
  };
}
