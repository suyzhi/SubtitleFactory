import { memo, useEffect, useRef, useState } from 'react';
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
  const minutes = Math.floor(value / 60);
  const seconds = Math.floor(value % 60);
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

export default function SubtitleTimeline({ projectId, segments, currentTime, duration, onSeek, onUpdateTime }: Props) {
  const [zoom, setZoom] = useState(1);
  const [waveform, setWaveform] = useState<{peaks: number[]; offset: number; duration: number}>({ peaks: [], offset: 0, duration: 0 });
  useEffect(() => {
    let active = true;
    setWaveform({ peaks: [], offset: 0, duration: 0 });
    getProjectWaveform(projectId, 4000)
      .then(result => { if (active) setWaveform({ peaks: result.peaks, offset: result.offset || 0, duration: result.duration }); })
      .catch(() => { /* Audio may not be extracted yet. */ });
    return () => { active = false; };
  }, [projectId]);
  const total = Math.max(duration, segments.at(-1)?.end ?? 1, 1);
  const width = Math.max(900, Math.min(18000, total * 8 * zoom));
  return (
    <div className="subtitle-timeline" aria-label="字幕时间轴">
      <div className="timeline-toolbar">
        <strong>字幕时间轴</strong>
        <span>{formatTime(currentTime)} / {formatTime(total)}</span>
        <label className="timeline-zoom">缩放
          <input aria-label="时间轴缩放" type="range" min="1" max="8" step="0.5" value={zoom}
            onChange={event => setZoom(Number(event.target.value))}/>
        </label>
      </div>
      <div className="timeline-scroll">
        <div className="timeline-track" style={{ width }} onClick={event => {
          const rect = event.currentTarget.getBoundingClientRect();
          onSeek(Math.max(0, Math.min(total, (event.clientX - rect.left) / rect.width * total)));
        }}>
          <div className="timeline-ruler">
            {Array.from({ length: Math.max(2, Math.ceil(total / 30) + 1) }, (_, i) => {
              const value = Math.min(total, i * 30);
              return <span key={value} style={{ left: `${value / total * 100}%` }}>{formatTime(value)}</span>;
            })}
          </div>
          <WaveformCanvas peaks={waveform.peaks} offset={waveform.offset} duration={waveform.duration} total={total}/>
          <TimelineSegments segments={segments} total={total} currentTime={currentTime} onSeek={onSeek} onUpdateTime={onUpdateTime}/>
          <div className="timeline-playhead" style={{ left: `${Math.min(100, currentTime / total * 100)}%` }} />
        </div>
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

const TimelineSegments = memo(function TimelineSegments({ segments, total, currentTime, onSeek, onUpdateTime }: {
  segments: SubtitleSegment[]; total: number; currentTime: number; onSeek: (time: number) => void;
  onUpdateTime?: (index: number, update: {start?: number; end?: number}) => void;
}) {
  return <div className="timeline-segments">
    {segments.map((segment, index) => <div key={segment.id} className="timeline-segment" role="button" tabIndex={0}
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
