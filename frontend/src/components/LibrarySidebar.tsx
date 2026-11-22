import type { CSSProperties, MouseEvent as ReactMouseEvent, RefObject } from 'react';
import * as api from '../api/backend';
import { languageLabel } from '../languages';
import { projectReadiness, taskLabel } from '../projectState';
import type { PlaylistBatchDetail, Project, SegmentSearchHit } from '../types';
import { highlightedSnippet, libraryTimecode } from '../utils/library';
import { isPlaylistBatchActive } from '../utils/playlist';
import AnimatedNumber from './AnimatedNumber';
import AppSelect from './AppSelect';
import LibraryControls from './LibraryControls';
import PlaylistBatchGroups from './PlaylistBatchGroups';

interface ProjectGroup {
  key: string;
  label: string;
  projects: Project[];
  rank: number;
}

interface Props {
  youtubeEnabled: boolean;
  filesystemAutomationEnabled: boolean;
  backendStatus: 'connecting' | 'connected' | 'error';
  motionEnabled: boolean;
  compactLibrary: boolean;
  layoutSwap: boolean;
  libraryTabsRef: RefObject<HTMLDivElement | null>;
  libraryView: 'projects' | 'trash';
  libraryPage: number;
  libraryPages: number;
  libraryTotal: number;
  libraryLoading: boolean;
  libraryFirstLoad: boolean;
  libraryError: string;
  libraryStatus: string;
  librarySearch: string;
  librarySort: string;
  librarySearchHits: SegmentSearchHit[];
  librarySearchTotal: number;
  librarySearchPage: number;
  librarySearchLoading: boolean;
  librarySearchSelection: number;
  librarySearchFacets: api.SegmentSearchFacets;
  librarySearchFilters: api.SegmentSearchFilters;
  projects: Project[];
  trashProjects: Project[];
  activeProjectId: string | null;
  playlistBatches: PlaylistBatchDetail[];
  openPlaylistBatches: Record<string, boolean>;
  playlistWorkflow: {
    model: string;
    runtime: string;
    language: string;
    target_language: string;
    clean_target_length: number;
  };
  projectGroups: ProjectGroup[];
  knownProjectGroups: string[];
  collapsedProjectGroups: Set<string>;
  groupEditorProjectId: string | null;
  groupDraft: string;
  contextMenuProjectId: string | null;
  removingProjectIds: Set<string>;
  onImportLocal: () => void;
  onOpenProductionCenter: () => void;
  onOpenLinkPopover: () => void;
  onLibraryViewChange: (view: 'projects' | 'trash') => void;
  onLibraryPageChange: (page: number) => void;
  onLibraryStatusChange: (status: string) => void;
  onLibraryRetry: () => void;
  onToggleCompactLibrary: () => void;
  onLibrarySearchChange: (value: string) => void;
  onLibrarySortChange: (value: string) => void;
  onLibrarySearchSelectionChange: (value: number | ((prev: number) => number)) => void;
  onLibrarySearchFiltersChange: (
    value:
      | api.SegmentSearchFilters
      | ((prev: api.SegmentSearchFilters) => api.SegmentSearchFilters),
  ) => void;
  onLibrarySearchPageChange: (value: number | ((prev: number) => number)) => void;
  onOpenSearchHit: (hit: SegmentSearchHit) => void;
  onTogglePlaylistBatch: (id: string, open: boolean) => void;
  onRefreshPlaylistBatches: () => void;
  onSelectProject: (project: Project) => void;
  onToggleProjectGroup: (key: string) => void;
  onOpenProjectMenu: (event: ReactMouseEvent, project: Project, trashed?: boolean) => void;
  onGroupDraftChange: (value: string) => void;
  onSaveProjectGroup: (project: Project) => void;
  onCloseGroupEditor: () => void;
  onClearTrash: () => void;
  onMessage: (message: string, duration?: number) => void;
}

