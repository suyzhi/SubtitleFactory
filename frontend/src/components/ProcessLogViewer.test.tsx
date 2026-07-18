import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import ProcessLogViewer from './ProcessLogViewer';
import type { ProcessLogEntry } from '../types';

const logs: ProcessLogEntry[] = [
  {
    id: 'one',
    time: '10:00:00',
    level: 'info',
    step: '转写',
    message: '保留完整语义句',
  },
  {
    id: 'two',
    time: '10:00:01',
    level: 'warning',
    step: 'AI 自适应拆批',
    message: '正在缩小请求范围',
    detail: '32 条缩小为 16 + 16 条',
    suggestion: '无需手动重试',
  },
];

describe('ProcessLogViewer', () => {
  it('keeps short logs content-sized and exposes expandable diagnostics', () => {
    render(<ProcessLogViewer logs={logs} collapsed={false} />);

    const body = screen.getByRole('log');
    expect(body).toHaveClass('process-log-body');
    fireEvent.click(screen.getByText('正在缩小请求范围'));
    expect(screen.getByText('32 条缩小为 16 + 16 条')).toBeInTheDocument();
    expect(screen.getByText(/无需手动重试/)).toBeInTheDocument();
  });

  it('does not swallow wheel events needed by the surrounding task page', () => {
    const onWheel = vi.fn();
    render(
      <div onWheel={onWheel}>
        <ProcessLogViewer logs={logs} collapsed={false} />
      </div>,
    );

    fireEvent.wheel(screen.getByRole('log'), { deltaY: 240 });
    expect(onWheel).toHaveBeenCalledTimes(1);
  });

  it('keeps clear behavior available for long-running tasks', () => {
    const onClear = vi.fn();
    render(<ProcessLogViewer logs={logs} collapsed={false} onClear={onClear} />);
    fireEvent.click(screen.getByText('🗑 清空'));
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});
