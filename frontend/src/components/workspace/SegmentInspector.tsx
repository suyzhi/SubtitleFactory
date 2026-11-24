import type { SegmentUpdate, SubtitleSegment } from '../../types';

interface Props {
  segment: SubtitleSegment;
  editorBusy: boolean;
  onClose: () => void;
  onUpdateSegment: (index: number, data: SegmentUpdate) => Promise<void>;
  onManageSpeakers: () => void;
  onOpenStyle: () => void;
}

export default function SegmentInspector({
  segment,
  editorBusy,
  onClose,
  onUpdateSegment,
  onManageSpeakers,
  onOpenStyle,
}: Props) {
  return (
    <>
      <button className="button secondary" onClick={onClose}>
        关闭属性
      </button>
      <h3>字幕 {segment.index}</h3>
      <label>
        开始时间（秒）
        <input
          key={`start-${segment.id}-${segment.start}`}
          type="number"
          min={0}
          step={0.01}
          defaultValue={segment.start}
          disabled={editorBusy}
          onBlur={event => {
            const start = Number(event.target.value);
            if (
              Number.isFinite(start) &&
              start >= 0 &&
              start < segment.end &&
              start !== segment.start
            ) {
              void onUpdateSegment(segment.index, { start });
            }
          }}
        />
      </label>
      <label>
        结束时间（秒）
        <input
          key={`end-${segment.id}-${segment.end}`}
          type="number"
          min={segment.start}
          step={0.01}
          defaultValue={segment.end}
          disabled={editorBusy}
          onBlur={event => {
            const end = Number(event.target.value);
            if (Number.isFinite(end) && end > segment.start && end !== segment.end) {
              void onUpdateSegment(segment.index, { end });
            }
          }}
        />
      </label>
      <p>说话人：{segment.speaker || '未分配'}</p>
      <button className="button secondary" onClick={onManageSpeakers}>
        管理说话人
      </button>
      <button className="button secondary" onClick={onOpenStyle}>
        调整字幕样式
      </button>
    </>
  );
}
