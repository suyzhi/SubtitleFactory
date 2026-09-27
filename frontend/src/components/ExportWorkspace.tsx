import { useState } from 'react';
import type { ReactNode } from 'react';
import type { ExportFormat } from '../types';
import { useSlidingIndicator } from '../utils/motion';

const formats = ['srt','vtt','ass','srt-bilingual','mp4','mkv'] as const;
const FORMAT_INFO: Record<string, [string, string]> = {
  srt: ['SRT', '最通用，几乎所有播放器和平台都支持'],
  vtt: ['VTT', '网页播放器（HTML5 视频）常用'],
  ass: ['ASS', '保留字体、颜色与位置等样式'],
  'srt-bilingual': ['双语 SRT', '原文与译文上下两行'],
  mp4: ['MP4', '兼容性最好，适合上传与分享'],
  mkv: ['MKV', '保留更多轨道信息，适合存档'],
};
export default function ExportWorkspace({bilingual,onBilingual,hasSegments,hasVideo,busy,onExport,onPackage,task}: {
  bilingual:boolean; onBilingual:(value:boolean)=>void; hasSegments:boolean; hasVideo:boolean; busy:boolean;
  onExport:(format:ExportFormat)=>void; onPackage:(media:boolean)=>void; task:ReactNode;
}) {
  const [format,setFormat] = useState<ExportFormat>(() => { const saved=localStorage.getItem('subtitle_factory_export_format'); return formats.includes(saved as typeof formats[number]) ? saved as ExportFormat : 'srt'; });
  const [category,setCategory] = useState<'subtitle'|'video'|'package'>(() => format === 'mp4' || format === 'mkv' ? 'video' : 'subtitle');
  const tabsRef = useSlidingIndicator<HTMLElement>(category);
  const choose=(value:ExportFormat) => {setFormat(value);localStorage.setItem('subtitle_factory_export_format',value);};
  return <section className="task-page export-task-page"><header className="export-page-hero"><div><small>最后一步</small><h2>导出作品</h2><p>先选择交付类型，再设置格式。带字幕视频在本机后台生成。</p></div></header>
    <nav ref={tabsRef} className="tool-panel-tabs sliding-tabs" aria-label="导出类型">{([['subtitle','字幕文件'],['video','带字幕视频'],['package','项目包']] as const).map(([id,label]) => <button key={id} aria-pressed={category===id} onClick={() => {setCategory(id);if(id==='subtitle' && (format==='mp4'||format==='mkv'))choose('srt');if(id==='video' && format!=='mp4' && format!=='mkv')choose('mp4');}}>{label}</button>)}</nav>
    {category === 'package' ? <section className="export-options export-package" key="package"><p>项目包包含字幕、编辑历史与项目设置，可在另一台电脑继续编辑。</p><button className="button secondary" onClick={() => onPackage(false)}>导出精简项目包</button><button className="button primary" onClick={() => onPackage(true)}>包含媒体的完整项目包</button></section> : <section className="export-options" key={category}><div className="export-format-cards" role="radiogroup" aria-label="导出格式">{(category === 'subtitle' ? ['srt','vtt','ass','srt-bilingual'] : ['mp4','mkv']).map(value => <button type="button" role="radio" aria-checked={format===value} key={value} className={format===value ? 'selected' : ''} onClick={() => choose(value as ExportFormat)}><strong>{FORMAT_INFO[value][0]}</strong><small>{FORMAT_INFO[value][1]}</small></button>)}</div>{category === 'video' && <p>使用“样式”页设置的字体、位置和外观压制字幕。</p>}<label className="check-row"><input type="checkbox" checked={bilingual || format==='srt-bilingual'} disabled={format==='srt-bilingual'} onChange={event => onBilingual(event.target.checked)}/>包含原文和译文</label>{!hasSegments && <p>请先生成或导入字幕。</p>}{category==='video' && !hasVideo && <p>当前缺少视频素材，请先在任务面板准备素材。</p>}<button className="button primary" disabled={busy || !hasSegments || (category==='video' && !hasVideo)} onClick={() => onExport(format)}>{category==='video' ? '开始生成带字幕视频' : '导出字幕文件'}</button></section>}
    {task}
  </section>;
}
