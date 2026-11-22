import { useEffect, useRef, useState } from 'react';

/** Counts from the previous value to the next one with an ease-out curve. */
export default function AnimatedNumber({ value, animate = true, duration = 720 }: { value: number; animate?: boolean; duration?: number }) {
  const [shown, setShown] = useState(animate ? 0 : value);
  const current = useRef(shown);
  useEffect(() => {
    const from = current.current;
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (!animate || reduced || from === value) { current.current = value; setShown(value); return; }
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / duration);
      const next = Math.round(from + (value - from) * (1 - Math.pow(1 - progress, 3)));
      current.current = next;
      setShown(next);
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, animate, duration]);
  return <>{shown}</>;
}
