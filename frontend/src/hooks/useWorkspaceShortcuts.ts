import { useEffect, type Dispatch, type MutableRefObject, type RefObject, type SetStateAction } from 'react';
import type { PlayerPresentationMode, SubtitlePlayerHandle } from '../components/SubtitlePlayer';
import type { Project, SegmentUpdate, SubtitleSegment } from '../types';

interface UseWorkspaceShortcutsOptions {
  activeProject: Project | null;
  activeProjectIdRef: MutableRefObject<string | null>;
  presentationMode: PlayerPresentationMode;
  setPresentationMode: Dispatch<SetStateAction<PlayerPresentationMode>>;
  setInspectorMode: Dispatch<SetStateAction<'style' | 'step' | null>>;
  setShowLinkPopover: Dispatch<SetStateAction<boolean>>;
  showAISettings: boolean;
  showProjectWorkspace: boolean;
  projectWorkspace: 'subtitles' | 'style' | 'export';
  draftItemsRef: MutableRefObject<Record<number, SegmentUpdate>>;
  editorQueue: MutableRefObject<Promise<unknown>>;
  editorWriteFailed: MutableRefObject<boolean>;
  playheadRef: MutableRefObject<{ segments: SubtitleSegment[]; time: number }>;
  videoPlayerRef: RefObject<SubtitlePlayerHandle | null>;
  commitDraft: () => Promise<void>;
  splitSegment: (index: number, splitAt: number) => Promise<void>;
  undoEditor: () => Promise<void>;
  redoEditor: () => Promise<void>;
  setToast: Dispatch<SetStateAction<string>>;
  showToast: (message: string, duration?: number) => void;
}

export function useWorkspaceShortcuts({
  activeProject,
  activeProjectIdRef,
  presentationMode,
  setPresentationMode,
  setInspectorMode,
  setShowLinkPopover,
  showAISettings,
  showProjectWorkspace,
  projectWorkspace,
  draftItemsRef,
  editorQueue,
  editorWriteFailed,
  playheadRef,
  videoPlayerRef,
  commitDraft,
  splitSegment,
  undoEditor,
  redoEditor,
  setToast,
  showToast,
}: UseWorkspaceShortcutsOptions) {
  // Theater / Fullscreen / Escape
  useEffect(() => {
    const handleTheaterShortcut = (event: KeyboardEvent) => {
      if (event.repeat || event.metaKey || event.ctrlKey || event.altKey) return;

      const target = event.target;
      const isEditing =
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT');

      const canPlay = Boolean(activeProject?.video_path);

      if (event.key.toLowerCase() === 't' && !isEditing && canPlay) {
        event.preventDefault();
        setPresentationMode(mode => (mode === 'theater' ? 'normal' : 'theater'));
        return;
      }

      if (event.key.toLowerCase() === 'f' && !isEditing && canPlay) {
        event.preventDefault();
        setPresentationMode(mode => (mode === 'fullscreen' ? 'normal' : 'fullscreen'));
        return;
      }

      if (
        event.key === 'Escape' &&
        presentationMode !== 'normal' &&
        !document.fullscreenElement &&
        !event.defaultPrevented
      ) {
        setPresentationMode('normal');
        return;
      }

      if (event.key === 'Escape' && !document.fullscreenElement && !event.defaultPrevented) {
        setInspectorMode(null);
        setShowLinkPopover(false);
      }
    };

    window.addEventListener('keydown', handleTheaterShortcut);
    return () => window.removeEventListener('keydown', handleTheaterShortcut);
  }, [
    activeProject?.media_mode,
    activeProject?.video_path,
    activeProject?.youtube_video_id,
    presentationMode,
    setInspectorMode,
    setPresentationMode,
    setShowLinkPopover,
  ]);

  // Cmd/Ctrl+S, Cmd/Ctrl+Enter split, Cmd/Ctrl+Z undo/redo
  useEffect(() => {
    const handleEditorShortcut = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.repeat) return;
      const key = event.key.toLowerCase();
      if (key === 's') {
        event.preventDefault();
        if (showAISettings || !showProjectWorkspace || !activeProjectIdRef.current) return;
        const target = event.target;
        if (
          target instanceof HTMLElement &&
          (target.isContentEditable || target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')
        ) {
          target.blur();
        }
        window.setTimeout(() => {
          if (Object.keys(draftItemsRef.current).length > 0) {
            void commitDraft();
            return;
          }
          void editorQueue.current.then(() => {
            setToast(
              editorWriteFailed.current ? '字幕尚未保存，请检查错误后重试' : '当前字幕已保存',
            );
            window.setTimeout(() => setToast(''), 1800);
          });
        }, 0);
        return;
      }
      if (key === 'enter') {
        if (showAISettings || !showProjectWorkspace || projectWorkspace !== 'subtitles') return;
        const target = event.target;
        if (
          target instanceof HTMLElement &&
          (target.isContentEditable || target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')
        ) {
          return;
        }
        event.preventDefault();
        const { segments: current, time } = playheadRef.current;
        const segment = current.find(item => time > item.start + 0.05 && time < item.end - 0.05);
        if (!segment) {
          showToast('播放头不在可拆分的字幕内');
          return;
        }
        void splitSegment(segment.index, time).catch(() => undefined);
        return;
      }
      if (key !== 'z') return;
      if (showAISettings || !showProjectWorkspace) return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable || target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')
      ) {
        return;
      }
      event.preventDefault();
      if (event.shiftKey) void redoEditor();
      else void undoEditor();
    };
    window.addEventListener('keydown', handleEditorShortcut);
    return () => window.removeEventListener('keydown', handleEditorShortcut);
  }, [
    activeProjectIdRef,
    commitDraft,
    draftItemsRef,
    editorQueue,
    editorWriteFailed,
    playheadRef,
    projectWorkspace,
    redoEditor,
    setToast,
    showAISettings,
    showProjectWorkspace,
    showToast,
    splitSegment,
    undoEditor,
  ]);

  // Lightweight <details> menus close on outside click or Escape, like native menus.
  useEffect(() => {
    const close = (event: Event) => {
      document.querySelectorAll<HTMLDetailsElement>('details.popover-menu[open]').forEach(menu => {
        if (
          event instanceof KeyboardEvent
            ? event.key === 'Escape'
            : !menu.contains(event.target as Node)
        ) {
          menu.open = false;
        }
      });
    };
    window.addEventListener('pointerdown', close);
    window.addEventListener('keydown', close);
    return () => {
      window.removeEventListener('pointerdown', close);
      window.removeEventListener('keydown', close);
    };
  }, []);

  // Playback keys work anywhere in the workspace, not only while the player has focus.
  useEffect(() => {
    const handlePlaybackShortcut = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
      if (showAISettings || !showProjectWorkspace || projectWorkspace === 'export') return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(target.tagName) ||
          target.closest('.pro-player, [role="dialog"], [role="menu"], .app-select-shell'))
      ) {
        return;
      }
      const player = videoPlayerRef.current;
      if (!player) return;
      if (event.key === ' ') player.togglePlay();
      else if (event.key.toLowerCase() === 'r' && !event.repeat) player.replay();
      else if (event.key === ',') player.stepFrame(-1);
      else if (event.key === '.') player.stepFrame(1);
      else return;
      event.preventDefault();
    };
    window.addEventListener('keydown', handlePlaybackShortcut);
    return () => window.removeEventListener('keydown', handlePlaybackShortcut);
  }, [projectWorkspace, showAISettings, showProjectWorkspace, videoPlayerRef]);
}
