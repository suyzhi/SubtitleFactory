// 字幕工厂 - 流程时间线组件

import type { ProcessStep, TaskStepStatus } from '../types';

interface Props {
  steps: ProcessStep[];
  currentStepId: string | null;
  totalProgress: number;
  onStepClick?: (stepId: string) => void;
}

const STATUS_CONFIG: Record<TaskStepStatus, { icon: string; label: string; className: string }> = {
  waiting:    { icon: '○', label: '等待', className: 'step-waiting' },
  running:    { icon: '◌', label: '运行中', className: 'step-running' },
  paused:     { icon: 'Ⅱ', label: '已暂停', className: 'step-paused' },
  success:    { icon: '✓', label: '完成', className: 'step-success' },
  failed:     { icon: '✕', label: '失败', className: 'step-failed' },
  cancelled:  { icon: '■', label: '已终止', className: 'step-cancelled' },
  partial:    { icon: '⚠', label: '部分失败', className: 'step-partial' },
  skipped:    { icon: '–', label: '跳过', className: 'step-skipped' },
};

export default function ProcessTimeline({ steps, currentStepId, totalProgress, onStepClick }: Props) {
  return (
    <section className="panel process-timeline-panel">
      <div className="process-timeline-header">
        <h3>处理流程</h3>
        <span className="total-progress-text">总进度 {totalProgress}%</span>
      </div>

      {/* 总进度条 */}
      <div className="total-progress-bar">
        <div className="total-progress-fill" style={{ width: `${Math.min(totalProgress, 100)}%` }} />
      </div>

      {/* 步骤列表 */}
      <div className="process-steps">
        {steps.map((step, i) => {
          const cfg = STATUS_CONFIG[step.status] || STATUS_CONFIG.waiting;
          const isCurrent = step.id === currentStepId;
          return (
            <div
              key={step.id}
              className={`process-step ${cfg.className} ${isCurrent ? 'step-current' : ''}`}
              onClick={() => onStepClick?.(step.id)}
              title={step.description}
            >
              <div className="step-indicator">
                <span className="step-icon">{cfg.icon}</span>
                <span className="step-number">{i + 1}</span>
              </div>
              <div className="step-content">
                <div className="step-name">
                  {step.name}
                  {isCurrent && <span className="step-badge">当前</span>}
                </div>
                <div className="step-desc">{step.description}</div>
                {step.status === 'running' && (
                  <div className="step-progress-micro">
                    <div className="micro-bar">
                      <div className="micro-fill" style={{ width: `${step.progress}%` }} />
                    </div>
                    <span className="micro-text">{step.progress}%</span>
                  </div>
                )}
              </div>
              <span className={`step-status-badge ${cfg.className}`}>{cfg.label}</span>
            </div>
          );
        })}
      </div>

      {steps.length === 0 && (
        <div className="process-empty">暂无处理步骤</div>
      )}
    </section>
  );
}
