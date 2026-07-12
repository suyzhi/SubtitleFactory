// 字幕工厂 - 字幕统计面板组件

import type { SubtitleStats } from '../types';

interface Props {
  stats: SubtitleStats | null;
}

export default function SubtitleStatsPanel({ stats }: Props) {
  if (!stats || stats.totalSegments === 0) return null;

  const fmt = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
  };

  const items = [
    { label: '生成字幕', value: `${stats.totalSegments} 条` },
    { label: '音频时长', value: stats.audioDuration ? fmt(stats.audioDuration) : '-' },
    { label: '平均时长', value: stats.averageDuration ? `${stats.averageDuration.toFixed(1)} 秒` : '-' },
    { label: '最短信', value: stats.minDuration ? `${stats.minDuration.toFixed(2)} 秒` : '-' },
    { label: '最长信', value: stats.maxDuration ? `${stats.maxDuration.toFixed(2)} 秒` : '-' },
    { label: '合并短信', value: stats.mergedShortSegments ? `${stats.mergedShortSegments} 条` : '0 条' },
    { label: '拆分长信', value: stats.splitLongSegments ? `${stats.splitLongSegments} 条` : '0 条' },
  ];

  return (
    <section className="panel subtitle-stats-panel">
      <h3>📊 转写统计</h3>
      <div className="stats-grid">
        {items.map(item => (
          <div key={item.label} className="stat-item">
            <span className="stat-label">{item.label}</span>
            <span className="stat-value">{item.value}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
