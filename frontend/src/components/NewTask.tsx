import { useMemo, useRef, useState } from 'react';
import { LANGUAGES, TARGET_LANGUAGES } from '../languages';
import { defaultRuntime, formatBytes, localRuntimes } from '../format';
import type { TranscriptionModel } from '../types';

export interface JobOptions {
  model: string;
  runtime: string;
  language: string;
  clean: boolean;
  translate: boolean;
  targetLanguage: string;
}

export type JobSource = { kind: 'link'; url: string } | { kind: 'file'; file: File };

interface Props {
  models: TranscriptionModel[];
  options: JobOptions;
  onOptionsChange: (options: JobOptions) => void;
  youtubeEnabled: boolean;
  disabled: boolean;
  onStart: (source: JobSource) => Promise<void>;
}

const VIDEO_PATTERN = /\.(mp4|mkv|mov|webm|avi)$/i;

export default function NewTask({ models, options, onOptionsChange, youtubeEnabled, disabled, onStart }: Props) {
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const set = (patch: Partial<JobOptions>) => onOptionsChange({ ...options, ...patch });
  const selectedModel = models.find(model => model.id === options.model);
  const runtimes = localRuntimes(selectedModel);
  const runtime = runtimes.find(item => item.id === options.runtime);

  const groups = useMemo(() => {
    const map = new Map<string, TranscriptionModel[]>();
    for (const model of models) {
      if (!localRuntimes(model).length) continue;
      const key = model.category_name || '其他';
      map.set(key, [...(map.get(key) || []), model]);
    }
    return [...map.entries()];
  }, [models]);

  const pickFile = (picked: File | undefined) => {
    if (!picked) return;
    if (!VIDEO_PATTERN.test(picked.name)) return;
    setFile(picked);
    setUrl('');
  };

  const source: JobSource | null = file ? { kind: 'file', file } : url.trim() ? { kind: 'link', url: url.trim() } : null;

  const start = async () => {
    if (!source || busy) return;
    setBusy(true);
    try {
      await onStart(source);
      setUrl('');
      setFile(null);
    } finally {
      setBusy(false);
    }
  };

  const modelHint = !runtime ? '' : runtime.model_ready
    ? '模型已下载'
    : `首次使用会先下载模型${runtime.download_bytes ? `（${formatBytes(runtime.download_bytes)}）` : ''}`;

  return (
    <section className="new-task">
      <header className="page-header">
        <h1>生成字幕</h1>
        <p>{youtubeEnabled ? '粘贴视频链接或选择本地视频，一键完成转写、整理和翻译。' : '选择本地视频，一键完成转写、整理和翻译。'}</p>
      </header>

      <div
        className={`source-box${dragging ? ' dragging' : ''}`}
        onDragOver={event => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={event => { event.preventDefault(); setDragging(false); pickFile(event.dataTransfer.files[0]); }}
      >
        {file ? (
          <div className="picked-file">
            <span className="file-icon" aria-hidden>▶</span>
            <span className="file-name">{file.name}</span>
            <button type="button" className="link-button" onClick={() => setFile(null)}>更换</button>
          </div>
        ) : (
          <>
            {youtubeEnabled && (
              <input
                className="url-input"
                type="url"
                placeholder="粘贴 YouTube 视频链接"
                value={url}
                onChange={event => setUrl(event.target.value)}
                onKeyDown={event => { if (event.key === 'Enter') void start(); }}
                aria-label="视频链接"
              />
            )}
            <div className="source-or">
              {youtubeEnabled && <span>或</span>}
              <button type="button" className="button secondary" onClick={() => fileInput.current?.click()}>选择本地视频…</button>
              <small>也可以把视频拖到这里</small>
            </div>
          </>
        )}
        <input
          ref={fileInput}
          type="file"
          hidden
          accept=".mp4,.mkv,.mov,.webm,.avi,video/*"
          onChange={event => { pickFile(event.target.files?.[0]); event.target.value = ''; }}
        />
      </div>

      <div className="option-card">
        <div className="option-row">
          <label className="field grow">
            <span>转写模型</span>
            <select
              value={options.model}
              onChange={event => {
                const model = models.find(item => item.id === event.target.value);
                set({ model: event.target.value, runtime: defaultRuntime(model) });
              }}
            >
              {groups.map(([group, items]) => (
                <optgroup key={group} label={group}>
                  {items.map(model => <option key={model.id} value={model.id}>{model.name}{model.ready ? ' ✓' : ''}</option>)}
                </optgroup>
              ))}
            </select>
          </label>
          <label className="field">
            <span>运行设备</span>
            <select value={options.runtime} onChange={event => set({ runtime: event.target.value })}>
              {runtimes.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label className="field">
            <span>视频语言</span>
            <select value={options.language} onChange={event => set({ language: event.target.value })}>
              {LANGUAGES.map(language => <option key={language.code} value={language.code}>{language.name}</option>)}
            </select>
          </label>
        </div>
        {modelHint && <p className="field-hint">{modelHint}</p>}

        <div className="divider" />

        <label className="toggle-row">
          <span>
            <strong>AI 整理</strong>
            <small>修正断句和标点，合并过碎的字幕</small>
          </span>
          <input type="checkbox" role="switch" checked={options.clean} onChange={event => set({ clean: event.target.checked })} />
        </label>
        <label className="toggle-row">
          <span>
            <strong>AI 翻译</strong>
            <small>生成译文，导出时可选双语字幕</small>
          </span>
          <input type="checkbox" role="switch" checked={options.translate} onChange={event => set({ translate: event.target.checked })} />
        </label>
        {options.translate && (
          <label className="field inline">
            <span>翻译为</span>
            <select value={options.targetLanguage} onChange={event => set({ targetLanguage: event.target.value })}>
              {TARGET_LANGUAGES.map(language => <option key={language.code} value={language.code}>{language.name}</option>)}
            </select>
          </label>
        )}
      </div>

      <button type="button" className="button primary large" disabled={!source || !options.runtime || disabled || busy} onClick={() => void start()}>
        {busy ? '正在开始…' : '开始生成字幕'}
      </button>
    </section>
  );
}
