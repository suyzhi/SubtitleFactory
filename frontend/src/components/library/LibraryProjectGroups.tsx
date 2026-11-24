import type { CSSProperties, MouseEvent as ReactMouseEvent } from 'react';
import * as api from '../../api/backend';
import { languageLabel } from '../../languages';
import { projectReadiness, taskLabel } from '../../projectState';
import type { Project } from '../../types';
import AppSelect from '../AppSelect';
import type { ProjectGroup } from './types';

interface Props {
  youtubeEnabled: boolean;
  backendStatus: 'connecting' | 'connected' | 'error';
  libraryFirstLoad: boolean;
  librarySearch: string;
  projectsCount: number;
  playlistBatchesCount: number;
  activeProjectId: string | null;
  projectGroups: ProjectGroup[];
  knownProjectGroups: string[];
  collapsedProjectGroups: Set<string>;
  groupEditorProjectId: string | null;
  groupDraft: string;
  contextMenuProjectId: string | null;
  removingProjectIds: Set<string>;
  onImportLocal: () => void;
  onSelectProject: (project: Project) => void;
  onToggleProjectGroup: (key: string) => void;
  onOpenProjectMenu: (event: ReactMouseEvent, project: Project, trashed?: boolean) => void;
  onGroupDraftChange: (value: string) => void;
  onSaveProjectGroup: (project: Project) => void;
  onCloseGroupEditor: () => void;
}

export default function LibraryProjectGroups({
  youtubeEnabled,
  backendStatus,
  libraryFirstLoad,
  librarySearch,
  projectsCount,
  playlistBatchesCount,
  activeProjectId,
  projectGroups,
  knownProjectGroups,
  collapsedProjectGroups,
  groupEditorProjectId,
  groupDraft,
  contextMenuProjectId,
  removingProjectIds,
  onImportLocal,
  onSelectProject,
  onToggleProjectGroup,
  onOpenProjectMenu,
  onGroupDraftChange,
  onSaveProjectGroup,
  onCloseGroupEditor,
}: Props) {
  return (
    <>
      {projectGroups.map(group => {
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

      {(backendStatus === 'connecting' || libraryFirstLoad) &&
        !projectsCount &&
        !playlistBatchesCount && (
          <div className="library-skeleton" aria-label="正在载入项目">
            <i />
            <i />
            <i />
          </div>
        )}

      {backendStatus !== 'connecting' &&
        !libraryFirstLoad &&
        !projectsCount &&
        !playlistBatchesCount &&
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
    </>
  );
}
