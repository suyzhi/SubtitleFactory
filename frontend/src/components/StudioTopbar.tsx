import type { RefObject } from 'react';
import appIcon from '../assets/branding/app-icon-ui.png';
import { languageLabel } from '../languages';
import { projectReadiness, taskProgressLabel } from '../projectState';
import type { Project, SubtitleSegment, TaskStatus } from '../types';
import { isPlaylistUrl } from '../utils/library';
import OverlayDialog from './OverlayDialog';
import PlayerIcon from './PlayerIcons';

interface Props {
  showProjectWorkspace: boolean;
  activeProject: Project | null;
  segments: SubtitleSegment[];
  inProjectView: boolean;
  youtubeEnabled: boolean;
  backendStatus: 'connecting' | 'connected' | 'error';
  isProcessing: boolean;
  currentTask: TaskStatus | null;
  activeTaskCount: number;
  showTaskDrawer: boolean;
  showLinkPopover: boolean;
  youtubeUrl: string;
  theme: 'dark' | 'light';
  settingsButtonRef: RefObject<HTMLButtonElement | null>;
  onBackToLibrary: () => void;
  onImportLocal: () => void;
  onToggleLinkPopover: () => void;
  onCloseLinkPopover: () => void;
  onYoutubeUrlChange: (url: string) => void;
  onSubmitLink: (url: string) => void;
  onToggleTaskDrawer: () => void;
  onToggleTheme: (origin: HTMLElement) => void;
  onOpenSettings: () => void;
}

export default function StudioTopbar({
  showProjectWorkspace,
  activeProject,
  segments,
  inProjectView,
  youtubeEnabled,
  backendStatus,
  isProcessing,
  currentTask,
  activeTaskCount,
  showTaskDrawer,
  showLinkPopover,
  youtubeUrl,
  theme,
  settingsButtonRef,
  onBackToLibrary,
  onImportLocal,
  onToggleLinkPopover,
  onCloseLinkPopover,
  onYoutubeUrlChange,
  onSubmitLink,
  onToggleTaskDrawer,
  onToggleTheme,
  onOpenSettings,
}: Props) {
  return (
    <header className="studio-topbar" data-tauri-drag-region>
      {showProjectWorkspace && activeProject ? (
        <button className="topbar-back" aria-label="返回项目库" onClick={onBackToLibrary}>
          <span aria-hidden="true">‹</span> 项目库
        </button>
      ) : (
        <span className="topbar-spacer" />
      )}
      <div className="brand-block" data-tauri-drag-region>
        <img className="brand-mark" src={appIcon} alt="" />
        <strong data-tauri-drag-region>字幕工厂</strong>
      </div>
      <div className="active-project-title" data-tauri-drag-region>
        <strong data-tauri-drag-region>
          {showProjectWorkspace && activeProject ? activeProject.title : '项目库'}
        </strong>
        <span data-tauri-drag-region>
          {showProjectWorkspace && activeProject
            ? [
                languageLabel(activeProject.language),
                activeProject.target_language &&
                activeProject.target_language !== 'none' &&
                segments.some(segment => segment.translated_text)
                  ? `译文 ${languageLabel(activeProject.target_language)}`
                  : '',
                projectReadiness(activeProject),
              ]
                .filter(Boolean)
                .join(' · ')
            : '本地优先的专业字幕工作台'}
        </span>
      </div>
      <div className="topbar-actions">
        {inProjectView && (
          <button
            className="topbar-button"
            disabled={backendStatus !== 'connected'}
            onClick={onImportLocal}
          >
            <PlayerIcon name="plus" />
            导入
          </button>
        )}
        {inProjectView && youtubeEnabled && (
          <button
            className={`topbar-button ${showLinkPopover ? 'active' : ''}`}
            disabled={backendStatus !== 'connected'}
            onClick={onToggleLinkPopover}
          >
            <PlayerIcon name="link" />
            链接
          </button>
        )}
        <button
          className={`task-status-pill ${backendStatus} ${isProcessing ? 'busy' : ''}`}
          onClick={onToggleTaskDrawer}
          aria-expanded={showTaskDrawer}
        >
          <i className={`backend-dot ${backendStatus}`} />
          <span>
            {isProcessing
              ? currentTask
                ? taskProgressLabel(currentTask)
                : '正在处理'
              : backendStatus === 'connected'
                ? activeTaskCount
                  ? `${activeTaskCount} 项后台任务`
                  : '引擎就绪'
                : backendStatus === 'connecting'
                  ? '正在启动'
                  : '引擎异常'}
          </span>
        </button>
        <button
          className="icon-action"
          aria-label={theme === 'dark' ? '切换浅色模式' : '切换深色模式'}
          onClick={event => onToggleTheme(event.currentTarget)}
        >
          <PlayerIcon name={theme === 'dark' ? 'sun' : 'moon'} />
        </button>
        <button
          ref={settingsButtonRef}
          className="icon-action"
          aria-label="打开设置"
          onClick={onOpenSettings}
        >
          <PlayerIcon name="settings" />
        </button>
      </div>
      {youtubeEnabled && showLinkPopover && (
        <OverlayDialog label="从 YouTube 链接创建" onClose={onCloseLinkPopover}>
          <div className="link-popover">
            <div>
              <strong>从 YouTube 链接创建</strong>
              <button aria-label="关闭" onClick={onCloseLinkPopover}>
                ×
              </button>
            </div>
            <input
              autoFocus
              type="url"
              value={youtubeUrl}
              placeholder="https://www.youtube.com/watch?v=…"
              onChange={event => onYoutubeUrlChange(event.target.value)}
            />
            <p>
              {isPlaylistUrl(youtubeUrl)
                ? '将先读取播放列表，确认批量转写与 AI 流水线后创建归组。'
                : '播放定位参数会自动移除并下载完整视频。'}
            </p>
            <button
              className="button primary"
              disabled={!youtubeUrl || backendStatus !== 'connected'}
              onClick={() => onSubmitLink(youtubeUrl)}
            >
              {isPlaylistUrl(youtubeUrl) ? '解析播放列表' : '下载并生成字幕'}
            </button>
          </div>
        </OverlayDialog>
      )}
    </header>
  );
}
