import type { HealthStatus } from '../../types';
import { SettingsSection } from './SettingsPrimitives';

interface Props {
  appVersion: string;
  distributionChannel: string;
  youtubeEnabled: boolean;
  health: HealthStatus | null;
  onCopyDiagnostics: () => void;
  onExportDiagnostics: () => void;
  onOpenLogs: () => void;
  onOpenPrivacyPolicy: () => void;
}

export function AboutSettingsTab({
  appVersion,
  distributionChannel,
  youtubeEnabled,
  health,
  onCopyDiagnostics,
  onExportDiagnostics,
  onOpenLogs,
  onOpenPrivacyPolicy,
}: Props) {
  return (
    <>
      <SettingsSection title="快捷键" description="所有核心操作均可通过键盘完成。">
        <div className="shortcut-grid">
          <span>播放 / 暂停</span>
          <kbd>Space</kbd>
          <span>剧院模式</span>
          <kbd>T</kbd>
          <span>关闭弹窗或检查器</span>
          <kbd>Esc</kbd>
          <span>保存字幕编辑</span>
          <kbd>Return</kbd>
        </div>
      </SettingsSection>

      <SettingsSection title="关于字幕工厂" description="本地优先的专业字幕工作台。">
        <div className="about-card">
          <strong>字幕工厂 {health?.version || appVersion}</strong>
          <span>
            Apple Silicon · {distributionChannel === 'app_store' ? 'Mac App Store 版' : '直装版'}
          </span>
          <small>服务状态：{health?.status || '正在连接'}</small>
        </div>
        <div className="about-data-row about-directory-row">
          <span>
            <strong>数据目录</strong>
            <small>{health?.runtime?.data_directory || 'App 本地数据目录'}</small>
          </span>
        </div>
        <div className="inline-actions">
          <button className="button secondary" onClick={onCopyDiagnostics}>
            复制诊断信息
          </button>
          <button className="button secondary" onClick={onExportDiagnostics}>
            导出脱敏诊断包
          </button>
          <button className="button secondary" onClick={onOpenLogs}>
            查看处理日志
          </button>
        </div>
        <p className="settings-help">
          复制的诊断信息不包含本机路径或 API Key。自定义路径和密钥不会进入 Git、默认配置、日志或
          Release。
        </p>
      </SettingsSection>

      <SettingsSection
        title="隐私与数据"
        description="默认本地处理；任何云端发送都需要用户主动配置并明确执行。"
      >
        <div className="about-data-row">
          <span>
            <strong>本地项目</strong>
            <small>媒体、字幕、编辑历史、数据库和模型保存在这台 Mac；不会自动同步。</small>
          </span>
        </div>
        <div className="about-data-row">
          <span>
            <strong>云端 AI</strong>
            <small>
              翻译、整理和内容生成只发送当前操作所需的文本。Fun-Realtime-ASR
              只在单独授权后分段上传当前项目音频，授权可随时撤销。
            </small>
          </span>
        </div>
        {youtubeEnabled ? (
          <div className="about-data-row">
            <span>
              <strong>第三方媒体</strong>
              <small>
                直装版可按用户指令连接 YouTube；仅应处理拥有明确权利的内容，Chrome
                登录状态最多在权限挑战时读取一次。
              </small>
            </span>
          </div>
        ) : (
          <div className="about-data-row">
            <span>
              <strong>App Store 保护</strong>
              <small>
                此发行版不提供第三方网站媒体读取、Cookie 访问、持久监听文件夹或外部运行时路径。
              </small>
            </span>
          </div>
        )}
        <div className="inline-actions">
          <button className="button secondary" onClick={onOpenPrivacyPolicy}>
            查看完整隐私政策
          </button>
        </div>
        <p className="settings-help">
          API Key 以明文保存在本机数据库（App
          数据目录）中，数据库备份也会包含。诊断包不包含密钥、媒体、字幕正文或完整用户目录路径。
        </p>
      </SettingsSection>
    </>
  );
}
