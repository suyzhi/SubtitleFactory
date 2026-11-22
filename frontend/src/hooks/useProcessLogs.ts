import { useCallback, useRef, useState } from 'react';
import type { ProcessLogEntry } from '../types';

export function useProcessLogs() {
  const [processLogs, setProcessLogs] = useState<ProcessLogEntry[]>([]);
  const logIdCounter = useRef(0);

  const addLog = useCallback(
    (
      level: 'info' | 'warning' | 'error',
      step: string,
      message: string,
      detail?: string,
      suggestion?: string,
    ) => {
      const entry: ProcessLogEntry = {
        id: String(++logIdCounter.current),
        time: new Date().toLocaleTimeString(),
        level,
        step,
        message,
        detail,
        suggestion,
      };
      setProcessLogs(prev => [...prev.slice(-199), entry]);
    },
    [],
  );

  return { processLogs, setProcessLogs, addLog };
}
