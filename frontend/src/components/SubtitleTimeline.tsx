import { memo, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { SubtitleSegment } from '../types';
import { getProjectWaveform } from '../api/backend';

interface Props {
  segments: SubtitleSegment[];
  projectId: string;
  currentTime: number;
  duration: number;
  onSeek: (time: number) => void;
  onUpdateTime?: (index: number, update: {start?: number; end?: number}) => void;
}

function formatTime(value: number) {
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = Math.floor(value % 60);
  const tail = `${minutes.toString().padStart(hours ? 2 : 1, '0')}:${seconds.toString().padStart(2, '0')}`;
  return hours ? `${hours}:${tail}` : tail;
}

const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800];
const MAX_TRACK_WIDTH = 16000;

export default function SubtitleTimeline({ projectId, segments, currentTime, duration, onSeek, onUpdateTime }: Props) {
  const [zoom, setZoom] = useState(1);
  const [follow, setFollow] = useState(true);
  const [viewport, setViewport] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pendingAnchor = useRef<{ time: number; offset: number } | null>(null);
  const [waveform, setWaveform] = useState<{peaks: number[]; offset: number; duration: number}>({ peaks: [], offset: 0, duration: 0 });
  useEffect(() => {
    let active = true;
    setWaveform({ peaks: [], offset: 0, duration: 0 });
    getProjectWaveform(projectId, 4000)
      .then(result => { if (active) setWaveform({ peaks: result.peaks, offset: result.offset || 0, duration: result.duration }); })
      .catch(() => { /* Audio may not be extracted yet. */ });
    return () => { active = false; };
  }, [projectId]);
  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const measure = () => setViewport(element.clientWidth);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // The media length wins; the waveform and the last subtitle cover a player that has not reported yet.
  const total = Math.max(duration, waveform.offset + waveform.duration, segments.at(-1)?.end ?? 0, 1);
  const fitWidth = Math.max(320, viewport - 2);
  const maxZoom = Math.max(1, MAX_TRACK_WIDTH / fitWidth);
  const width = Math.min(MAX_TRACK_WIDTH, fitWidth * zoom);
  const pixelsPerSecond = width / total;
  const tick = TICK_STEPS.find(step => step * pixelsPerSecond >= 84) ?? TICK_STEPS.at(-1)!;
  const active = segments.find(segment => currentTime >= segment.start && currentTime < segment.end);

  const changeZoom = (next: number, anchorClientX?: number) => {
    const element = scrollRef.current;
    const clamped = Math.max(1, Math.min(maxZoom, next));
    if (element) {
      const rect = element.getBoundingClientRect();
      const offset = anchorClientX === undefined ? element.clientWidth / 2 : anchorClientX - rect.left;
      pendingAnchor.current = { time: (element.scrollLeft + offset) / pixelsPerSecond, offset };
    }
    setZoom(clamped);
  };
  useLayoutEffect(() => {
    const element = scrollRef.current;
    const anchor = pendingAnchor.current;
    if (!element || !anchor) return;
    pendingAnchor.current = null;
    element.scrollLeft = Math.max(0, anchor.time * pixelsPerSecond - anchor.offset);
  }, [pixelsPerSecond]);

  // Keep the playhead in view while playing, without fighting a user who scrolls away.
  useEffect(() => {
    const element = scrollRef.current;
    if (!element || !follow || zoom <= 1) return;
    const x = currentTime * pixelsPerSecond;
    if (x < element.scrollLeft + 40 || x > element.scrollLeft + element.clientWidth - 80) {
      element.scrollLeft = Math.max(0, x - element.clientWidth * .3);
    }
  }, [currentTime, follow, pixelsPerSecond, zoom]);

  return (
    <div className="subtitle-timeline" aria-label="字幕时间轴">
      <div className="timeline-toolbar">
        <strong>时间轴</strong>
        <span className="timeline-clock">{formatTime(currentTime)} / {formatTime(total)}</span>
        {active && <span className="timeline-active" title={active.clean_text || active.raw_text}>#{active.index} {active.clean_text || active.raw_text}</span>}
        <span className="timeline-controls">
          <button type="button" aria-pressed={follow} className={follow ? 'active' : ''} onClick={() => setFollow(value => !value)} title="播放时自动滚动到播放头">跟随</button>
          <button type="button" aria-label="缩小时间轴" disabled={zoom <= 1} onClick={() => changeZoom(zoom / 1.6)}>−</button>
          <input aria-label="时间轴缩放" type="range" min={0} max={100} step={1}
            value={Math.round(Math.log(zoom) / Math.log(maxZoom || 1.0001) * 100) || 0}
            onChange={event => changeZoom(Math.pow(maxZoom, Number(event.target.value) / 100))}/>
          <button type="button" aria-label="放大时间轴" disabled={zoom >= maxZoom} onClick={() => changeZoom(zoom * 1.6)}>+</button>
          <button type="button" disabled={zoom === 1} onClick={() => setZoom(1)} title="显示整段媒体">适应宽度</button>
        </span>
      </div>
      <div className="timeline-scroll" ref={scrollRef}
        onWheel={event => {
          if (!(event.ctrlKey || event.metaKey)) return;
          event.preventDefault();
          changeZoom(zoom * (event.deltaY < 0 ? 1.25 : 0.8), event.clientX);
        }}>
        <div className="timeline-track" style={{ width }} onClick={event => {
          const rect = event.currentTarget.getBoundingClientRect();
          onSeek(Math.max(0, Math.min(total, (event.clientX - rect.left) / rect.width * total)));
        }}>
          <div className="timeline-ruler">
            {Array.from({ length: Math.floor(total / tick) + 1 }, (_, i) => {
              const value = i * tick;
              return <span key={value} style={{ left: `${value / total * 100}%` }}>{formatTime(value)}</span>;
            })}
          </div>
          <WaveformCanvas peaks={waveform.peaks} offset={waveform.offset} duration={waveform.duration} total={total}/>
          <TimelineSegments segments={segments} total={total} currentTime={currentTime} activeId={active?.id} onSeek={onSeek} onUpdateTime={onUpdateTime}/>
          <div className="timeline-playhead" style={{ left: `${Math.min(100, currentTime / total * 100)}%` }} />
        </div>
        {!segments.length && <p className="timeline-empty">生成或导入字幕后，每条字幕会以色块显示在波形上：点击跳转，拖动边缘微调时间。按住 ⌘ 滚动可缩放。</p>}
      </div>
    </div>
  );
}

function WaveformCanvas({ peaks, offset, duration, total }: { peaks: number[]; offset: number; duration: number; total: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !peaks.length) return;
    const render = () => {
      const rect = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(rect.width * ratio));
      canvas.height = Math.max(1, Math.round(rect.height * ratio));
      const context = canvas.getContext('2d');
      if (!context) return;
      context.scale(ratio, ratio);
      context.clearRect(0, 0, rect.width, rect.height);
      context.fillStyle = 'rgba(91, 140, 255, .42)';
      const middle = rect.height / 2;
      for (let x = 0; x < rect.width; x += 1) {
        const peak = peaks[Math.min(peaks.length - 1, Math.floor(x / rect.width * peaks.length))] || 0;
        const height = Math.max(1, peak * middle * .9);
        context.fillRect(x, middle - height, 1, height * 2);
      }
    };
    render();
    const observer = new ResizeObserver(render);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [peaks]);
  return <canvas ref={canvasRef} className="timeline-waveform" aria-hidden="true" style={{ left: `${offset / total * 100}%`, right: 'auto', width: `${Math.min(100 - offset / total * 100, duration / total * 100)}%` }}/>
}

