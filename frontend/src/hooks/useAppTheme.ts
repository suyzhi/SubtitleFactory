import { useCallback, useEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { revealTheme } from '../themeTransition';
import type { PlayerPresentationMode } from '../components/SubtitlePlayer';

export function useAppTheme() {
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    const stored = localStorage.getItem('subtitle_factory_theme');
    if (stored === 'light' || stored === 'dark') return stored;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });
  const [motionEnabled, setMotionEnabled] = useState(
    () => localStorage.getItem('subtitle_factory_motion') !== 'off',
  );
  const [presentationMode, setPresentationMode] = useState<PlayerPresentationMode>('normal');

  const themeTransitionBusy = useRef(false);
  const ownsWindowFullscreen = useRef(false);

  const changeTheme = useCallback((next: 'light' | 'dark', origin?: HTMLElement) => {
    if (themeTransitionBusy.current || next === theme) return;
    const apply = () => flushSync(() => setTheme(next));
    if (
      !motionEnabled ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches ||
      !document.startViewTransition
    ) {
      apply();
      return;
    }
    const trigger =
      origin ||
      document.querySelector<HTMLElement>(
        '.icon-action[aria-label="切换深色模式"],.icon-action[aria-label="切换浅色模式"]',
      );
    if (!trigger) {
      apply();
      return;
    }
    themeTransitionBusy.current = true;
    void revealTheme(apply, trigger).finally(() => {
      themeTransitionBusy.current = false;
    });
  }, [motionEnabled, theme]);

  useEffect(() => {
    localStorage.setItem('subtitle_factory_theme', theme);
    document.documentElement.style.colorScheme = theme;
    document.documentElement.dataset.theme = theme;
    if ((window as any).__TAURI_INTERNALS__) {
      void getCurrentWindow().setTheme(theme).catch(() => undefined);
    }
  }, [theme]);

  useEffect(() => {
    localStorage.setItem('subtitle_factory_motion', motionEnabled ? 'on' : 'off');
    localStorage.removeItem('subtitle_factory_density');
  }, [motionEnabled]);

  useEffect(() => {
    if (!(window as any).__TAURI_INTERNALS__) return;
    const appWindow = getCurrentWindow();
    let cancelled = false;

    const syncWindowFullscreen = async () => {
      try {
        const isFullscreen = await appWindow.isFullscreen();
        if (cancelled) return;
        if (presentationMode === 'fullscreen') {
          ownsWindowFullscreen.current = !isFullscreen;
          if (!isFullscreen) await appWindow.setFullscreen(true);
        } else if (ownsWindowFullscreen.current) {
          ownsWindowFullscreen.current = false;
          if (isFullscreen) await appWindow.setFullscreen(false);
        }
      } catch (error) {
        console.error('同步播放器全屏状态失败', error);
      }
    };

    void syncWindowFullscreen();
    return () => {
      cancelled = true;
    };
  }, [presentationMode]);

  useEffect(() => {
    if (!(window as any).__TAURI_INTERNALS__) return;
    const appWindow = getCurrentWindow();
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void appWindow
      .onResized(async () => {
        if (disposed || presentationMode !== 'fullscreen' || !ownsWindowFullscreen.current) return;
        if (!(await appWindow.isFullscreen())) {
          ownsWindowFullscreen.current = false;
          setPresentationMode('normal');
        }
      })
      .then(fn => {
        unlisten = fn;
      });
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [presentationMode]);

  return {
    theme,
    changeTheme,
    motionEnabled,
    setMotionEnabled,
    presentationMode,
    setPresentationMode,
  };
}
