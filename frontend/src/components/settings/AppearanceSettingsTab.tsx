import { Segmented, SettingsSection, Toggle } from './SettingsPrimitives';

interface Props {
  theme: 'light' | 'dark';
  onThemeChange: (theme: 'light' | 'dark', origin?: HTMLElement) => void;
  motionEnabled: boolean;
  onMotionEnabledChange: (enabled: boolean) => void;
}

export function AppearanceSettingsTab({
  theme,
  onThemeChange,
  motionEnabled,
  onMotionEnabledChange,
}: Props) {
  return (
    <>
      <SettingsSection
        title="外观"
        description="主题同时作用于 Web 界面和 macOS 原生标题栏。"
      >
        <Segmented
          value={theme}
          onChange={(value, origin) => onThemeChange(value as 'light' | 'dark', origin)}
          options={[
            ['light', '浅色'],
            ['dark', '深色'],
          ]}
        />
      </SettingsSection>
      <SettingsSection title="动画" description="系统“减少动态效果”始终具有最高优先级。">
        <Toggle
          label="界面动画"
          detail="页面切换、卡片入场、按钮回弹、滑动选中与弹窗过渡"
          checked={motionEnabled}
          onChange={onMotionEnabledChange}
        />
      </SettingsSection>
    </>
  );
}
