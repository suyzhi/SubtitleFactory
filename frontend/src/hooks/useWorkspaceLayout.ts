import { useCallback, useEffect, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { flushSync } from 'react-dom';

export function useWorkspaceLayout(motionEnabled: boolean) {
  const [compactLibrary, setCompactLibrary] = useState(
    () => localStorage.getItem('subtitle_factory_library_compact') === 'true',
  );
  const [leftPanelWidth, setLeftPanelWidth] = useState(
    () => Number(localStorage.getItem('subtitle_factory_left_width')) || 258,
  );
  const [rightPanelWidth, setRightPanelWidth] = useState(
    () => Number(localStorage.getItem('subtitle_factory_right_width')) || 336,
  );
  const [viewerHeight, setViewerHeight] = useState(
    () => Number(localStorage.getItem('subtitle_factory_viewer_height')) || 470,
  );

  useEffect(() => {
    localStorage.setItem('subtitle_factory_left_width', String(leftPanelWidth));
    localStorage.setItem('subtitle_factory_right_width', String(rightPanelWidth));
    localStorage.setItem('subtitle_factory_viewer_height', String(viewerHeight));
  }, [leftPanelWidth, rightPanelWidth, viewerHeight]);

  // Cards morph between the grid and the list: each card and cover is captured under its
  // own view-transition name only for the duration of the switch (see .layout-morphing).
  const toggleCompactLibrary = useCallback(() => {
    const apply = () =>
      flushSync(() =>
        setCompactLibrary(value => {
          localStorage.setItem('subtitle_factory_library_compact', String(!value));
          return !value;
        }),
      );
    const root = document.documentElement;
    if (
      !motionEnabled ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches ||
      !document.startViewTransition ||
      root.classList.contains('theme-revealing')
    ) {
      apply();
      return;
    }
    // Snapshotting every cover is what makes the switch stutter; only the visible ones need to fly.
    const visible = Array.from(
      document.querySelectorAll<HTMLElement>('.app-library .project-card-shell'),
    )
      .filter(shell => {
        const rect = shell.getBoundingClientRect();
        return rect.bottom > 0 && rect.top < window.innerHeight;
      })
      .slice(0, 16);
    visible.forEach(shell => shell.setAttribute('data-morph', ''));
    root.classList.add('layout-morphing');
    const transition = document.startViewTransition(apply);
    void transition.finished.catch(() => undefined).finally(() => {
      root.classList.remove('layout-morphing');
      visible.forEach(shell => shell.removeAttribute('data-morph'));
    });
  }, [motionEnabled]);

  const beginResize = useCallback(
    (kind: 'left' | 'right' | 'viewer', event: ReactPointerEvent) => {
      event.preventDefault();
      const startX = event.clientX;
      const startY = event.clientY;
      const initialLeft = leftPanelWidth;
      const initialRight = rightPanelWidth;
      const initialViewer = viewerHeight;
      document.body.classList.add('is-resizing');
      const onMove = (move: PointerEvent) => {
        if (kind === 'left') {
          setLeftPanelWidth(Math.max(210, Math.min(430, initialLeft + move.clientX - startX)));
        }
        if (kind === 'right') {
          setRightPanelWidth(Math.max(280, Math.min(480, initialRight - (move.clientX - startX))));
        }
        if (kind === 'viewer') {
          setViewerHeight(
            Math.max(260, Math.min(window.innerHeight - 250, initialViewer + move.clientY - startY)),
          );
        }
      };
      const onUp = () => {
        document.body.classList.remove('is-resizing');
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp, { once: true });
    },
    [leftPanelWidth, rightPanelWidth, viewerHeight],
  );

  return {
    compactLibrary,
    toggleCompactLibrary,
    leftPanelWidth,
    setLeftPanelWidth,
    rightPanelWidth,
    viewerHeight,
    beginResize,
  };
}
