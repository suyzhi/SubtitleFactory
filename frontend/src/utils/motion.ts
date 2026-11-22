import { useLayoutEffect, useRef, useState } from 'react';

const ACTIVE_CHILD = '.active,[aria-pressed="true"],[aria-selected="true"],[aria-current="page"]';

/**
 * Publishes the active child's box as CSS variables on the container, so a
 * `.sliding-tabs::before` pill can glide between tabs instead of jumping.
 */
export function useSlidingIndicator<T extends HTMLElement>(activeKey: unknown) {
  const ref = useRef<T>(null);
  useLayoutEffect(() => {
    const container = ref.current;
    if (!container) return;
    const place = () => {
      const active = Array.from(container.children).find(child => child.matches(ACTIVE_CHILD)) as HTMLElement | undefined;
      if (!active) { container.style.setProperty('--indicator-opacity', '0'); return; }
      container.style.setProperty('--indicator-x', `${active.offsetLeft}px`);
      container.style.setProperty('--indicator-y', `${active.offsetTop}px`);
      container.style.setProperty('--indicator-w', `${active.offsetWidth}px`);
      container.style.setProperty('--indicator-h', `${active.offsetHeight}px`);
      container.style.setProperty('--indicator-opacity', '1');
    };
    place();
    // Enable the glide only after the first placement so the pill never flies in from 0,0.
    const frame = requestAnimationFrame(() => container.setAttribute('data-indicator-ready', ''));
    if (typeof ResizeObserver === 'undefined') return () => cancelAnimationFrame(frame);
    const observer = new ResizeObserver(place);
    observer.observe(container);
    Array.from(container.children).forEach(child => observer.observe(child));
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [activeKey]);
  return ref;
}

/**
 * True for a short window after `key` changes. Entrance animations are scoped
 * to this window, so a WebView that never ticks the animation still ends up
 * showing the final, fully visible state once the class is removed.
 */
export function useTransientFlag(key: string, duration = 1100) {
  const [active, setActive] = useState(true);
  useLayoutEffect(() => {
    setActive(true);
    const timer = window.setTimeout(() => setActive(false), duration);
    return () => window.clearTimeout(timer);
  }, [key, duration]);
  return active;
}