export default function LibrarySidebar({
  youtubeEnabled,
  filesystemAutomationEnabled,
  backendStatus,
  motionEnabled,
  compactLibrary,
  layoutSwap,
  libraryTabsRef,
  libraryView,
  libraryPage,
  libraryPages,
  libraryTotal,
  libraryLoading,
  libraryFirstLoad,
  libraryError,
  libraryStatus,
  librarySearch,
  librarySort,
  librarySearchHits,
  librarySearchTotal,
  librarySearchPage,
  librarySearchLoading,
  librarySearchSelection,
  librarySearchFacets,
  librarySearchFilters,
  projects,
  trashProjects,
  activeProjectId,
  playlistBatches,
  openPlaylistBatches,
  playlistWorkflow,
  projectGroups,
  knownProjectGroups,
  collapsedProjectGroups,
  groupEditorProjectId,
  groupDraft,
  contextMenuProjectId,
  removingProjectIds,
  onImportLocal,
  onOpenProductionCenter,
  onOpenLinkPopover,
  onLibraryViewChange,
  onLibraryPageChange,
  onLibraryStatusChange,
  onLibraryRetry,
  onToggleCompactLibrary,
  onLibrarySearchChange,
  onLibrarySortChange,
  onLibrarySearchSelectionChange,
  onLibrarySearchFiltersChange,
  onLibrarySearchPageChange,
  onOpenSearchHit,
  onTogglePlaylistBatch,
  onRefreshPlaylistBatches,
  onSelectProject,
  onToggleProjectGroup,
  onOpenProjectMenu,
  onGroupDraftChange,
  onSaveProjectGroup,
  onCloseGroupEditor,
  onClearTrash,
  onMessage,
}: Props) {
  return (
    <aside
      className={`project-sidebar ${compactLibrary ? 'library-list-view' : ''} ${layoutSwap ? 'layout-swap' : ''}`}
    >
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
          {filesystemAutomationEnabled && (
            <button
              className="button secondary"
              disabled={backendStatus !== 'connected'}
              onClick={onOpenProductionCenter}
            >
              批量与监听
            </button>
          )}
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
            (() => {
              const active = playlistBatches.filter(item =>
                isPlaylistBatchActive(item.batch.status),
              ).length;
              const attention = playlistBatches.filter(item =>
                ['partial', 'failed'].includes(item.batch.status),
              ).length;
              return (
                <div
                  title={
                    playlistBatches.length ? `${playlistBatches.length} 个播放列表` : '暂无进行中的队列'
                  }
                >
                  <strong>
                    <AnimatedNumber value={active || attention} animate={motionEnabled} />
                  </strong>
                  <span>{active || !attention ? '个批量任务进行中' : '个批量任务需处理'}</span>
                </div>
              );
            })()
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

      <div
        ref={libraryTabsRef}
        className="library-switcher sliding-tabs"
        role="tablist"
        aria-label="项目库视图"
      >
        <button
          role="tab"
          aria-selected={libraryView === 'projects'}
          className={libraryView === 'projects' ? 'active' : ''}
          onClick={() => onLibraryViewChange('projects')}
        >
          项目
        </button>
        <button
          role="tab"
          aria-selected={libraryView === 'trash'}
          className={libraryView === 'trash' ? 'active' : ''}
          onClick={() => onLibraryViewChange('trash')}
        >
          回收站
        </button>
      </div>

      <LibraryControls
        page={libraryPage}
        pages={libraryPages}
        total={libraryTotal}
        loading={libraryLoading}
        error={libraryError}
        compact={compactLibrary}
        status={libraryStatus}
        onPage={onLibraryPageChange}
        onStatus={onLibraryStatusChange}
        onRetry={onLibraryRetry}
        onCompact={onToggleCompactLibrary}
      />

      {libraryView === 'projects' && (
        <div className="library-filters">
          <input
            type="search"
            value={librarySearch}
            onChange={event => onLibrarySearchChange(event.target.value)}
            placeholder="搜索项目或所有字幕"
            aria-label="搜索项目或所有字幕"
            onKeyDown={event => {
              if (event.key === 'Escape') {
                event.preventDefault();
                onLibrarySearchChange('');
                onLibrarySearchSelectionChange(-1);
                return;
              }
              if (
                !librarySearchHits.length ||
                !['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)
              ) {
                return;
              }
              event.preventDefault();
              if (event.key === 'ArrowDown') {
                onLibrarySearchSelectionChange(value =>
                  Math.min(librarySearchHits.length - 1, value + 1),
                );
              }
              if (event.key === 'ArrowUp') {
                onLibrarySearchSelectionChange(value => Math.max(0, value <= 0 ? 0 : value - 1));
              }
              if (event.key === 'Enter') {
                const hit = librarySearchHits[Math.max(0, librarySearchSelection)];
                if (hit) onOpenSearchHit(hit);
              }
            }}
          />
          <select
            aria-label="项目排序"
            value={librarySort}
            onChange={event => onLibrarySortChange(event.target.value)}
          >
            <option value="updated_desc">最近更新</option>
            <option value="created_desc">最近创建</option>
            <option value="name_asc">名称 A–Z</option>
            <option value="name_desc">名称 Z–A</option>
          </select>
        </div>
      )}

      <div className="project-list">
        {libraryView === 'projects' && librarySearch.trim() && (
          <section className="library-unified-results">
            {librarySearch.trim().length === 1 ? (
              <div className="search-input-hint">
                再输入一个字即可搜索全部字幕，避免扫描整个项目库。
              </div>
            ) : (
              <>
                <details className="search-filter-panel">
                  <summary>
                    筛选字幕结果{' '}
                    <span>{Object.values(librarySearchFilters).filter(Boolean).length || ''}</span>
                  </summary>
                  <div>
                    <label>
                      项目
                      <select
                        value={String(librarySearchFilters.project_id || '')}
                        onChange={event =>
                          onLibrarySearchFiltersChange(current => ({
                            ...current,
                            project_id: event.target.value || undefined,
                          }))
                        }
                      >
                        <option value="">全部项目</option>
                        {(librarySearchFacets.projects || []).map(item => (
                          <option value={item.id} key={item.id}>
                            {item.title}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      分组
                      <select
                        value={String(librarySearchFilters.group_name || '')}
                        onChange={event =>
                          onLibrarySearchFiltersChange(current => ({
                            ...current,
                            group_name: event.target.value || undefined,
                          }))
                        }
                      >
                        <option value="">全部分组</option>
                        {(librarySearchFacets.groups || []).map(item => (
                          <option value={item} key={item}>
                            {item}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      源语言
                      <select
                        value={String(librarySearchFilters.source_language || '')}
                        onChange={event =>
                          onLibrarySearchFiltersChange(current => ({
                            ...current,
                            source_language: event.target.value || undefined,
                          }))
                        }
                      >
                        <option value="">全部</option>
                        {Array.from(
                          new Set(
                            (librarySearchFacets.projects || [])
                              .map(item => item.source_language)
                              .filter(Boolean),
                          ),
                        ).map(item => (
                          <option value={item} key={item}>
                            {languageLabel(item)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      目标语言
                      <select
                        value={String(librarySearchFilters.target_language || '')}
                        onChange={event =>
                          onLibrarySearchFiltersChange(current => ({
                            ...current,
                            target_language: event.target.value || undefined,
                          }))
                        }
                      >
                        <option value="">全部</option>
                        {Array.from(
                          new Set(
                            (librarySearchFacets.projects || [])
                              .map(item => item.target_language)
                              .filter(Boolean),
                          ),
                        ).map(item => (
                          <option value={item} key={item}>
                            {languageLabel(item)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      创建日期从
                      <input
                        type="date"
                        value={String(librarySearchFilters.created_from || '')}
                        onChange={event =>
                          onLibrarySearchFiltersChange(current => ({
                            ...current,
                            created_from: event.target.value || undefined,
                          }))
                        }
                      />
                    </label>
                    <label>
                      到
                      <input
                        type="date"
                        value={String(librarySearchFilters.created_to || '')}
                        onChange={event =>
                          onLibrarySearchFiltersChange(current => ({
                            ...current,
                            created_to: event.target.value || undefined,
                          }))
                        }
                      />
                    </label>
                    <button
                      className="button secondary"
                      onClick={() => onLibrarySearchFiltersChange({})}
                    >
                      清除筛选
                    </button>
                  </div>
                </details>
                <header className="search-result-heading">
                  <strong>字幕命中</strong>
                  <span>{librarySearchLoading ? '搜索中…' : `${librarySearchTotal} 条`}</span>
                </header>
                <div className="subtitle-search-hits">
                  {librarySearchHits.map((hit, index) => (
                    <button
                      key={hit.segment_id}
                      className={librarySearchSelection === index ? 'selected' : ''}
                      onMouseEnter={() => onLibrarySearchSelectionChange(index)}
                      onClick={() => onOpenSearchHit(hit)}
                    >
                      <span className="search-hit-time">{libraryTimecode(hit.start)}</span>
                      <span>
                        <strong>{hit.project_title}</strong>
                        <small>
                          {hit.playlist_title ? `${hit.playlist_title} · ` : ''}
                          {hit.group_name ? `${hit.group_name} · ` : ''}
                          命中{' '}
                          {hit.match_fields
                            .map(field =>
                              field === 'clean_text'
                                ? '整理后原文'
                                : field === 'translated_text'
                                  ? '译文'
                                  : field === 'raw_text'
                                    ? '原始转写'
                                    : '项目标题',
                            )
                            .join('、')}
                        </small>
                        <p>{highlightedSnippet(hit.snippet, librarySearch)}</p>
                      </span>
                      <em>打开 ↗</em>
                    </button>
                  ))}
                </div>
                {!librarySearchLoading && !librarySearchHits.length && (
                  <div className="search-empty">没有匹配的字幕。</div>
                )}
                {librarySearchTotal > 50 && (
                  <div className="search-pagination">
                    <button
                      disabled={librarySearchPage <= 1}
                      onClick={() => onLibrarySearchPageChange(page => page - 1)}
                    >
                      上一页
                    </button>
                    <span>
                      {librarySearchPage} / {Math.ceil(librarySearchTotal / 50)}
                    </span>
                    <button
                      disabled={librarySearchPage >= Math.ceil(librarySearchTotal / 50)}
                      onClick={() => onLibrarySearchPageChange(page => page + 1)}
                    >
                      下一页
                    </button>
                  </div>
                )}
                <header className="search-result-heading project-hits-heading">
                  <strong>项目命中</strong>
                  <span>{projects.length}</span>
                </header>
              </>
            )}
          </section>
        )}

        {youtubeEnabled && libraryView === 'projects' && (
          <PlaylistBatchGroups
            batches={playlistBatches}
            search={librarySearch}
            open={openPlaylistBatches}
            workflow={playlistWorkflow}
            onToggle={onTogglePlaylistBatch}
            onOpenProject={onSelectProject}
            onChanged={onRefreshPlaylistBatches}
            onMessage={onMessage}
          />
        )}

        {libraryView === 'projects' &&
          projectGroups.map(group => {
            const collapsed = collapsedProjectGroups.has(group.key);
            return (
              <section className="project-group" key={group.key}>
                <button
                  className="project-group-header"
                  aria-expanded={!collapsed}
                  onClick={() => onToggleProjectGroup(group.key)}
                >
                  <span>
                    <i className="chevron" aria-hidden="true" />
                    {group.label}
                    <small>{group.projects.length}</small>
                  </span>
                </button>
                {!collapsed && (
                  <div className="project-group-items">
                    {group.projects.map((project, index) => {
                      const thumbnailUrl = api.getProjectThumbnailUrl(project);
                      const editingGroup = groupEditorProjectId === project.id;
                      return (
                        <div
                          className={`project-card-shell ${removingProjectIds.has(project.id) ? 'removing' : ''}`}
                          key={project.id}
                          style={
                            {
                              '--i': Math.min(index, 11),
                              '--vt-card': `card-${project.id}`,
                              '--vt-thumb': `thumb-${project.id}`,
                            } as CSSProperties
                          }
                          onContextMenu={event => onOpenProjectMenu(event, project)}
                        >
                          <button
                            className={`project-card ${activeProjectId === project.id ? 'active' : ''}`}
                            onClick={() => onSelectProject(project)}
                          >
                            <span className="project-thumb">
                              <span className="project-thumb-fallback">
                                {project.source_type === 'youtube' ? '▶' : '▣'}
                              </span>
                              {thumbnailUrl && (
                                <img
                                  src={thumbnailUrl}
                                  alt=""
                                  loading="lazy"
                                  onError={event => {
                                    event.currentTarget.style.display = 'none';
                                  }}
                                />
                              )}
                            </span>
                            <span className="project-card-copy">
                              <strong>{project.title}</strong>
                              <small>
                                {languageLabel(project.language)} · {project.segments_count} 条 ·{' '}
                                {project.created_at.slice(0, 10)}
                              </small>
                              <span className="media-mode-badge">{projectReadiness(project)}</span>
                              {project.latest_task_status && (
                                <em className={`project-task-hint ${project.latest_task_status}`}>
                                  最近任务：{taskLabel(project.latest_task_status)}
                                  {project.latest_task_status === 'failed'
                                    ? ` · ${project.latest_task_message || ''}`
                                    : ''}
                                </em>
                              )}
                            </span>
                          </button>
                          <button
                            type="button"
                            className="project-more"
                            aria-label={`更多项目操作：${project.title}`}
                            aria-haspopup="menu"
                            aria-expanded={contextMenuProjectId === project.id}
                            onClick={event => onOpenProjectMenu(event, project)}
                          >
                            •••
                          </button>
                          {editingGroup && (
                            <div className="project-group-editor">
                              <AppSelect
                                value={groupDraft}
                                onChange={onGroupDraftChange}
                                label="项目分组"
                                placeholder="搜索或输入分组名称"
                                searchable
                                allowCustom
                                options={knownProjectGroups.map(name => ({
                                  value: name,
                                  label: name,
                                }))}
                              />
                              <button onClick={() => onSaveProjectGroup(project)}>保存</button>
                              <button aria-label="取消" onClick={onCloseGroupEditor}>
                                ×
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>
            );
          })}

        {libraryView === 'projects' &&
          (backendStatus === 'connecting' || libraryFirstLoad) &&
          !projects.length &&
          !playlistBatches.length && (
            <div className="library-skeleton" aria-label="正在载入项目">
              <i />
              <i />
              <i />
            </div>
          )}

        {libraryView === 'projects' &&
          backendStatus !== 'connecting' &&
          !libraryFirstLoad &&
          !projects.length &&
          !playlistBatches.length &&
          (librarySearch.trim() ? (
            <div className="search-empty">没有名称匹配的项目</div>
          ) : (
            <section className="library-welcome" aria-label="开始第一个项目">
              <button
                type="button"
                className="welcome-drop"
                onClick={onImportLocal}
                disabled={backendStatus !== 'connected'}
              >
                <svg viewBox="0 0 48 48" aria-hidden="true">
                  <rect x="6" y="10" width="36" height="28" rx="6" />
                  <path d="M20 19.5v9l7.5-4.5z" fill="currentColor" />
                  <path d="M6 17h36M6 31h36" opacity=".35" />
                </svg>
                <strong>把视频拖到这里，或点击导入</strong>
                <small>
                  支持 MP4、MOV、MKV、WebM 和 AVI，文件只保存在这台 Mac 上
                  {youtubeEnabled ? '；也可以用上方的“添加链接”' : ''}
                </small>
              </button>
              <ol className="welcome-steps">
                <li>
                  <b>1</b>
                  <strong>导入视频</strong>
                  <small>本地文件{youtubeEnabled ? '或视频链接' : ''}</small>
                </li>
                <li>
                  <b>2</b>
                  <strong>生成字幕</strong>
                  <small>在本机转写，自动对齐时间轴</small>
                </li>
                <li>
                  <b>3</b>
                  <strong>校对与导出</strong>
                  <small>SRT、ASS 或带字幕的视频</small>
                </li>
              </ol>
            </section>
          ))}

        {libraryView === 'trash' &&
          trashProjects.slice(0, 40).map(project => (
            <button
              className={`project-card trash-card ${removingProjectIds.has(project.id) ? 'removing' : ''}`}
              key={project.id}
              onContextMenu={event => onOpenProjectMenu(event, project, true)}
              onClick={event => onOpenProjectMenu(event, project, true)}
            >
              <span className="project-thumb">
                <span className="project-thumb-fallback">♲</span>
              </span>
              <span className="project-card-copy">
                <strong>{project.title}</strong>
                <small>{project.deleted_at?.slice(0, 10) || '已删除'} · 媒体仍保留</small>
              </span>
              <span className="project-more">•••</span>
            </button>
          ))}

        {libraryView === 'trash' && !trashProjects.length && (
          <div className="project-empty">
            <span>♲</span>
            <strong>回收站为空</strong>
            <small>移入回收站的项目会保留媒体与字幕。</small>
          </div>
        )}
      </div>

      <div className="sidebar-footer">
        {libraryView === 'trash' ? (
          <button
            className="sidebar-action danger"
            disabled={!trashProjects.length}
            onClick={onClearTrash}
          >
            清空回收站
          </button>
        ) : (
          <>
            <button className="sidebar-action" onClick={onImportLocal}>
              ＋ 导入视频
            </button>
            {filesystemAutomationEnabled && (
              <button className="sidebar-action" onClick={onOpenProductionCenter}>
                ▦ 批量与监听
              </button>
            )}
            {youtubeEnabled && (
              <button className="sidebar-action" onClick={onOpenLinkPopover}>
                ⌁ 添加链接
              </button>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
