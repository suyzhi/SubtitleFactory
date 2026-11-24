import type { AppSettings } from '../../types';
import AppSelect from '../AppSelect';
import { Segmented, SettingsSection, Toggle } from './SettingsPrimitives';

interface Props {
  draft: AppSettings;
  onUpdateDraft: (partial: Partial<AppSettings>) => void;
}

export function GeneralSettingsTab({ draft, onUpdateDraft }: Props) {
  return (
    <>
      <SettingsSection
        title="默认工作流"
        description="下载与本地导入均可自动完成音频提取和转写。"
      >
        <Segmented
          value={String(draft.default_workflow || 'automatic')}
          onChange={value => onUpdateDraft({ default_workflow: value })}
          options={[
            ['automatic', '自动'],
            ['manual', '手动'],
          ]}
        />
        <Toggle
          label="自动保存字幕编辑"
          detail="当前编辑器会在确认单行编辑后立即写入本地项目"
          checked={draft.auto_save !== false}
          onChange={auto_save => onUpdateDraft({ auto_save })}
        />
      </SettingsSection>
      <SettingsSection title="启动" description="选择 App 打开时看到的内容。">
        <label className="settings-field horizontal">
          <span>
            <strong>启动行为</strong>
            <small>不影响正在运行的后台任务</small>
          </span>
          <AppSelect
            value={draft.startup_behavior || 'restore_last'}
            onChange={startup_behavior =>
              onUpdateDraft({
                startup_behavior: startup_behavior as AppSettings['startup_behavior'],
              })
            }
            label="启动行为"
            options={[
              { value: 'restore_last', label: '打开上次项目' },
              { value: 'project_library', label: '显示项目库' },
            ]}
          />
        </label>
      </SettingsSection>
    </>
  );
}
