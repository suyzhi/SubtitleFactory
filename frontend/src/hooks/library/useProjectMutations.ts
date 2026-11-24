import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MouseEvent as ReactMouseEvent,
  type SetStateAction,
} from 'react';
import * as api from '../../api/backend';
import type { Project, TaskStatus } from '../../types';

export interface ProjectContextMenuState {
  x: number;
  y: number;
  project: Project;
  trashed: boolean;
}

interface UseProjectMutationsOptions {
  motionEnabled: boolean;
  trashProjects: Project[];
  activeProject: Project | null;
  currentTask: TaskStatus | null;
  setProjects: Dispatch<SetStateAction<Project[]>>;
  setActiveProject: Dispatch<SetStateAction<Project | null>>;
  waitForPendingWrites: () => Promise<void>;
  resetActiveProject: () => void;
  refreshLibraries: () => Promise<void>;
  setToast: Dispatch<SetStateAction<string>>;
  showToast: (message: string, duration?: number) => void;
}

export function useProjectMutations({
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
}: UseProjectMutationsOptions) {
  const [contextMenu, setContextMenu] = useState<ProjectContextMenuState | null>(null);
  const [renameProjectState, setRenameProjectState] = useState<Project | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [removingProjectIds, setRemovingProjectIds] = useState<Set<string>>(() => new Set());
  const [groupEditorProjectId, setGroupEditorProjectId] = useState<string | null>(null);
  const [groupDraft, setGroupDraft] = useState('');

  const contextMenuRef = useRef<HTMLDivElement>(null);
  const contextMenuReturnFocus = useRef<HTMLElement | null>(null);

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
