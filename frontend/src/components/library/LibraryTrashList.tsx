import type { MouseEvent as ReactMouseEvent } from 'react';
import type { Project } from '../../types';

interface Props {
  trashProjects: Project[];
  removingProjectIds: Set<string>;
  onOpenProjectMenu: (event: ReactMouseEvent, project: Project, trashed?: boolean) => void;
}

export default function LibraryTrashList({
  trashProjects,
  removingProjectIds,
  onOpenProjectMenu,
}: Props) {
  if (!trashProjects.length) {
    return (
      <div className="project-empty">
        <span>♲</span>
        <strong>回收站为空</strong>
        <small>移入回收站的项目会保留媒体与字幕。</small>
      </div>
    );
  }

  return (
    <>
      {trashProjects.slice(0, 40).map(project => (
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
    </>
  );
}