const TimelineSegments = memo(function TimelineSegments({ segments, total, currentTime, activeId, onSeek, onUpdateTime }: {
  segments: SubtitleSegment[]; total: number; currentTime: number; activeId?: string; onSeek: (time: number) => void;
  onUpdateTime?: (index: number, update: {start?: number; end?: number}) => void;
}) {
  return <div className="timeline-segments">
    {segments.map((segment, index) => <div key={segment.id} className={`timeline-segment ${segment.id === activeId ? 'active' : ''}`} role="button" tabIndex={0}
      style={{ left: `${segment.start / total * 100}%`, width: `${Math.max(.18, (segment.end - segment.start) / total * 100)}%` }}
      title={`${formatTime(segment.start)} ${segment.clean_text || segment.raw_text}`}
      onClick={event => { event.stopPropagation(); onSeek(segment.start); }} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSeek(segment.start); } }}>{segment.index}
      {onUpdateTime && <><BoundaryHandle side="start" segment={segment} previous={segments[index - 1]} next={segments[index + 1]} total={total} currentTime={currentTime} onSeek={onSeek} onChange={onUpdateTime}/><BoundaryHandle side="end" segment={segment} previous={segments[index - 1]} next={segments[index + 1]} total={total} currentTime={currentTime} onSeek={onSeek} onChange={onUpdateTime}/></>}
    </div>)}
  </div>;
});

function BoundaryHandle({ side, segment, previous, next, total, currentTime, onSeek, onChange }: {
  side: 'start' | 'end'; segment: SubtitleSegment; previous?: SubtitleSegment; next?: SubtitleSegment;
  total: number; currentTime: number; onSeek: (time: number) => void; onChange: (index: number, update: {start?: number; end?: number}) => void;
}) {
  const commit = (raw: number) => {
    const candidates = [currentTime, previous?.end, next?.start].filter((value): value is number => value !== undefined);
    const snapped = candidates.reduce((best, value) => Math.abs(value - raw) < Math.abs(best - raw) && Math.abs(value - raw) <= .12 ? value : best, raw);
    const value = side === 'start' ? Math.max(previous?.end || 0, Math.min(segment.end - .05, snapped)) : Math.min(next?.start ?? total, Math.max(segment.start + .05, snapped));
    onChange(segment.index, { [side]: Math.round(value * 1000) / 1000 });
  };
  return <span role="slider" tabIndex={0} aria-label={`第 ${segment.index} 条${side === 'start' ? '开始' : '结束'}边界`} aria-valuemin={0} aria-valuemax={total} aria-valuenow={side === 'start' ? segment.start : segment.end} className={`timeline-boundary ${side}`}
    onPointerDown={event => {
      event.stopPropagation(); const track = event.currentTarget.closest('.timeline-track') as HTMLElement | null; if (!track) return;
      const move = (pointer: PointerEvent) => { const rect = track.getBoundingClientRect(); onSeek(Math.max(0, Math.min(total, (pointer.clientX - rect.left) / rect.width * total))); };
      const up = (pointer: PointerEvent) => { const rect = track.getBoundingClientRect(); commit(Math.max(0, Math.min(total, (pointer.clientX - rect.left) / rect.width * total))); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', up, { once: true });
    }}
    onClick={event => event.stopPropagation()}
    onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); commit((side === 'start' ? segment.start : segment.end) + (event.key === 'ArrowLeft' ? -.04 : .04)); } }}/>;
}
