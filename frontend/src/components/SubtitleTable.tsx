// 字幕表格编辑器：虚拟化渲染、行内编辑、批量替换与自动跟随播放。

import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
} from 'react';
import type { SubtitleSegment, SegmentUpdate } from '../types';
import {
  buildSubtitleRowOffsets,
  getSubtitleWindowRange,
  SUBTITLE_VIRTUALIZE_THRESHOLD,
} from '../subtitleTableVirtualization';
import { fmtTime } from '../utils/library';
import './SubtitleTable.css';

function SubtitleTable({
  segments, currentTime, activeIdx, onSeek, onUpdate, onReplaceAll, onSplit, onMerge,
  onUndo, onRedo, saveState, draftCount, draftIsStale, onPreviewDraft,
  onCommitDraft, onDiscardDraft,
  onAutoScrollChange, autoScroll, entryFocusIdx, entryFocusRequest, disabled
}: {
  segments: SubtitleSegment[];
  currentTime: number;
  activeIdx: number;
  entryFocusIdx?: number;
  entryFocusRequest?: number;
  onSeek: (time: number) => void;
  onUpdate: (idx: number, data: SegmentUpdate) => void;
  onReplaceAll: (search: string, replacement: string, fields: Array<'clean_text' | 'translated_text'>, options: {matchCase: boolean; includeLocked: boolean}) => Promise<void>;
  onSplit: (index: number, splitAt: number) => Promise<void>;
  onMerge: (indices: number[]) => Promise<void>;
  onUndo: () => void | Promise<void>;
  onRedo: () => void | Promise<void>;
  saveState: 'idle' | 'saving' | 'saved' | 'error';
  draftCount: number;
  draftIsStale?: boolean;
  onPreviewDraft?: () => void;
  onCommitDraft: () => void | Promise<void>;
  onDiscardDraft: () => void | Promise<void>;
  onAutoScrollChange?: (v: boolean) => void;
  autoScroll?: boolean;
  disabled?: boolean;
}) {
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editField, setEditField] = useState<'start' | 'end' | 'clean_text' | 'translated_text' | null>(null);
  const [editValue, setEditValue] = useState('');
  const [searchText, setSearchText] = useState('');
  const [replaceText, setReplaceText] = useState('');
  const [replacePreview, setReplacePreview] = useState(false);
  const [replaceOriginal, setReplaceOriginal] = useState(true);
  const [replaceTranslation, setReplaceTranslation] = useState(true);
  const [replaceMatchCase, setReplaceMatchCase] = useState(false);
  const [replaceIncludeLocked, setReplaceIncludeLocked] = useState(false);
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(() => new Set());
  const tableRef = useRef<HTMLDivElement>(null);
  const userScrolling = useRef(false);
  const programmaticScroll = useRef(false);
  const userScrollTimer = useRef<number | undefined>(undefined);
  const programmaticTimer = useRef<number | undefined>(undefined);
  const measuredRowHeights = useRef<Map<string, number>>(new Map());
  const measuredTableWidth = useRef(0);
  const [rowMeasurementRevision, setRowMeasurementRevision] = useState(0);
  const [estimatedRowHeight, setEstimatedRowHeight] = useState(44);
  const [windowRange, setWindowRange] = useState({ start: 0, end: 90 });
  const handledEntryRequest = useRef<number | undefined>(undefined);
  const lastAutoScrolledIdx = useRef<number | null>(null);
  const pendingCenterIdx = useRef<number | null>(null);
  const pendingCenterPasses = useRef(0);
  const visibleSegments = useMemo(() => {
    const needle = searchText.trim().toLocaleLowerCase();
    if (!needle) return segments;
    return segments.filter(segment =>
      `${segment.clean_text || segment.raw_text}\n${segment.translated_text}`.toLocaleLowerCase().includes(needle)
    );
  }, [searchText, segments]);
  const virtualized = visibleSegments.length > SUBTITLE_VIRTUALIZE_THRESHOLD;
  const rowOffsets = useMemo(() => {
    void rowMeasurementRevision;
    return buildSubtitleRowOffsets(visibleSegments, measuredRowHeights.current, estimatedRowHeight);
  }, [estimatedRowHeight, rowMeasurementRevision, visibleSegments]);
  const renderedSegments = useMemo(
    () => virtualized ? visibleSegments.slice(windowRange.start, windowRange.end) : visibleSegments,
    [virtualized, visibleSegments, windowRange.end, windowRange.start],
  );
  const topSpacer = virtualized ? rowOffsets[windowRange.start] ?? 0 : 0;
  const bottomSpacer = virtualized
    ? Math.max(0, (rowOffsets.at(-1) ?? 0) - (rowOffsets[windowRange.end] ?? 0))
    : 0;

  const updateWindow = useCallback((scrollTopOverride?: number) => {
    const element = tableRef.current;
    if (!element || !virtualized) return;
    const { start, end } = getSubtitleWindowRange(
      rowOffsets,
      scrollTopOverride ?? element.scrollTop,
      element.clientHeight,
    );
    setWindowRange(current => current.start === start && current.end === end ? current : { start, end });
  }, [rowOffsets, virtualized]);

  useLayoutEffect(() => {
    const element = tableRef.current;
    if (!element) return;
    const readEstimatedRowHeight = () => {
      const value = Number.parseFloat(getComputedStyle(element).getPropertyValue('--subtitle-row-height'));
      if (Number.isFinite(value) && value > 0) setEstimatedRowHeight(value);
      const width = element.clientWidth;
      if (width > 0 && measuredTableWidth.current > 0 && Math.abs(measuredTableWidth.current - width) > 1) {
        measuredRowHeights.current.clear();
        setRowMeasurementRevision(revision => revision + 1);
      }
      measuredTableWidth.current = width;
    };
    readEstimatedRowHeight();
    const observer = new ResizeObserver(() => { readEstimatedRowHeight(); updateWindow(); });
    observer.observe(element);
    return () => observer.disconnect();
  }, [updateWindow]);

  useLayoutEffect(() => {
    const element = tableRef.current;
    if (!element) return;
    const rows = Array.from(element.querySelectorAll<HTMLTableRowElement>('tbody tr[data-idx]'));
    if (!rows.length) return;
    const recordMeasurements = (targets: HTMLTableRowElement[]) => {
      let changed = false;
      for (const row of targets) {
        const segmentId = row.dataset.segmentId;
        const height = row.getBoundingClientRect().height;
        if (!segmentId || !Number.isFinite(height) || height <= 0) continue;
        if (Math.abs((measuredRowHeights.current.get(segmentId) ?? 0) - height) < .5) continue;
        measuredRowHeights.current.set(segmentId, height);
        changed = true;
      }
      if (changed) setRowMeasurementRevision(revision => revision + 1);
    };
    recordMeasurements(rows);
    const observer = new ResizeObserver(entries => {
      recordMeasurements(entries.map(entry => entry.target as HTMLTableRowElement));
    });
    rows.forEach(row => observer.observe(row));
    return () => observer.disconnect();
  }, [renderedSegments]);

  useLayoutEffect(() => {
    updateWindow();
  }, [rowMeasurementRevision, updateWindow]);

  useEffect(() => {
    setWindowRange({ start: 0, end: 90 });
    if (tableRef.current) tableRef.current.scrollTop = 0;
  }, [searchText]);

  const replaceMatchCount = useMemo(() => {
    if (!searchText || (!replaceOriginal && !replaceTranslation)) return 0;
    const normalize = (value: string) => replaceMatchCase ? value : value.toLocaleLowerCase();
    const needle = normalize(searchText);
    return segments.filter(segment => (replaceIncludeLocked || !segment.locked) && (
      (replaceOriginal && normalize(segment.clean_text || segment.raw_text).includes(needle)) ||
      (replaceTranslation && normalize(segment.translated_text).includes(needle))
    )).length;
  }, [replaceIncludeLocked, replaceMatchCase, replaceOriginal, replaceTranslation, searchText, segments]);

  const centerSegment = useCallback((segmentIndex: number, settleAfterMeasurement = false) => {
    const element = tableRef.current;
    const position = visibleSegments.findIndex(segment => segment.index === segmentIndex);
    if (!element || position < 0) return;
    if (settleAfterMeasurement) {
      pendingCenterIdx.current = segmentIndex;
      pendingCenterPasses.current = 2;
    }
    const rowTop = rowOffsets[position] ?? 0;
    const rowBottom = rowOffsets[position + 1] ?? rowTop + estimatedRowHeight;
    const top = Math.max(0, rowTop - element.clientHeight / 2 + (rowBottom - rowTop) / 2);
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      || !!document.querySelector('.motion-off');
    programmaticScroll.current = true;
    window.clearTimeout(programmaticTimer.current);
    if (virtualized) updateWindow(top);
    element.scrollTo({ top, behavior: reduced ? 'auto' : 'smooth' });
    // `scrollend` clears this as soon as the browser settles. The long fallback
    // is deliberately not an animation-duration guess: explicit wheel,
    // pointer, or touch intent cancels it immediately, while a slow smooth
    // scroll must not turn off the user's auto-scroll preference by mistake.
    programmaticTimer.current = window.setTimeout(() => { programmaticScroll.current = false; }, 5000);
  }, [estimatedRowHeight, rowOffsets, updateWindow, virtualized, visibleSegments]);

  useLayoutEffect(() => {
    if (pendingCenterIdx.current === null || pendingCenterPasses.current <= 0) return;
    const frame = window.requestAnimationFrame(() => {
      if (pendingCenterIdx.current === null) return;
      centerSegment(pendingCenterIdx.current);
      pendingCenterPasses.current -= 1;
      if (pendingCenterPasses.current <= 0) pendingCenterIdx.current = null;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [centerSegment, rowMeasurementRevision]);

  // Entering the subtitle workspace always performs one positioning pass. This is
  // deliberately independent from the user's ongoing auto-scroll preference.
  useEffect(() => {
    if (entryFocusIdx === undefined || entryFocusIdx < 0) return;
    if (handledEntryRequest.current === entryFocusRequest) return;
    handledEntryRequest.current = entryFocusRequest;
    centerSegment(entryFocusIdx, true);
    if (entryFocusIdx === activeIdx) lastAutoScrolledIdx.current = activeIdx;
  }, [activeIdx, centerSegment, entryFocusIdx, entryFocusRequest]);

  // Continue following playback only while the user-facing auto-scroll toggle is on.
  useEffect(() => {
    if (!autoScroll) {
      lastAutoScrolledIdx.current = null;
      return;
    }
    if (activeIdx < 0 || userScrolling.current || lastAutoScrolledIdx.current === activeIdx) return;
    lastAutoScrolledIdx.current = activeIdx;
    centerSegment(activeIdx, true);
  }, [activeIdx, autoScroll, centerSegment]);

  useEffect(() => () => {
    window.clearTimeout(userScrollTimer.current);
    window.clearTimeout(programmaticTimer.current);
  }, []);

  useEffect(() => {
    const element = tableRef.current;
    if (!element) return;
    const finishProgrammaticScroll = () => {
      programmaticScroll.current = false;
      window.clearTimeout(programmaticTimer.current);
    };
    element.addEventListener('scrollend', finishProgrammaticScroll);
    return () => element.removeEventListener('scrollend', finishProgrammaticScroll);
  }, []);

  const markUserScrollIntent = useCallback(() => {
    programmaticScroll.current = false;
    window.clearTimeout(programmaticTimer.current);
  }, []);

  // Detect user scrolling
  const handleScroll = useCallback(() => {
    updateWindow();
    if (programmaticScroll.current) return;
    userScrolling.current = true;
    window.clearTimeout(userScrollTimer.current);
    userScrollTimer.current = window.setTimeout(() => { userScrolling.current = false; }, 2000);
    if (onAutoScrollChange) onAutoScrollChange(false);
  }, [onAutoScrollChange, updateWindow]);

  const startEdit = (seg: SubtitleSegment, field: 'start' | 'end' | 'clean_text' | 'translated_text') => {
    if (disabled) return;
    setEditingIdx(seg.index);
    setEditField(field);
    setEditValue(field === 'start' || field === 'end'
      ? String(seg[field].toFixed(3))
      : (seg[field] || (field === 'clean_text' ? seg.raw_text : '')));
  };

  const saveEdit = () => {
    if (editingIdx === null || !editField) return;
    if (editField === 'start' || editField === 'end') {
      const value = Number(editValue);
      if (!Number.isFinite(value) || value < 0) return;
      onUpdate(editingIdx, { [editField]: value });
    } else {
      onUpdate(editingIdx, { [editField]: editValue });
    }
    setEditingIdx(null);
    setEditField(null);
  };

  const cancelEdit = () => {
    setEditingIdx(null);
    setEditField(null);
  };

  const selected = [...selectedIndices].sort((a, b) => a - b);
  const selectedSegment = selected.length === 1 ? segments.find(segment => segment.index === selected[0]) : undefined;
  const canSplit = !!selectedSegment && currentTime > selectedSegment.start && currentTime < selectedSegment.end;
  const canMerge = selected.length >= 2 && selected.every((value, index) => index === 0 || value === selected[index - 1] + 1);

  const toggleSelected = (index: number) => setSelectedIndices(current => {
    const next = new Set(current);
    if (next.has(index)) next.delete(index); else next.add(index);
    return next;
  });

  return (
    <div className={`subtitle-table-container ${disabled ? 'editing-disabled' : ''}`}>
      <div className="subtitle-table-header">
        <h3>字幕时间轴与编辑 ({segments.length} 条)</h3>
        <div className="table-header-right">
          <button className="btn btn-ghost btn-xs" disabled={disabled} onClick={() => void onUndo()} title="撤销上次编辑">↶ 撤销</button>
          <button className="btn btn-ghost btn-xs" disabled={disabled} onClick={() => void onRedo()} title="重做上次编辑">↷ 重做</button>
          <button className="btn btn-ghost btn-xs" disabled={disabled || !canSplit} onClick={() => {
            if (!selectedSegment) return;
            void onSplit(selectedSegment.index, currentTime)
              .then(() => setSelectedIndices(new Set()))
              .catch(() => undefined);
          }}>拆分</button>
          <button className="btn btn-ghost btn-xs" disabled={disabled || !canMerge} onClick={() => void onMerge(selected).then(() => setSelectedIndices(new Set())).catch(() => undefined)}>合并</button>
          <button className={`btn btn-ghost btn-xs ${autoScroll ? '' : 'inactive'}`}
            onClick={() => onAutoScrollChange?.(!autoScroll)}
            title={autoScroll ? '自动滚动已开启' : '自动滚动已关闭'}>
            {autoScroll ? '🔁 自动' : '⏸ 锁定'}
          </button>
          <span className="current-time">⏱ {fmtTime(currentTime)}</span>
          <span className={`editor-save-state ${saveState}`}>{saveState === 'saving' ? '保存中…' : saveState === 'error' ? '保存失败' : saveState === 'saved' ? '已保存' : ''}</span>
        </div>
      </div>
      {draftCount > 0 && <div className="subtitle-draft-bar" role="status"><span>{draftIsStale ? `${draftCount} 条旧草稿已保留；正式字幕已变化` : `${draftCount} 条未提交草稿已安全保存在本机`}</span>{onPreviewDraft && <button onClick={onPreviewDraft}>预览</button>}<button onClick={() => void onDiscardDraft()}>放弃</button><button className="primary" onClick={() => void onCommitDraft()}>{draftIsStale ? '确认恢复' : '保存全部'}</button></div>}
      <div className="subtitle-findbar">
        <input value={searchText} onChange={event => setSearchText(event.target.value)} placeholder="搜索字幕" />
        <input value={replaceText} onChange={event => setReplaceText(event.target.value)} placeholder="替换为" />
        <button disabled={disabled || !searchText || !replaceMatchCount} onClick={() => setReplacePreview(true)}>全部替换</button>
        {searchText && <span>{visibleSegments.length} 条匹配</span>}
        <label><input type="checkbox" checked={replaceOriginal} onChange={event => setReplaceOriginal(event.target.checked)}/> 原文/整理</label><label><input type="checkbox" checked={replaceTranslation} onChange={event => setReplaceTranslation(event.target.checked)}/> 译文</label><label><input type="checkbox" checked={replaceMatchCase} onChange={event => setReplaceMatchCase(event.target.checked)}/> 区分大小写</label><label><input type="checkbox" checked={replaceIncludeLocked} onChange={event => setReplaceIncludeLocked(event.target.checked)}/> 覆盖锁定项</label>
      </div>
      {replacePreview && <div className="replace-preview" role="dialog" aria-label="确认全部替换"><div><strong>预览全部替换</strong><span>将在{replaceOriginal ? '原文/整理' : ''}{replaceOriginal && replaceTranslation ? '和' : ''}{replaceTranslation ? '译文' : ''}中修改 {replaceMatchCount} 条字幕，{replaceIncludeLocked ? '包括锁定字幕' : '锁定字幕不会改变'}。此操作可撤销。</span></div><button onClick={() => setReplacePreview(false)}>取消</button><button className="primary" onClick={() => void onReplaceAll(searchText, replaceText, [...(replaceOriginal ? ['clean_text' as const] : []), ...(replaceTranslation ? ['translated_text' as const] : [])], { matchCase: replaceMatchCase, includeLocked: replaceIncludeLocked }).then(() => setReplacePreview(false)).catch(() => undefined)}>确认替换</button></div>}
      <div className="subtitle-table-scroll" ref={tableRef} onScroll={handleScroll}
        onWheelCapture={markUserScrollIntent} onPointerDownCapture={markUserScrollIntent}
        onTouchStart={markUserScrollIntent}>
        <table className="subtitle-table">
          <thead>
            <tr>
              <th className="col-idx">#</th>
              <th className="col-time">开始</th>
              <th className="col-time">结束</th>
              <th className="col-text">原文/整理</th>
              <th className="col-text">译文</th>
              <th className="col-lock">🔒</th>
            </tr>
          </thead>
          <tbody>
            {topSpacer > 0 && <tr className="virtual-spacer" aria-hidden="true"><td colSpan={6} style={{ height: topSpacer }}/></tr>}
            {renderedSegments.map(seg => {
              const isActive = seg.index === activeIdx;
              const isEditing = seg.index === editingIdx;
              const displayText = seg.clean_text || seg.raw_text;
              const duration = seg.end - seg.start;
              const charsPerSecond = displayText.length / Math.max(duration, .1);
              const qualityIssue = duration < .35 ? '时长过短' : duration > 8 ? '时长过长' : charsPerSecond > 18 ? '语速过快' : '';
              return (
                <tr key={seg.id}
                  data-idx={seg.index}
                  data-segment-id={seg.id}
                  title={qualityIssue}
                  className={`${isActive ? 'active-row' : ''} ${seg.locked ? 'locked-row' : ''} ${qualityIssue ? 'quality-warning' : ''}`}
                >
                  <td className="col-idx"><span className="subtitle-row-identity"><input type="checkbox" aria-label={`选择第 ${seg.index} 条字幕`} checked={selectedIndices.has(seg.index)} onChange={() => toggleSelected(seg.index)}/><span>{seg.index}</span></span></td>
                  <td className="col-time">
                    {isEditing && editField === 'start' ? <input className="time-edit-input" type="number" min="0" step="0.001" autoFocus value={editValue} onChange={event => setEditValue(event.target.value)} onBlur={saveEdit} onKeyDown={event => event.key === 'Enter' ? saveEdit() : event.key === 'Escape' ? cancelEdit() : undefined}/>
                      : <><button className="time-seek" onClick={() => onSeek(seg.start)}>{fmtTime(seg.start)}</button><button className="time-edit" aria-label={`编辑第 ${seg.index} 条开始时间`} onClick={() => startEdit(seg, 'start')}>✎</button></>}
                  </td>
                  <td className="col-time">
                    {isEditing && editField === 'end' ? <input className="time-edit-input" type="number" min="0" step="0.001" autoFocus value={editValue} onChange={event => setEditValue(event.target.value)} onBlur={saveEdit} onKeyDown={event => event.key === 'Enter' ? saveEdit() : event.key === 'Escape' ? cancelEdit() : undefined}/>
                      : <><button className="time-seek" onClick={() => onSeek(seg.end)}>{fmtTime(seg.end)}</button><button className="time-edit" aria-label={`编辑第 ${seg.index} 条结束时间`} onClick={() => startEdit(seg, 'end')}>✎</button></>}
                  </td>
                  <td className="col-text editable"
                    onClick={() => !isEditing && startEdit(seg, 'clean_text')}>
                    {isEditing && editField === 'clean_text' ? (
                      <input className="edit-input" autoFocus
                        value={editValue}
                        onChange={e => setEditValue(e.target.value)}
                        onBlur={saveEdit}
                        onKeyDown={e => e.key === 'Enter' ? saveEdit() : e.key === 'Escape' ? cancelEdit() : undefined}
                      />
                    ) : (
                      <span className="text-preview">{displayText || '...'}</span>
                    )}
                  </td>
                  <td className="col-text editable"
                    onClick={() => !isEditing && startEdit(seg, 'translated_text')}>
                    {isEditing && editField === 'translated_text' ? (
                      <input className="edit-input" autoFocus
                        value={editValue}
                        onChange={e => setEditValue(e.target.value)}
                        onBlur={saveEdit}
                        onKeyDown={e => e.key === 'Enter' ? saveEdit() : e.key === 'Escape' ? cancelEdit() : undefined}
                      />
                    ) : (
                      <span className="text-preview">{seg.translated_text || '...'}</span>
                    )}
                  </td>
                  <td className="col-lock">
                    <input type="checkbox" checked={seg.locked} disabled={disabled}
                      onChange={e => onUpdate(seg.index, { locked: e.target.checked })} />
                  </td>
                </tr>
              );
            })}
            {bottomSpacer > 0 && <tr className="virtual-spacer" aria-hidden="true"><td colSpan={6} style={{ height: bottomSpacer }}/></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default SubtitleTable;
