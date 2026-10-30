import { useState } from 'react';
import { TARGET_LANGUAGES } from '../languages';
import { formatTime, isTaskActive, STAGE_LABELS, TASK_STATE_LABELS } from '../format';
import type { ExportFormat, Project, SubtitleSegment, TaskStatus } from '../types';

export interface ClientPhase {
  label: string;
  progress: number;
  error?: string;
}

interface Props {
  project: Project;
  segments: SubtitleSegment[];
  task: TaskStatus | null;
  phase: ClientPhase | null;
  onClean: () => void;
  onTranslate: (targetLanguage: string) => void;
  onCancel: () => void;
  onExport: (format: ExportFormat) => void;
}

export default function ProjectView({ project, segments, task, phase, onClean, onTranslate, onCancel, onExport }: Props) {
  const [target, setTarget] = useState(project.target_language || 'zh');
  const busy = Boolean(phase && !phase.error) || isTaskActive(task);
  const hasSegments = segments.length > 0;
  const hasTranslation = segments.some(segment => segment.translated_text);

  return (
    <section className="project-view">
      <header className="page-header">
        <h1 title={project.title}>{project.title}</h1>
        <p>{project.source_type === 'youtube' ? project.source_url : '本地视频'} · {project.created_at.slice(0, 16)}</p>
      </header>

      <Progress task={task} phase={phase} onCancel={onCancel} />

      <div className="toolbar">
        <button type="button" className="button secondary" disabled={busy || !hasSegments} onClick={onClean}>AI 整理</button>
        <div className="button-group">
          <select aria-label="翻译目标语言" value={target} onChange={event => setTarget(event.target.value)} disabled={busy || !hasSegments}>
            {TARGET_LANGUAGES.map(language => <option key={language.code} value={language.code}>{language.name}</option>)}
          </select>
          <button type="button" className="button secondary" disabled={busy || !hasSegments} onClick={() => onTranslate(target)}>翻译</button>
        </div>
        <span className="spacer" />
        <ExportMenu disabled={busy || !hasSegments} hasTranslation={hasTranslation} onExport={onExport} />
      </div>

      {hasSegments ? (
        <ol className="subtitle-list">
          {segments.map(segment => (
            <li key={segment.id}>
              <time>{formatTime(segment.start)}</time>
              <div>
                <p>{segment.clean_text || segment.raw_text}</p>
                {segment.translated_text && <p className="translation">{segment.translated_text}</p>}
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <div className="empty-subtitles">{busy ? '字幕会在转写过程中逐条出现在这里。' : '还没有字幕。'}</div>
      )}
    </section>
  );
}

function Progress({ task, phase, onCancel }: { task: TaskStatus | null; phase: ClientPhase | null; onCancel: () => void }) {
  if (phase) {
    return (
      <div className={`progress-card${phase.error ? ' failed' : ''}`}>
        <div className="progress-head">
          <strong>{phase.error ? '无法开始' : phase.label}</strong>
          {!phase.error && <span>{Math.round(phase.progress)}%</span>}
        </div>
        {phase.error ? <p className="progress-message">{phase.error}</p> : <Bar value={phase.progress} />}
      </div>
    );
  }
  if (!task) return null;
  const active = isTaskActive(task);
  const stages = Object.entries(task.details?.stages || {}).filter(([key]) => key in STAGE_LABELS);
  if (!active && task.status === 'success') return null;
  return (
    <div className={`progress-card ${task.status}`}>
      <div className="progress-head">
        <strong>{active ? (task.message || '处理中…') : TASK_STATE_LABELS[task.status] || task.status}</strong>
        {active ? (
          <>
            <span>{Math.round(task.progress)}%</span>
            <button type="button" className="link-button" onClick={onCancel}>停止</button>
          </>
        ) : null}
      </div>
      {active && <Bar value={task.progress} />}
      {stages.length > 0 && (
        <ul className="stages">
          {stages.map(([key, state]) => <li key={key} className={state}>{STAGE_LABELS[key]}</li>)}
        </ul>
      )}
      {!active && (task.error || task.message) && (
        <p className="progress-message">{task.error || task.message}{task.suggestion ? `。${task.suggestion}` : ''}</p>
      )}
    </div>
  );
}

function Bar({ value }: { value: number }) {
  return <div className="bar" role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${Math.min(100, Math.max(2, value))}%` }} /></div>;
}

function ExportMenu({ disabled, hasTranslation, onExport }: { disabled: boolean; hasTranslation: boolean; onExport: (format: ExportFormat) => void }) {
  const [open, setOpen] = useState(false);
  const choose = (format: ExportFormat) => { setOpen(false); onExport(format); };
  return (
    <div className="export-menu">
      <button type="button" className="button primary" disabled={disabled} aria-expanded={open} onClick={() => setOpen(value => !value)}>导出 SRT</button>
      {open && !disabled && (
        <div className="menu" role="menu" onMouseLeave={() => setOpen(false)}>
          <button type="button" role="menuitem" onClick={() => choose('original')}>原文字幕</button>
          <button type="button" role="menuitem" disabled={!hasTranslation} onClick={() => choose('translated')}>译文字幕</button>
          <button type="button" role="menuitem" disabled={!hasTranslation} onClick={() => choose('bilingual')}>双语字幕</button>
        </div>
      )}
    </div>
  );
}
