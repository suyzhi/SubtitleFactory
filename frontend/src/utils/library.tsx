// 项目库与字幕表共用的纯展示工具函数（无状态、无副作用）。

export function isPlaylistUrl(value: string) {
  try {
    const url = new URL(value);
    return /(^|\.)youtube\.com$/i.test(url.hostname) && !!url.searchParams.get('list');
  } catch { return false; }
}

export function highlightedSnippet(text: string, query: string) {
  const index = text.toLocaleLowerCase().indexOf(query.trim().toLocaleLowerCase());
  if (index < 0 || !query.trim()) return text;
  return <>
    {text.slice(0, index)}
    <mark>{text.slice(index, index + query.trim().length)}</mark>
    {text.slice(index + query.trim().length)}
  </>;
}

export function libraryTimecode(value: number) {
  const seconds = Math.max(0, Math.floor(value));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
    : `${minutes}:${String(remainder).padStart(2, '0')}`;
}

export function projectExportFilename(projectTitle: string | undefined, format: string, suffix = '') {
  const extension = format === 'srt-bilingual' ? 'bilingual.srt' : format;
  const title = projectTitle?.trim() || '字幕工厂';
  return `${title}${suffix ? `-${suffix}` : ''}.${extension}`;
}

export function fmtTime(seconds: number): string {
  if (!seconds && seconds !== 0) return '--:--.---';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 1000);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${ms.toString().padStart(3, '0')}`;
}
