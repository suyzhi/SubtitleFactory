import type { ProcessingConfig } from '../../types';
import LanguagePicker from '../LanguagePicker';
import StepActions from './StepActions';
import { stepBlocker } from './stepHelpers';

interface Props {
  hasSegments: boolean;
  isProcessing: boolean;
  config: ProcessingConfig;
  translateAIReady: boolean;
  onConfigChange: (updater: (current: ProcessingConfig) => ProcessingConfig) => void;
  onUpdateTargetLanguage: (lang: string) => void;
  onTranslate: () => void;
  onOpenSettings: () => void;
}

export default function TranslateStepPanel({
  hasSegments,
  isProcessing,
  config,
  translateAIReady,
  onConfigChange,
  onUpdateTargetLanguage,
  onTranslate,
  onOpenSettings,
}: Props) {
  return (
    <section className="inspector-section step-panel">
      <header className="step-heading">
        <h3>AI 翻译</h3>
        <p>译文单独保存，不覆盖原文，之后可以继续逐句校对。</p>
      </header>
      <label>
        目标语言
        <LanguagePicker
          mode="target"
          allowCustom
          allowNone
          value={config.target_language}
          onChange={onUpdateTargetLanguage}
        />
      </label>
      <label className="check-row">
        <input
          type="checkbox"
          checked={config.bilingual}
          onChange={event =>
            onConfigChange(current => ({ ...current, bilingual: event.target.checked }))
          }
        />{' '}
        导出时包含原文与译文
      </label>
      <StepActions
        primary={
          <button
            className="button primary"
            disabled={
              !hasSegments ||
              isProcessing ||
              !translateAIReady ||
              config.target_language === 'none'
            }
            onClick={onTranslate}
          >
            开始翻译
          </button>
        }
        blocker={stepBlocker(
          [!hasSegments, '先生成或导入字幕'],
          [isProcessing, '有任务正在运行，完成或停止后再开始'],
          [config.target_language === 'none', '先选择目标语言'],
          [!translateAIReady, '尚未配置用于翻译的 AI 服务或 API 密钥'],
        )}
        extra={
          !translateAIReady ? (
            <button type="button" className="button secondary" onClick={onOpenSettings}>
              配置 AI 服务
            </button>
          ) : null
        }
      />
    </section>
  );
}
