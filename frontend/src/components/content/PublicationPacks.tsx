import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../../api/backend';
import type { ContentPack, ContentPackInputMode, ContentSection, Project } from '../../types';
import AppSelect from '../AppSelect';
import { errorMessage, TERMINAL_TASKS } from './helpers';
import SectionEditor from './SectionEditor';

interface Props {
  project: Project;
  hasSegments: boolean;
  granted: boolean;
  onRequireAuthorization: () => Promise<boolean>;
  onMessage: (message: string) => void;
}

export default function PublicationPacks({
  project,
  hasSegments,
  granted,
  onRequireAuthorization,
  onMessage,
}: Props) {
  const [packs, setPacks] = useState<ContentPack[]>([]);
  const [selected, setSelected] = useState<ContentPack | null>(null);
  const [name, setName] = useState('内容发布包');
  const [inputMode, setInputMode] = useState<ContentPackInputMode>('original');
  const [outputLanguage, setOutputLanguage] = useState('auto');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [renameDraft, setRenameDraft] = useState<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback(
    async (preferredId?: string) => {
      const result = await api.getContentPacks(project.id);
      if (!alive.current) return;
      setPacks(result.packs);
      const id = preferredId || selected?.id || result.packs[0]?.id;
      if (!id) {
        setSelected(null);
        return;
      }
      const detail = await api.getContentPack(id);
      if (alive.current) setSelected(detail);
    },
    [project.id, selected?.id],
  );

  useEffect(() => {
    setSelected(null);
    setPacks([]);
    void refresh().catch(reason => setError(errorMessage(reason)));
  }, [project.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => setRenameDraft(null), [selected?.id]);

  const waitForTask = useCallback(
    async (taskId: string, packId: string) => {
      while (alive.current) {
        const task = await api.getTaskStatus(taskId);
        if (TERMINAL_TASKS.has(task.status)) {
          await refresh(packId);
          if (task.status === 'failed') throw new Error(task.error || task.message);
          onMessage(
            task.status === 'partial'
              ? '发布包部分生成完成，可单独重试失败区域'
              : '内容发布包已生成',
          );
          return;
        }
        await new Promise(resolve => window.setTimeout(resolve, 1000));
      }
    },
    [onMessage, refresh],
  );

  const create = async (allowFallback = false) => {
    if (!hasSegments) return;
    if (!granted && !(await onRequireAuthorization())) return;
    setBusy(true);
    setError('');
    try {
      const result = await api.createContentPack(project.id, {
        name,
        input_mode: inputMode,
        output_language: outputLanguage,
        allow_translation_fallback: allowFallback,
      });
      setSelected(result.pack);
      await refresh(result.pack.id);
      void waitForTask(result.task_id, result.pack.id).catch(reason =>
        setError(errorMessage(reason)),
      );
    } catch (reason) {
      if (
        reason instanceof api.BackendError &&
        reason.code === 'TRANSLATION_INCOMPLETE' &&
        !allowFallback
      ) {
        const coverage = Number(reason.details.coverage || 0);
        if (
          window.confirm(
            `译文覆盖率为 ${(coverage * 100).toFixed(1)}%。是否明确允许缺失部分使用原文补齐？`,
          )
        ) {
          await create(true);
        }
      } else {
        setError(errorMessage(reason));
      }
    } finally {
      setBusy(false);
    }
  };

  const regenerate = async (section: ContentSection) => {
    if (!granted && !(await onRequireAuthorization())) return;
    if (
      !window.confirm(
        `重新生成“${section.title}”会替换这个区域的人工修改，其他区域不受影响。继续吗？`,
      )
    ) {
      return;
    }
    try {
      const result = await api.regenerateContentSection(selected!.id, section.kind);
      onMessage(`正在重新生成${section.title}`);
      void waitForTask(result.task_id, selected!.id).catch(reason =>
        setError(errorMessage(reason)),
      );
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const rename = () => {
    if (selected) setRenameDraft(selected.name);
  };

  const saveRename = async () => {
    if (!selected || renameDraft === null) return;
    const next = renameDraft.trim();
    if (!next) return;
    if (next === selected.name) {
      setRenameDraft(null);
      return;
    }
    try {
      const pack = await api.updateContentPack(selected.id, next, selected.revision);
      setSelected(pack);
      setRenameDraft(null);
      await refresh(pack.id);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const remove = async () => {
    if (!selected || !window.confirm(`删除“${selected.name}”？此操作不会删除字幕。`)) return;
    try {
      await api.deleteContentPack(selected.id);
      setSelected(null);
      await refresh();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const exportPack = async () => {
    if (!selected) return;
    try {
      const result = await api.exportContentPack(selected.id);
      const saved = await api.downloadContentPack(
        result.export_id,
        `${selected.name}.zip`,
        result.path,
      );
      onMessage(saved ? '内容发布包 ZIP 已导出' : '已取消保存内容发布包');
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  return (
    <div className="content-pack-layout">
      <aside className="content-collection-list">
        <header>
          <strong>发布包</strong>
          <span>{packs.length}</span>
        </header>
        {packs.map(pack => (
          <button
            key={pack.id}
            className={selected?.id === pack.id ? 'active' : ''}
            onClick={() =>
              void api
                .getContentPack(pack.id)
                .then(setSelected)
                .catch(reason => setError(errorMessage(reason)))
            }
          >
            <span>
              <strong>{pack.name}</strong>
              <small>
                {pack.input_mode === 'original'
                  ? '原文'
                  : pack.input_mode === 'translated'
                    ? '译文'
                    : '双语'}{' '}
                · {pack.updated_at}
              </small>
            </span>
            <em className={pack.stale ? 'stale' : pack.status}>
              {pack.stale ? '已过期' : pack.status}
            </em>
          </button>
        ))}
        {!packs.length && <p>还没有发布包。右侧设置来源后开始生成。</p>}
      </aside>
      <section className="content-pack-main">
        {!selected && (
          <div className="content-create-card">
            <header>
              <small>新建发布包</small>
              <h2>把字幕整理成一套可编辑发布资料</h2>
              <p>六个区域独立生成，任何一个失败都不会覆盖其他成功内容。</p>
            </header>
            <label>
              名称
              <input value={name} onChange={event => setName(event.target.value)} />
            </label>
            <div className="content-create-grid">
              <label>
                输入内容
                <AppSelect
                  value={inputMode}
                  onChange={value => setInputMode(value as ContentPackInputMode)}
                  label="发布包输入内容"
                  options={[
                    { value: 'original', label: '整理后原文' },
                    { value: 'translated', label: '译文', description: '不完整时会先确认' },
                    { value: 'bilingual', label: '双语', description: '原文与译文共同参考' },
                  ]}
                />
              </label>
              <label>
                输出语言
                <input
                  value={outputLanguage}
                  onChange={event => setOutputLanguage(event.target.value)}
                  placeholder="auto / 中文 / English"
                />
              </label>
            </div>
            <button
              className="button primary"
              disabled={busy || !name.trim() || !hasSegments}
              onClick={() => void create()}
            >
              {busy ? '正在创建…' : '生成完整发布包'}
            </button>
          </div>
        )}
        {selected && (
          <>
            <header className="content-pack-toolbar">
              <div>
                {renameDraft === null ? (
                  <span>
                    <strong>{selected.name}</strong>
                    {selected.stale && <em>源字幕已更新</em>}
                  </span>
                ) : (
                  <form
                    className="content-pack-rename"
                    onSubmit={event => {
                      event.preventDefault();
                      void saveRename();
                    }}
                  >
                    <input
                      autoFocus
                      aria-label="发布包名称"
                      maxLength={120}
                      value={renameDraft}
                      onChange={event => setRenameDraft(event.target.value)}
                      onKeyDown={event => {
                        if (event.key === 'Escape') setRenameDraft(null);
                      }}
                    />
                    <button className="button primary" disabled={!renameDraft.trim()}>
                      保存
                    </button>
                    <button
                      type="button"
                      className="button secondary"
                      onClick={() => setRenameDraft(null)}
                    >
                      取消
                    </button>
                  </form>
                )}
                <small>
                  {selected.provider_id || '内容服务'} · {selected.model || '默认模型'} · 字幕修订{' '}
                  {selected.source_revision}
                </small>
              </div>
              <div>
                <button
                  className="button secondary"
                  disabled={renameDraft !== null}
                  onClick={rename}
                >
                  重命名
                </button>
                <button
                  className="button secondary"
                  onClick={() =>
                    void navigator.clipboard
                      .writeText(JSON.stringify(selected.sections || [], null, 2))
                      .then(() => onMessage('发布包结构已复制'))
                  }
                >
                  复制
                </button>
                <button className="button secondary" onClick={() => void exportPack()}>
                  导出 ZIP
                </button>
                <button className="button secondary danger" onClick={() => void remove()}>
                  删除
                </button>
                <button className="button primary" onClick={() => setSelected(null)}>
                  新建
                </button>
              </div>
            </header>
            {selected.stale && (
              <div className="content-stale-banner">
                字幕在发布包生成后有过修改。旧内容和人工编辑都已保留，请按需重新生成具体区域。
              </div>
            )}
            <div className="content-section-list">
              {(selected.sections || []).map(section => (
                <SectionEditor
                  key={section.id}
                  packId={selected.id}
                  section={section}
                  stale={selected.stale}
                  provider={selected.provider_id}
                  model={selected.model}
                  onSaved={pack => {
                    setSelected(pack);
                    setPacks(current =>
                      current.map(item =>
                        item.id === pack.id ? { ...item, ...pack, sections: undefined } : item,
                      ),
                    );
                  }}
                  onRegenerate={item => void regenerate(item)}
                />
              ))}
            </div>
          </>
        )}
        {error && <p className="content-error">{error}</p>}
      </section>
    </div>
  );
}
