import type { PlaylistBatchDetail, Project } from '../../types';
import { isPlaylistBatchActive } from '../../utils/playlist';
import AnimatedNumber from '../AnimatedNumber';

interface Props {
  youtubeEnabled: boolean;
  filesystemAutomationEnabled: boolean;
  backendStatus: 'connecting' | 'connected' | 'error';
  motionEnabled: boolean;
  librarySearch: string;
  libraryTotal: number;
  projects: Project[];
  trashProjects: Project[];
  playlistBatches: PlaylistBatchDetail[];
  onImportLocal: () => void;
  onImportProjectPackage: () => void;
  onOpenProductionCenter: () => void;
  onOpenLinkPopover: () => void;
}

export default function LibraryHeader({
  youtubeEnabled,
  filesystemAutomationEnabled,
  backendStatus,
  motionEnabled,
  librarySearch,
  libraryTotal,
  projects,
  trashProjects,
  playlistBatches,
  onImportLocal,
  onImportProjectPackage,
  onOpenProductionCenter,
  onOpenLinkPopover,
}: Props) {
  const activeBatchCount = playlistBatches.filter(item =>
    isPlaylistBatchActive(item.batch.status),
  ).length;
  const attentionBatchCount = playlistBatches.filter(item =>
    ['partial', 'failed'].includes(item.batch.status),
  ).length;

  return (
    <>
      <header className="library-page-header">
        <div>
          <small>字幕工厂</small>
          <h1>你的项目</h1>
          <p>
            {youtubeEnabled
              ? '选择一个项目继续工作，或从视频和链接开始新的字幕任务。'
              : '选择一个项目继续工作，或导入您有权处理的视频。'}
          </p>
        </div>
        <div>
          <details className="library-more-actions popover-menu">
            <summary className="button secondary" aria-label="更多导入方式">
              更多
            </summary>
            <div
              role="menu"
              onClick={event => {
                (event.currentTarget.parentElement as HTMLDetailsElement).open = false;
              }}
            >
              <button
                role="menuitem"
                disabled={backendStatus !== 'connected'}
                onClick={onImportProjectPackage}
              >
                <strong>导入项目包</strong>
                <small>打开 .sfproject，继续在另一台电脑上的工作</small>
              </button>
              {filesystemAutomationEnabled && (
                <button
                  role="menuitem"
                  disabled={backendStatus !== 'connected'}
                  onClick={onOpenProductionCenter}
                >
                  <strong>批量与监听</strong>
                  <small>一次导入多个文件，或监听文件夹自动处理</small>
                </button>
              )}
            </div>
          </details>
          {youtubeEnabled && (
            <button
              className="button secondary"
              disabled={backendStatus !== 'connected'}
              onClick={onOpenLinkPopover}
            >
              添加链接
            </button>
          )}
          <button
            className="button primary"
            disabled={backendStatus !== 'connected'}
            onClick={onImportLocal}
          >
            导入视频
          </button>
        </div>
      </header>

      {!librarySearch.trim() && (projects.length > 0 || backendStatus !== 'connected') && (
        <section className="library-overview library-stats" aria-label="项目库概览">
          <div title="当前筛选条件下的项目，全部保存在本机">
            <strong>
              <AnimatedNumber value={libraryTotal} animate={motionEnabled} />
            </strong>
            <span>个项目</span>
          </div>
          <div title="本页项目的字幕总数，可在上方搜索框全文搜索">
            <strong>
              <AnimatedNumber
                value={projects.reduce(
                  (total, project) => total + Number(project.segments_count || 0),
                  0,
                )}
                animate={motionEnabled}
              />
            </strong>
            <span>条字幕</span>
          </div>
          {youtubeEnabled ? (
            <div
              title={
                playlistBatches.length ? `${playlistBatches.length} 个播放列表` : '暂无进行中的队列'
              }
            >
              <strong>
                <AnimatedNumber
                  value={activeBatchCount || attentionBatchCount}
                  animate={motionEnabled}
                />
              </strong>
              <span>
                {activeBatchCount || !attentionBatchCount
                  ? '个批量任务进行中'
                  : '个批量任务需处理'}
              </span>
            </div>
          ) : (
            <div title="第三方媒体读取已关闭">
              <strong>本地优先</strong>
              <span>隐私模式</span>
            </div>
          )}
          {trashProjects.length > 0 && (
            <div>
              <strong>{trashProjects.length}</strong>
              <span>个在回收站</span>
            </div>
          )}
          {backendStatus !== 'connected' && (
            <div className={`library-runtime-card ${backendStatus}`}>
              <strong>{backendStatus === 'connecting' ? '引擎启动中' : '引擎需检查'}</strong>
              <span>
                {backendStatus === 'connecting'
                  ? '正在载入本机运行时'
                  : '本地功能受限，请在设置中查看诊断'}
              </span>
            </div>
          )}
        </section>
      )}
    </>
  );
}
