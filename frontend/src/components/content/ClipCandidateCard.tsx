import { useEffect, useState, type PointerEvent } from 'react';
import * as api from '../../api/backend';
import type { ClipAspectRatio, ClipCandidate, ClipLayout, ClipSet } from '../../types';
import AppSelect from '../AppSelect';
import { errorMessage, timecode } from './helpers';

interface Props {
  candidate: ClipCandidate;
  previewImage?: string | null;
  onChange: (next: ClipSet) => void;
  onPreview: (start: number, end: number) => void;
  onMessage: (message: string) => void;
}

export default function ClipCandidateCard({
  candidate,
  previewImage,
  onChange,
  onPreview,
  onMessage,
}: Props) {
  const [draft, setDraft] = useState(candidate);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => setDraft(candidate), [candidate]);

  const mutateLayout = (aspect: ClipAspectRatio, patch: Partial<ClipLayout>) => {
    setDraft(current => ({
      ...current,
      layouts: current.layouts.map(item =>
        item.aspect_ratio === aspect ? { ...item, ...patch } : item,
      ),
    }));
  };

  const moveFocalPoint = (event: PointerEvent<HTMLDivElement>, aspect: ClipAspectRatio) => {
    if (event.type === 'pointermove' && event.buttons !== 1) return;
    if (event.type === 'pointerdown') event.currentTarget.setPointerCapture(event.pointerId);
    const bounds = event.currentTarget.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    mutateLayout(aspect, {
      focal_x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      focal_y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
    });
  };

  const saveCandidate = async () => {
    setBusy(true);
    setError('');
    try {
      const next = await api.updateClipCandidate(candidate.id, {
        title: draft.title,
        start: Number(draft.start),
        end: Number(draft.end),
        selected: draft.selected,
        expected_revision: candidate.revision,
        confirm_current_source: candidate.stale,
      });
      onChange(next);
      onMessage('短片范围已保存');
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const saveLayout = async (layout: ClipLayout) => {
    setBusy(true);
    setError('');
    try {
      const next = await api.updateClipLayout(candidate.id, layout.aspect_ratio, {
        enabled: layout.enabled,
        composition: layout.composition,
        focal_x: layout.focal_x,
        focal_y: layout.focal_y,
        subtitle_mode: layout.subtitle_mode,
        style: layout.style || {},
        expected_revision:
          candidate.layouts.find(item => item.aspect_ratio === layout.aspect_ratio)?.revision || 0,
      });
      onChange(next);
      onMessage(`${layout.aspect_ratio} 构图已保存`);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const duration = Number(draft.end) - Number(draft.start);
  const recommended = duration >= 30 && duration <= 90;

  return (
    <article className={`clip-candidate-card ${draft.selected ? 'selected' : ''}`}>
      <header>
        <label className="clip-select">
          <input
            type="checkbox"
            checked={draft.selected}
            onChange={event => setDraft({ ...draft, selected: event.target.checked })}
          />
          <span>选择渲染</span>
        </label>
        <span className="clip-score">
          {Math.round(candidate.score * (candidate.score <= 1 ? 100 : 1))} 分
        </span>
        {candidate.stale && <em>字幕已更新</em>}
      </header>
      <input
        className="clip-title"
        value={draft.title}
        onChange={event => setDraft({ ...draft, title: event.target.value })}
      />
      <p>
        <strong>开场钩子：</strong>
        {candidate.hook || '—'}
      </p>
      <p>
        <strong>推荐理由：</strong>
        {candidate.reason || '—'}
      </p>
      <div className="clip-range-editor">
        <label>
          开始（秒）
          <input
            type="number"
            min={0}
            step={0.1}
            value={draft.start}
            onChange={event => setDraft({ ...draft, start: Number(event.target.value) })}
          />
        </label>
        <span>
          {timecode(draft.start)} → {timecode(draft.end)}
          <small className={recommended ? '' : 'warning'}>
            {duration.toFixed(1)} 秒
            {recommended ? ' · 推荐范围' : ' · 人工范围允许 15–180 秒'}
          </small>
        </span>
        <label>
          结束（秒）
          <input
            type="number"
            min={0}
            step={0.1}
            value={draft.end}
            onChange={event => setDraft({ ...draft, end: Number(event.target.value) })}
          />
        </label>
        <button className="button secondary" onClick={() => onPreview(draft.start, draft.end)}>
          循环预览
        </button>
        <button
          className="button primary"
          disabled={busy || duration < 15 || duration > 180}
          onClick={() => void saveCandidate()}
        >
          保存范围
        </button>
      </div>
      <div className="clip-layout-grid">
        {draft.layouts.map(layout => (
          <section key={layout.aspect_ratio} className={layout.enabled ? 'enabled' : ''}>
            <header>
              <label>
                <input
                  type="checkbox"
                  checked={layout.enabled}
                  onChange={event =>
                    mutateLayout(layout.aspect_ratio, { enabled: event.target.checked })
                  }
                />
                <strong>{layout.aspect_ratio}</strong>
              </label>
              <small>
                {layout.aspect_ratio === '9:16'
                  ? '1080×1920'
                  : layout.aspect_ratio === '1:1'
                    ? '1080×1080'
                    : '1920×1080'}
              </small>
            </header>
            <AppSelect
              value={layout.composition}
              onChange={composition =>
                mutateLayout(layout.aspect_ratio, {
                  composition: composition as 'blur' | 'crop',
                })
              }
              label={`${layout.aspect_ratio} 构图`}
              options={[
                { value: 'blur', label: '模糊背景', description: '完整保留原画面' },
                { value: 'crop', label: '填满裁切', description: '按焦点决定裁切中心' },
              ]}
            />
            {layout.composition === 'crop' && (
              <div className="clip-focal">
                <div
                  className={`clip-focal-preview aspect-${layout.aspect_ratio.replace(':', 'x')}`}
                  style={previewImage ? { backgroundImage: `url("${previewImage}")` } : undefined}
                  role="application"
                  aria-label={`${layout.aspect_ratio} 裁切焦点预览`}
                  onPointerDown={event => moveFocalPoint(event, layout.aspect_ratio)}
                  onPointerMove={event => moveFocalPoint(event, layout.aspect_ratio)}
                >
                  <i style={{ left: `${layout.focal_x * 100}%`, top: `${layout.focal_y * 100}%` }} />
                  <span>拖动焦点决定裁切中心</span>
                </div>
                <label>
                  水平焦点
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.01}
                    value={layout.focal_x}
                    onChange={event =>
                      mutateLayout(layout.aspect_ratio, { focal_x: Number(event.target.value) })
                    }
                  />
                </label>
                <label>
                  垂直焦点
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.01}
                    value={layout.focal_y}
                    onChange={event =>
                      mutateLayout(layout.aspect_ratio, { focal_y: Number(event.target.value) })
                    }
                  />
                </label>
              </div>
            )}
            <AppSelect
              value={layout.subtitle_mode}
              onChange={subtitle_mode =>
                mutateLayout(layout.aspect_ratio, {
                  subtitle_mode: subtitle_mode as ClipLayout['subtitle_mode'],
                })
              }
              label={`${layout.aspect_ratio} 字幕`}
              options={[
                { value: 'off', label: '关闭字幕' },
                { value: 'original', label: '原文' },
                { value: 'translated', label: '译文' },
                { value: 'bilingual', label: '双语' },
              ]}
            />
            <button
              className="button secondary"
              disabled={busy}
              onClick={() => void saveLayout(layout)}
            >
              保存此布局
            </button>
          </section>
        ))}
      </div>
      {!!candidate.renders.length && (
        <div className="clip-render-list">
          {candidate.renders.map(render => (
            <span key={render.id} className={render.status}>
              <strong>{render.aspect_ratio}</strong>
              <small>
                {render.status === 'success'
                  ? `${render.width}×${render.height} · ${Number(render.duration || 0).toFixed(1)} 秒`
                  : render.error || render.status}
              </small>
              {render.status === 'success' && (
                <button
                  onClick={() =>
                    void api
                      .downloadClipRender(
                        render.id,
                        `${candidate.title}-${render.aspect_ratio.replace(':', 'x')}.mp4`,
                      )
                      .then(saved => onMessage(saved ? '短片已保存' : '已取消保存短片'))
                      .catch(reason => setError(errorMessage(reason)))
                  }
                >
                  下载
                </button>
              )}
              {['failed', 'cancelled'].includes(render.status) && (
                <button
                  onClick={() =>
                    void api.deleteClipRender(render.id).then(() => onMessage('短片记录已删除'))
                  }
                >
                  删除
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      {error && <p className="content-error">{error}</p>}
    </article>
  );
}
