import { useState } from 'react';
import type { ReactNode } from 'react';
import type { ExportFormat } from '../types';

const formats = ['srt','vtt','ass','srt-bilingual','mp4','mkv'] as const;
export default function ExportWorkspace({bilingual,onBilingual,hasSegments,hasVideo,busy,onExport,onPackage,task}: {
  bilingual:boolean; onBilingual:(value:boolean)=>void; hasSegments:boolean; hasVideo:boolean; busy:boolean;
  onExport:(format:ExportFormat)=>void; onPackage:(media:boolean)=>void; task:ReactNode;
}) {
  const [format,setFormat] = useState<ExportFormat>(() => { const saved=localStorage.getItem('subtitle_factory_export_format'); return formats.includes(saved as typeof formats[number]) ? saved as ExportFormat : 'srt'; });
  const [category,setCategory] = useState<'subtitle'|'video'|'package'>(() => format === 'mp4' || format === 'mkv' ? 'video' : 'subtitle');
  const choose=(value:ExportFormat) => {setFormat(value);localStorage.setItem('subtitle_factory_export_format',value);};
  return <section className="task-page export-task-page"><header className="export-page-hero"><div><small>最后一步</small><h2>导出作品</h2><p>先选择交付类型，再设置格式。带字幕视频在本机后台生成。</p></div></header>
    <nav className="tool-panel-tabs" aria-label="导出类型">{([['subtitle','字幕文件'],['video','带字幕视频'],['package','项目包']] as const).map(([id,label]) => <button key={id} aria-pressed={category===id} onClick={() => {setCategory(id);if(id==='subtitle' && (format==='mp4'||format==='mkv'))choose('srt');if(id==='video' && format!=='mp4' && format!=='mkv')choose('mp4');}}>{label}</button>)}</nav>
    {category === 'package' ? <section><p>项目包包含字幕、编辑历史与项目设置，可在另一台电脑继续编辑。</p><button className="button secondary" onClick={() => onPackage(false)}>导出精简项目包</button><button className="button primary" onClick={() => onPackage(true)}>包含媒体的完整项目包</button></section> : <section className="export-options"><label>文件格式<select aria-label="导出格式" value={format} onChange={event => choose(event.target.value as ExportFormat)}>{(category === 'subtitle' ? ['srt','vtt','ass','srt-bilingual'] : ['mp4','mkv']).map(value => <option key={value} value={value}>{value==='srt-bilingual' ? '双语 SRT' : value.toUpperCase()}</option>)}</select></label><p>{category === 'video' ? '使用“样式”页设置的字体、位置和外观压制字幕。' : format==='ass' ? 'ASS 保留字幕外观与位置。' : 'SRT 和 VTT 保留文字与时间，播放器决定显示外观。'}</p><label className="check-row"><input type="checkbox" checked={bilingual || format==='srt-bilingual'} disabled={format==='srt-bilingual'} onChange={event => onBilingual(event.target.checked)}/>包含原文和译文</label>{!hasSegments && <p>请先生成或导入字幕。</p>}{category==='video' && !hasVideo && <p>当前缺少视频素材，请先在任务面板准备素材。</p>}<button className="button primary" disabled={busy || !hasSegments || (category==='video' && !hasVideo)} onClick={() => onExport(format)}>{category==='video' ? '开始生成带字幕视频' : '导出字幕文件'}</button></section>}
    {task}
  </section>;
}
