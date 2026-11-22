// 字幕工厂 - 步骤详情卡片组件

import type { ProcessStep } from '../types';

interface Props {
  step: ProcessStep;
  onRetry?: () => void;
  onSkip?: () => void;
}

export default function ProcessStepCard({ step, onRetry, onSkip }: Props) {
  const isFailed = step.status === 'failed' || step.status === 'partial';

  return (
    <div className={`step-card step-card-${step.status}`}>
      <div className="step-card-header">
        <span className="step-card-name">{step.name}</span>
        <span className={`step-card-status ${step.status}`}>
          {step.status === 'running' && '⏳ 运行中'}
          {step.status === 'success' && '✅ 完成'}
          {step.status === 'failed' && '❌ 失败'}
          {step.status === 'cancelled' && '■ 已终止'}
          {step.status === 'partial' && '⚠️ 部分失败'}
          {step.status === 'waiting' && '⏸ 等待'}
          {step.status === 'skipped' && '⏭ 跳过'}
        </span>
      </div>

      <div className="step-card-desc">{step.description}</div>

      {(isFailed || step.status === 'running') && (
        <div className="step-card-extra">
          {step.error && <div className="step-card-error">❌ {step.error}</div>}
          {step.suggestion && <div className="step-card-suggestion">💡 {step.suggestion}</div>}
        </div>
      )}

      {step.details && Object.keys(step.details).length > 0 && (
        <div className="step-card-details">
          {Object.entries(step.details).map(([key, val]) => (
            <div key={key} className="step-detail-row">
              <span className="detail-key">{key}</span>
              <span className="detail-val">{String(val)}</span>
            </div>
          ))}
        </div>
      )}

      {isFailed && (
        <div className="step-card-actions">
          {onRetry && <button className="btn btn-primary btn-sm" onClick={onRetry}>🔄 重试</button>}
          {onSkip && <button className="btn btn-outline btn-sm" onClick={onSkip}>⏭ 跳过</button>}
        </div>
      )}
    </div>
  );
}
