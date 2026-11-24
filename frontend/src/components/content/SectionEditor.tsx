import { useEffect, useRef, useState } from 'react';
import * as api from '../../api/backend';
import type { ContentPack, ContentSection } from '../../types';
import { errorMessage, timecode, type SaveState } from './helpers';

function SectionFields({
  kind,
  value,
  onChange,
}: {
  kind: ContentSection['kind'];
  value: Record<string, any>;
  onChange: (next: Record<string, any>) => void;
}) {
  const set = (key: string, next: unknown) => onChange({ ...value, [key]: next });
  const setArrayItem = (key: string, index: number, patch: Record<string, unknown>) => {
    const items = [...(Array.isArray(value[key]) ? value[key] : [])];
    items[index] = { ...items[index], ...patch };
    set(key, items);
  };

  if (kind === 'summary') {
    return (
      <div className="content-fields">
        <label>
          内容摘要
          <textarea
            value={String(value.overview || '')}
            onChange={event => set('overview', event.target.value)}
          />
        </label>
        <label>
          要点（每行一条）
          <textarea
            value={(value.key_points || []).join('\n')}
            onChange={event =>
              set('key_points', event.target.value.split('\n').filter(Boolean))
            }
          />
        </label>
      </div>
    );
  }
  if (kind === 'chapters') {
    return (
      <div className="structured-item-list">
        {(value.chapters || []).map((item: any, index: number) => (
          <article key={`${item.start_index}-${index}`}>
            <span>
              {timecode(item.start)} → {timecode(item.end)}
            </span>
            <input
              aria-label={`章节 ${index + 1} 标题`}
              value={String(item.title || '')}
              onChange={event => setArrayItem('chapters', index, { title: event.target.value })}
            />
            <textarea
              aria-label={`章节 ${index + 1} 摘要`}
              value={String(item.summary || '')}
              onChange={event => setArrayItem('chapters', index, { summary: event.target.value })}
            />
          </article>
        ))}
      </div>
    );
  }
  if (kind === 'quotes') {
    return (
      <div className="structured-item-list">
        {(value.quotes || []).map((item: any, index: number) => (
          <article key={`${item.segment_index}-${index}`}>
            <span>
              {timecode(item.start)} · {item.speaker || '未标说话人'}
            </span>
            <textarea
              aria-label={`金句 ${index + 1}`}
              value={String(item.text || '')}
              onChange={event => setArrayItem('quotes', index, { text: event.target.value })}
            />
            <input
              aria-label={`金句 ${index + 1} 说明`}
              value={String(item.reason || '')}
              onChange={event => setArrayItem('quotes', index, { reason: event.target.value })}
            />
          </article>
        ))}
      </div>
    );
  }
  if (kind === 'youtube') {
    return (
      <div className="content-fields">
        <label>
          标题候选（每行一个）
          <textarea
            value={(value.titles || []).join('\n')}
            onChange={event => set('titles', event.target.value.split('\n').filter(Boolean))}
          />
        </label>
        <label>
          简介
          <textarea
            value={String(value.description || '')}
            onChange={event => set('description', event.target.value)}
          />
        </label>
        <label>
          章节文本
          <textarea
            value={String(value.chapter_text || '')}
            onChange={event => set('chapter_text', event.target.value)}
          />
        </label>
        <label>
          标签（逗号分隔）
          <input
            value={(value.tags || []).join(', ')}
            onChange={event =>
              set(
                'tags',
                event.target.value
                  .split(',')
                  .map(item => item.trim())
                  .filter(Boolean),
              )
            }
          />
        </label>
      </div>
    );
  }
  if (kind === 'podcast') {
    return (
      <div className="content-fields">
        <label>
          播客标题
          <input
            value={String(value.title || '')}
            onChange={event => set('title', event.target.value)}
          />
        </label>
        <label>
          节目简介
          <textarea
            value={String(value.intro || '')}
            onChange={event => set('intro', event.target.value)}
          />
        </label>
        <label>
          Show Notes
          <textarea
            value={String(value.show_notes || '')}
            onChange={event => set('show_notes', event.target.value)}
          />
        </label>
      </div>
    );
  }
  return (
    <div className="content-fields">
      <label>
        小红书
        <textarea
          value={String(value.xiaohongshu || '')}
          onChange={event => set('xiaohongshu', event.target.value)}
        />
      </label>
      <label>
        公众号
        <textarea
          value={String(value.wechat || '')}
          onChange={event => set('wechat', event.target.value)}
        />
      </label>
      <label>
        通用社交
        <textarea
          value={String(value.generic || '')}
          onChange={event => set('generic', event.target.value)}
        />
      </label>
    </div>
  );
}

interface Props {
  packId: string;
  section: ContentSection;
  stale: boolean;
  provider?: string | null;
  model?: string | null;
  onSaved: (pack: ContentPack) => void;
  onRegenerate: (section: ContentSection) => void;
}

export default function SectionEditor({
  packId,
  section,
  stale,
  provider,
  model,
  onSaved,
  onRegenerate,
}: Props) {
  const [draft, setDraft] = useState<Record<string, any>>(section.content || {});
  const [saveState, setSaveState] = useState<SaveState>('idle');
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const sectionId = useRef(section.id);

  useEffect(() => {
    setDraft(section.content || {});
    setDirty(false);
    if (sectionId.current !== section.id) {
      sectionId.current = section.id;
      setSaveState('idle');
    }
    setError('');
  }, [section.content, section.id, section.revision]);

  useEffect(() => {
    if (!dirty) return;
    const timer = window.setTimeout(() => {
      setSaveState('saving');
      setError('');
      void api
        .updateContentSection(packId, section.kind, draft, section.revision)
        .then(pack => {
          setDirty(false);
          setSaveState('saved');
          onSaved(pack);
        })
        .catch(reason => {
          setSaveState('error');
          setError(errorMessage(reason));
        });
    }, 800);
    return () => window.clearTimeout(timer);
  }, [dirty, draft, onSaved, packId, section.kind, section.revision]);

  return (
    <details className={`content-section ${section.status}`} open={section.kind === 'summary'}>
      <summary>
        <span>
          <strong>{section.title}</strong>
          <small>
            {section.status === 'failed'
              ? section.error
              : section.generated_at
                ? `${section.generated_at} · 修订 ${section.revision}`
                : '等待生成'}
          </small>
        </span>
        <em className={saveState}>
          {saveState === 'saving'
            ? '保存中…'
            : saveState === 'saved'
              ? '已保存'
              : saveState === 'error'
                ? '保存失败'
                : stale
                  ? '源字幕已更新'
                  : section.status === 'ready'
                    ? '已就绪'
                    : section.status}
        </em>
      </summary>
      {section.status === 'failed' && <p className="content-error">{section.error}</p>}
      <SectionFields
        kind={section.kind}
        value={draft}
        onChange={next => {
          setDraft(next);
          setDirty(true);
          setSaveState('idle');
        }}
      />
      {error && <p className="content-error">{error}</p>}
      <footer>
        <span>
          {stale
            ? `源字幕已更新 · ${provider || '内容服务'} / ${model || '默认模型'} · 重新生成只替换本区域`
            : `生成来源：${section.generated_at ? `${provider || '内容服务'} / ${model || '默认模型'} + 人工编辑` : '等待 AI'}`}
        </span>
        <button className="button secondary" onClick={() => onRegenerate(section)}>
          重新生成本区域
        </button>
      </footer>
    </details>
  );
}
