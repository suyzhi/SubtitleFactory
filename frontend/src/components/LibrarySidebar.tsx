import type { MouseEvent as ReactMouseEvent, RefObject } from 'react';
import type * as api from '../api/backend';
import type { PlaylistBatchDetail, Project, SegmentSearchHit } from '../types';
import LibraryControls from './LibraryControls';
import LibraryHeader from './library/LibraryHeader';
import LibraryProjectGroups from './library/LibraryProjectGroups';
import LibrarySearchResults from './library/LibrarySearchResults';
import LibraryTrashList from './library/LibraryTrashList';
import type { ProjectGroup } from './library/types';
import PlaylistBatchGroups from './PlaylistBatchGroups';

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
  onImportProjectPackage: () => void;
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
  onImportProjectPackage,
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
      <LibraryHeader
        youtubeEnabled={youtubeEnabled}
        filesystemAutomationEnabled={filesystemAutomationEnabled}
        backendStatus={backendStatus}
        motionEnabled={motionEnabled}
        librarySearch={librarySearch}
        libraryTotal={libraryTotal}
        projects={projects}
        trashProjects={trashProjects}
        playlistBatches={playlistBatches}
        onImportLocal={onImportLocal}
        onImportProjectPackage={onImportProjectPackage}
        onOpenProductionCenter={onOpenProductionCenter}
        onOpenLinkPopover={onOpenLinkPopover}
      />

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
          <LibrarySearchResults
            librarySearch={librarySearch}
            librarySearchHits={librarySearchHits}
            librarySearchTotal={librarySearchTotal}
            librarySearchPage={librarySearchPage}
            librarySearchLoading={librarySearchLoading}
            librarySearchSelection={librarySearchSelection}
            librarySearchFacets={librarySearchFacets}
            librarySearchFilters={librarySearchFilters}
            projectMatchCount={projects.length}
            onLibrarySearchSelectionChange={onLibrarySearchSelectionChange}
            onLibrarySearchFiltersChange={onLibrarySearchFiltersChange}
            onLibrarySearchPageChange={onLibrarySearchPageChange}
            onOpenSearchHit={onOpenSearchHit}
          />
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

        {libraryView === 'projects' && (
          <LibraryProjectGroups
            youtubeEnabled={youtubeEnabled}
            backendStatus={backendStatus}
            libraryFirstLoad={libraryFirstLoad}
            librarySearch={librarySearch}
            projectsCount={projects.length}
            playlistBatchesCount={playlistBatches.length}
            activeProjectId={activeProjectId}
            projectGroups={projectGroups}
            knownProjectGroups={knownProjectGroups}
            collapsedProjectGroups={collapsedProjectGroups}
            groupEditorProjectId={groupEditorProjectId}
            groupDraft={groupDraft}
            contextMenuProjectId={contextMenuProjectId}
            removingProjectIds={removingProjectIds}
            onImportLocal={onImportLocal}
            onSelectProject={onSelectProject}
            onToggleProjectGroup={onToggleProjectGroup}
            onOpenProjectMenu={onOpenProjectMenu}
            onGroupDraftChange={onGroupDraftChange}
            onSaveProjectGroup={onSaveProjectGroup}
            onCloseGroupEditor={onCloseGroupEditor}
          />
        )}

        {libraryView === 'trash' && (
          <LibraryTrashList
            trashProjects={trashProjects}
            removingProjectIds={removingProjectIds}
            onOpenProjectMenu={onOpenProjectMenu}
          />
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
            <button className="sidebar-action" onClick={onImportProjectPackage}>
              ⇧ 导入项目包
            </button>
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
