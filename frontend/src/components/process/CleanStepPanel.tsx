import type * as api from '../../api/backend';
import type { FailedCleanBatch, ProcessingConfig } from '../../types';
import StepActions from './StepActions';
import { stepBlocker } from './stepHelpers';

interface Props {
  hasSegments: boolean;
  isProcessing: boolean;
  taskStarting: boolean;
  config: ProcessingConfig;
  aiProviderState: api.AIProvidersResponse | null;
  cleanAIProvider?: api.AIProviderCard;
  cleanAIReady: boolean;
  failedCleanBatches: FailedCleanBatch[];
  onConfigChange: (updater: (current: ProcessingConfig) => ProcessingConfig) => void;
  onClean: () => void;
  onUndoClean: () => void;
  onRetryFailedCleanBatch: (batchIndex: number) => void;
  onOpenSettings: () => void;
}

export default function CleanStepPanel({
  hasSegments,
  isProcessing,
  taskStarting,
  config,
  aiProviderState,
  cleanAIProvider,
  cleanAIReady,
  failedCleanBatches,
  onConfigChange,
  onClean,
  onUndoClean,
  onRetryFailedCleanBatch,
  onOpenSettings,
}: Props) {
  return (
    <>
      <section className="inspector-section step-panel">
        <header className="step-heading">
          <h3>AI 忠实整理</h3>
          <p>只修正明显的错词、标点和断句，不改变原意；可随时撤销。</p>
        </header>
        <div className="ai-summary-row">
          <span className="ai-logo">✦</span>
          <div>
            <strong>
              {cleanAIProvider?.name || (aiProviderState ? '未配置 AI 服务' : '正在读取 AI 服务')}
            </strong>
            <small>{cleanAIProvider?.model || '在设置 → AI 服务中配置'}</small>
          </div>
        </div>
        <label className="clean-length-field">
          参考单句长度 <span>{config.clean_target_length} 字</span>
          <input
            type="range"
            min={16}
            max={100}
            step={2}
            value={config.clean_target_length}
            onChange={event =>
              onConfigChange(current => ({
                ...current,
                clean_target_length: Number(event.target.value),
              }))
            }
          />
        </label>
        <StepActions
          primary={
            <button
              className="button primary"
              disabled={!hasSegments || isProcessing || !cleanAIReady}
              onClick={onClean}
            >
              开始整理
            </button>
          }
          blocker={stepBlocker(
            [!hasSegments, '先生成或导入字幕'],
            [isProcessing, '有任务正在运行，完成或停止后再开始'],
            [!cleanAIReady, '尚未配置用于整理的 AI 服务或 API 密钥'],
          )}
          extra={
            <>
              {!cleanAIReady && (
                <button type="button" className="button secondary" onClick={onOpenSettings}>
                  配置 AI 服务
                </button>
              )}
              <button
                type="button"
                className="step-link"
                disabled={!hasSegments || isProcessing}
                onClick={onUndoClean}
              >
                撤销上次整理
              </button>
            </>
          }
        />
      </section>

      {failedCleanBatches.length > 0 && (
        <section className="failed-batch-recovery" aria-label="失败批次恢复">
          <header>
            <div>
              <strong>有 {failedCleanBatches.length} 个批次需要重试</strong>
              <small>只会重新整理对应时间范围，不会重跑整份字幕。</small>
            </div>
          </header>
          <div className="failed-batch-list">
            {failedCleanBatches.map(batch => (
              <article key={batch.batch_index}>
                <div>
                  <strong>
                    第 {batch.batch_index} 批 · {batch.segment_count} 条
                  </strong>
                  <small>
                    {batch.start === null || batch.end === null
                      ? '时间范围不可用'
                      : `${batch.start.toFixed(1)}s – ${batch.end.toFixed(1)}s`}{' '}
                    · 已尝试 {batch.attempts} 次
                  </small>
                  <p title={batch.error}>{batch.error || 'AI 未返回有效 JSON'}</p>
                </div>
                <button
                  type="button"
                  disabled={taskStarting || isProcessing}
                  onClick={() => onRetryFailedCleanBatch(batch.batch_index)}
                >
                  只重试这一批
                </button>
              </article>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
