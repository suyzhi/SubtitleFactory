import { memo } from 'react';
import type { SubtitleSegment } from '../types';

interface Props {
  segments: SubtitleSegment[];
  currentTime: number;
  duration: number;
  onSeek: (time: number) => void;
}

function formatTime(value: number) {
  const minutes = Math.floor(value / 60);
  const seconds = Math.floor(value % 60);
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

export default function SubtitleTimeline({ segments, currentTime, duration, onSeek }: Props) {
  const total = Math.max(duration, segments.at(-1)?.end ?? 1, 1);
  const width = Math.max(900, Math.min(6000, total * 8));
  return (
    <div className="subtitle-timeline" aria-label="字幕时间轴">
      <div className="timeline-toolbar">
        <strong>字幕时间轴</strong>
        <span>{formatTime(currentTime)} / {formatTime(total)}</span>
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
          <TimelineSegments segments={segments} total={total} onSeek={onSeek}/>
          <div className="timeline-playhead" style={{ left: `${Math.min(100, currentTime / total * 100)}%` }} />
        </div>
      </div>
    </div>
  );
}

const TimelineSegments = memo(function TimelineSegments({ segments, total, onSeek }: {
  segments: SubtitleSegment[]; total: number; onSeek: (time: number) => void;
}) {
  return <div className="timeline-segments">
    {segments.map(segment => <button key={segment.id} className="timeline-segment"
      style={{ left: `${segment.start / total * 100}%`, width: `${Math.max(.18, (segment.end - segment.start) / total * 100)}%` }}
      title={`${formatTime(segment.start)} ${segment.clean_text || segment.raw_text}`}
      onClick={event => { event.stopPropagation(); onSeek(segment.start); }}>{segment.index}</button>)}
  </div>;
});
