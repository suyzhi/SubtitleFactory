import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../api/backend';
import type { SubtitleStyleSettings } from '../types';
import { DEFAULT_SUBTITLE_STYLE } from '../subtitleStyle';

interface Props { style: SubtitleStyleSettings; onApply: (style: SubtitleStyleSettings) => void; }

export default function StyleTemplateBar({ style, onApply }: Props) {
  const client = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState('');
  const [fontWarning, setFontWarning] = useState('');
  const query = useQuery({ queryKey: ['style-templates'], queryFn: api.getStyleTemplates });
  const templates = useMemo(() => query.data?.templates || [], [query.data?.templates]);
  const save = useMutation({
    mutationFn: (name: string) => api.createStyleTemplate(name, style as unknown as Record<string, unknown>),
    onSuccess: () => client.invalidateQueries({ queryKey: ['style-templates'] }),
  });
  const normalizeSettings = (value: Record<string, unknown>) => {
    const settings = { ...DEFAULT_SUBTITLE_STYLE, ...value } as SubtitleStyleSettings;
    const font = String(settings.fontFamily || '');
    if (font && document.fonts && !document.fonts.check(`16px ${font}`)) {
      settings.fontFamily = DEFAULT_SUBTITLE_STYLE.fontFamily;
      setFontWarning(`模板字体“${font}”不可用，已回退到系统字体。`);
    } else setFontWarning('');
    return settings;
  };
  const apply = (identifier: string) => {
    setSelected(identifier);
    const template = templates.find(item => item.id === identifier); if (!template) return;
    onApply(normalizeSettings(template.settings));
  };
  const exportJson = () => {
    const blob = new Blob([JSON.stringify({ name: '字幕样式', settings: style }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'subtitle-style.json'; anchor.click(); URL.revokeObjectURL(url);
  };
  const importJson = async (file?: File) => {
    if (!file) return;
    const payload = JSON.parse(await file.text());
    const settings = payload.settings || payload;
    onApply(normalizeSettings(settings));
    await api.createStyleTemplate(payload.name || file.name.replace(/\.json$/i, ''), settings);
    await client.invalidateQueries({ queryKey: ['style-templates'] });
  };
  return <div className="style-template-bar">
    <label>样式模板<select value={selected} onChange={event => apply(event.target.value)}><option value="">选择模板…</option>{templates.map(item => <option key={item.id} value={item.id}>{item.builtin ? '系统 · ' : ''}{item.name}</option>)}</select></label>
    <button onClick={() => { const name = window.prompt('模板名称'); if (name?.trim()) save.mutate(name.trim()); }}>保存当前样式</button>
    <button onClick={exportJson}>导出 JSON</button><button onClick={() => fileRef.current?.click()}>导入 JSON</button>
    <input ref={fileRef} hidden type="file" accept="application/json,.json" onChange={event => void importJson(event.target.files?.[0])}/>
    {fontWarning && <span role="status">{fontWarning}</span>}
  </div>;
}
