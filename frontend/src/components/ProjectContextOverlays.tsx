import type { RefObject } from 'react';
import type { ProjectContextMenuState } from '../hooks/useLibrary';
import type { Project } from '../types';

interface Props {
  contextMenu: ProjectContextMenuState | null;
  contextMenuRef: RefObject<HTMLDivElement | null>;
  renameProjectState: Project | null;
  renameDraft: string;
  dragActive: boolean;
  toast: string;
  onCloseContextMenu: () => void;
  onSelectProject: (project: Project) => void;
  onStartRename: (project: Project) => void;
  onOpenGroupEditor: (project: Project) => void;
  onMoveToTrash: (project: Project) => void;
  onRestoreProject: (project: Project) => void;
  onDeleteForever: (project: Project) => void;
  onRenameDraftChange: (value: string) => void;
  onCloseRename: () => void;
  onSaveRename: () => void;
}

export default function ProjectContextOverlays({
  contextMenu,
  contextMenuRef,
  renameProjectState,
  renameDraft,
  dragActive,
  toast,
  onCloseContextMenu,
  onSelectProject,
  onStartRename,
  onOpenGroupEditor,
  onMoveToTrash,
  onRestoreProject,
  onDeleteForever,
  onRenameDraftChange,
  onCloseRename,
  onSaveRename,
}: Props) {
  return (
    <>
      {contextMenu && (
        <div
          ref={contextMenuRef}
          className="context-menu"
          role="menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onPointerDown={event => event.stopPropagation()}
          onKeyDown={event => {
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const items = Array.from(
              event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'),
            );
            const current = Math.max(0, items.indexOf(document.activeElement as HTMLButtonElement));
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? items.length - 1
                  : event.key === 'ArrowDown'
                    ? (current + 1) % items.length
                    : (current - 1 + items.length) % items.length;
            items[next]?.focus();
          }}
        >
          {contextMenu.trashed ? (
            <>
              <button
                role="menuitem"
                onClick={() => {
                  onCloseContextMenu();
                  onRestoreProject(contextMenu.project);
                }}
              >
                ↶ <span>恢复项目</span>
              </button>
              <button
                className="danger"
                role="menuitem"
                onClick={() => {
                  onCloseContextMenu();
                  onDeleteForever(contextMenu.project);
                }}
              >
                ⌫ <span>永久删除…</span>
              </button>
            </>
          ) : (
            <>
              <button
                role="menuitem"
                onClick={() => {
                  onCloseContextMenu();
                  onSelectProject(contextMenu.project);
                }}
              >
                ↗ <span>打开</span>
              </button>
              <button
                role="menuitem"
                onClick={() => {
                  onStartRename(contextMenu.project);
                  onCloseContextMenu();
                }}
              >
                ✎ <span>重命名…</span>
              </button>
              <button
                role="menuitem"
                disabled={contextMenu.project.source_type !== 'youtube'}
                onClick={() => {
                  onOpenGroupEditor(contextMenu.project);
                  onCloseContextMenu();
                }}
              >
                ⌘ <span>移动分组…</span>
              </button>
              <hr />
              <button
                className="danger"
                role="menuitem"
                onClick={() => {
                  onCloseContextMenu();
                  onMoveToTrash(contextMenu.project);
                }}
              >
                ♲ <span>移入回收站</span>
              </button>
            </>
          )}
        </div>
      )}

      {renameProjectState && (
        <div className="modal-backdrop" onMouseDown={onCloseRename}>
          <form
            className="rename-dialog"
            onMouseDown={event => event.stopPropagation()}
            onSubmit={event => {
              event.preventDefault();
              onSaveRename();
            }}
          >
            <header>
              <div>
                <h2>重命名项目</h2>
                <p>媒体与字幕文件不会移动。</p>
              </div>
              <button type="button" aria-label="关闭" onClick={onCloseRename}>
                ×
              </button>
            </header>
            <input
              autoFocus
              maxLength={120}
              value={renameDraft}
              onChange={event => onRenameDraftChange(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Escape') onCloseRename();
              }}
            />
            <footer>
              <button type="button" className="button secondary" onClick={onCloseRename}>
                取消
              </button>
              <button className="button primary" disabled={!renameDraft.trim()}>
                保存
              </button>
            </footer>
          </form>
        </div>
      )}

      {dragActive && (
        <div className="drop-overlay">
          <div>
            <span>⇩</span>
            <strong>松开以导入视频</strong>
            <small>支持 MP4、MKV、MOV、WebM 和 AVI</small>
          </div>
        </div>
      )}

      {toast && (
        <div className="studio-toast" role="status" aria-live="polite">
          <span>✓</span>
          {toast}
        </div>
      )}
    </>
  );
}
