import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../../api/backend';
import type { ClipSet, Project } from '../../types';
import AppSelect from '../AppSelect';
import ClipCandidateCard from './ClipCandidateCard';
import { errorMessage, TERMINAL_TASKS } from './helpers';

interface Props {
  project: Project;
  hasSegments: boolean;
  granted: boolean;
  onRequireAuthorization: () => Promise<boolean>;
  onPreview: (start: number, end: number) => void;
  onMessage: (message: string) => void;
}

export default function ClipWorkbench({
  project,
  hasSegments,
  granted,
  onRequireAuthorization,
  onPreview,
  onMessage,
}: Props) {
  const [sets, setSets] = useState<ClipSet[]>([]);
  const [selected, setSelected] = useState<ClipSet | null>(null);
  const [name, setName] = useState('短片候选');
  const [count, setCount] = useState<3 | 5 | 10>(5);
  const [minimum, setMinimum] = useState(30);
  const [maximum, setMaximum] = useState(90);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback(
    async (preferredId?: string) => {
      const result = await api.getClipSets(project.id);
      if (!alive.current) return;
      setSets(result.clip_sets);
      const id = preferredId || selected?.id || result.clip_sets[0]?.id;
      if (id) {
        const detail = await api.getClipSet(id);
        if (alive.current) setSelected(detail);
      } else {
        setSelected(null);
      }
    },
    [project.id, selected?.id],
  );

  useEffect(() => {
    setSelected(null);
    setSets([]);
    void refresh().catch(reason => setError(errorMessage(reason)));
  }, [project.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const waitForTask = useCallback(
    async (taskId: string, setId: string, message: string) => {
      while (alive.current) {
        const task = await api.getTaskStatus(taskId);
        if (TERMINAL_TASKS.has(task.status)) {
          await refresh(setId);
          if (task.status === 'failed') throw new Error(task.error || task.message);
          onMessage(message);
          return;
        }
        await new Promise(resolve => window.setTimeout(resolve, 1000));
      }
    },
    [onMessage, refresh],
  );

  const create = async () => {
    if (!hasSegments) return;
    if (!granted && !(await onRequireAuthorization())) return;
    setBusy(true);
    setError('');
    try {
      const result = await api.createClipSet(project.id, {
        name,
        desired_count: count,
        min_duration: minimum,
        max_duration: maximum,
      });
      await refresh(result.clip_set_id);
      void waitForTask(result.task_id, result.clip_set_id, '短片候选已生成').catch(reason =>
        setError(errorMessage(reason)),
      );
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const render = async () => {
    if (!selected) return;
    const hasSelectedStaleCandidate = (selected.candidates || []).some(
      candidate => candidate.selected && candidate.stale,
    );
    const items = (selected.candidates || []).flatMap(candidate =>
      candidate.selected
        ? candidate.layouts
            .filter(layout => layout.enabled)
            .map(layout => ({
              candidate_id: candidate.id,
              aspect_ratio: layout.aspect_ratio,
            }))
        : [],
    );
    if (!items.length) {
      setError('请先选择至少一个候选，并启用一个输出比例。');
      return;
    }
    if (
      project.source_type === 'youtube' &&
      !project.video_available &&
      !window.confirm(
        '这个网页播放项目需要先下载本地视频。实际占用取决于源视频画质，下载完成后才会开始渲染。继续吗？',
      )
    ) {
      return;
    }
    if (
      hasSelectedStaleCandidate &&
      !window.confirm('字幕已经更新。请确认你已重新预览当前范围；继续渲染吗？')
    ) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      const result = await api.renderClips(project.id, {
        items,
        confirm_stale: hasSelectedStaleCandidate,
      });
      if (result.task_id) {
        onMessage(`已提交 ${items.length} 个短片输出，可在任务中心暂停或取消`);
        void waitForTask(result.task_id, selected.id, '短片渲染完成').catch(reason =>
          setError(errorMessage(reason)),
        );
      } else {
        await refresh(selected.id);
        onMessage('相同配置的成片已存在，已直接复用');
      }
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="clip-workbench">
      <header className="clip-workbench-toolbar">
        <div>
          <h2>长视频短片工作台</h2>
          <p>AI 只推荐真实字幕范围；确认前不会下载、裁切或渲染视频。</p>
        </div>
        {selected && (
          <div>
            <AppSelect
              value={selected.id}
              onChange={id =>
                void api
                  .getClipSet(id)
                  .then(setSelected)
                  .catch(reason => setError(errorMessage(reason)))
              }
              label="短片集合"
              options={sets.map(item => ({
                value: item.id,
                label: item.name,
                description: `${item.candidate_count || 0} 个候选`,
              }))}
            />
            <button className="button secondary" onClick={() => setSelected(null)}>
              新建集合
            </button>
            <button className="button primary" disabled={busy} onClick={() => void render()}>
              渲染所选版本
            </button>
          </div>
        )}
      </header>
      {!selected && (
        <section className="clip-create-card">
          <label>
            集合名称
            <input value={name} onChange={event => setName(event.target.value)} />
          </label>
          <label>
            推荐数量
            <AppSelect
              value={String(count)}
              onChange={value => setCount(Number(value) as 3 | 5 | 10)}
              label="短片推荐数量"
              options={[3, 5, 10].map(value => ({ value: String(value), label: `${value} 个` }))}
            />
          </label>
          <label>
            最短秒数
            <input
              type="number"
              min={15}
              max={179}
              value={minimum}
              onChange={event => setMinimum(Number(event.target.value))}
            />
          </label>
          <label>
            最长秒数
            <input
              type="number"
              min={16}
              max={180}
              value={maximum}
              onChange={event => setMaximum(Number(event.target.value))}
            />
          </label>
          <button
            className="button primary"
            disabled={busy || minimum >= maximum || !hasSegments}
            onClick={() => void create()}
          >
            {busy ? '正在创建…' : '生成候选'}
          </button>
        </section>
      )}
      {selected?.stale && (
        <div className="content-stale-banner">
          源字幕已更新。候选仍然保留，但渲染前必须重新预览并确认当前时间范围。
        </div>
      )}
      {selected && (
        <div className="clip-candidate-list">
          {(selected.candidates || []).map(candidate => (
            <ClipCandidateCard
              key={candidate.id}
              candidate={candidate}
              previewImage={project.thumbnail_access_url || project.thumbnail_url}
              onChange={next => {
                setSelected(next);
                setSets(current =>
                  current.map(item =>
                    item.id === next.id ? { ...item, ...next, candidates: undefined } : item,
                  ),
                );
              }}
              onPreview={onPreview}
              onMessage={onMessage}
            />
          ))}
        </div>
      )}
      {error && <p className="content-error">{error}</p>}
    </div>
  );
}
