import type * as api from '../../api/backend';
import AppSelect from '../AppSelect';
import { SettingsSection, Toggle } from './SettingsPrimitives';

interface Props {
  providerCards: api.AIProviderCard[];
  assignments: api.AIAssignments;
  busy: boolean;
  onAssignmentsChange: (next: api.AIAssignments) => void;
  onSaveAssignments: () => void;
  onUpdateProvider: (id: string, patch: Partial<api.AIProviderCard>) => void;
  onTestProvider: (card: api.AIProviderCard) => void;
  onSaveProvider: (card: api.AIProviderCard) => void;
}

export function AISettingsTab({
  providerCards,
  assignments,
  busy,
  onAssignmentsChange,
  onSaveAssignments,
  onUpdateProvider,
  onTestProvider,
  onSaveProvider,
}: Props) {
  const providerOptions = providerCards.map(card => ({
    value: card.provider_id,
    label: card.name,
    description: card.model,
  }));

  return (
    <>
      <SettingsSection
        title="任务分配"
        description="整理、翻译与内容生成分别选择服务商，不会静默继承彼此的配置。"
      >
        <div className="provider-assignments">
          <label>
            AI 整理
            <AppSelect
              value={assignments.clean_provider_id}
              onChange={clean_provider_id =>
                onAssignmentsChange({ ...assignments, clean_provider_id })
              }
              label="AI 整理供应商"
              options={providerOptions}
            />
          </label>
          <label>
            AI 翻译
            <AppSelect
              value={assignments.translate_provider_id}
              onChange={translate_provider_id =>
                onAssignmentsChange({ ...assignments, translate_provider_id })
              }
              label="AI 翻译供应商"
              options={providerOptions}
            />
          </label>
          <label>
            内容生成
            <AppSelect
              value={assignments.content_provider_id}
              onChange={content_provider_id =>
                onAssignmentsChange({ ...assignments, content_provider_id })
              }
              label="内容生成供应商"
              options={providerOptions}
            />
          </label>
          <button className="button primary" disabled={busy} onClick={onSaveAssignments}>
            保存分配
          </button>
        </div>
      </SettingsSection>

      <SettingsSection
        title="模型供应商"
        description="每张卡的地址、密钥和模型互相隔离，密钥只保存在本机。"
      >
        <div className="provider-card-grid">
          {providerCards.map(card => (
            <details className="provider-card" key={card.provider_id}>
              <summary>
                <strong>{card.name}</strong>
                <span className={card.has_api_key ? 'ready' : ''}>
                  {card.has_api_key ? '已配置' : '未配置'}
                </span>
              </summary>
              <div className="provider-card-body">
                <label>
                  Base URL
                  <input
                    value={card.base_url}
                    onChange={event =>
                      onUpdateProvider(card.provider_id, { base_url: event.target.value })
                    }
                  />
                </label>
                <label>
                  模型
                  <input
                    value={card.model}
                    onChange={event =>
                      onUpdateProvider(card.provider_id, { model: event.target.value })
                    }
                  />
                </label>
                {!!card.models.length && (
                  <div className="provider-model-chips">
                    {card.models.map(model => (
                      <button
                        type="button"
                        className={model === card.model ? 'active' : ''}
                        key={model}
                        onClick={() => onUpdateProvider(card.provider_id, { model })}
                      >
                        {model}
                      </button>
                    ))}
                  </div>
                )}
                {card.supports_thinking && (
                  <Toggle
                    label="思考模式"
                    detail="开启后模型会先推理再作答，更慢且消耗更多 token；字幕整理和翻译通常不需要"
                    checked={card.thinking_enabled}
                    onChange={thinking_enabled =>
                      onUpdateProvider(card.provider_id, { thinking_enabled })
                    }
                  />
                )}
                <label>
                  API Key
                  <input
                    type="password"
                    value={card.api_key}
                    placeholder={card.has_api_key ? '留空保留现有密钥' : 'sk-…'}
                    onChange={event =>
                      onUpdateProvider(card.provider_id, { api_key: event.target.value })
                    }
                  />
                </label>
                <footer>
                  <button
                    className="button secondary"
                    disabled={busy || !card.has_api_key}
                    onClick={() => onTestProvider(card)}
                  >
                    测试连接
                  </button>
                  <button
                    className="button primary"
                    disabled={busy}
                    onClick={() => onSaveProvider(card)}
                  >
                    保存
                  </button>
                </footer>
              </div>
            </details>
          ))}
        </div>
      </SettingsSection>
    </>
  );
}
