import OverlayDialog from './OverlayDialog';
import {useTaskSnapshot} from '../taskStore';
import {taskProgressLabel} from '../projectState';
import {useState} from 'react';
import type {TaskStatus} from '../types';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../api/backend';
import { recoveryActionLabel } from '../taskRecovery';

const TASK_LABELS: Record<string, string> = {
  download: '下载素材',
  prepare_audio: '准备网页音频',
  extract_audio: '提取音频',
  transcribe: '语音转写',
  workflow: '自动字幕工作流',
  clean: 'AI 整理',
  translate: 'AI 翻译',
  render: '导出成片',
  ocr: '硬字幕 OCR',
  speaker_diarization: '说话人识别',
  prepare_model: '准备转写模型',
  prepare_speaker_models: '准备说话人模型',
  content_generate: '生成内容发布包',
  clip_recommend: '推荐短片候选',
  clip_render_batch: '批量渲染短片',
};

interface Props {
  open: boolean;
  onClose: () => void;
  onOpenProject: (projectId: string) => void;
  onOpenSettings?: () => void;
}

export default function GlobalTaskDrawer({ open, onClose, onOpenProject, onOpenSettings }: Props) {
  const client = useQueryClient();
  const [error,setError]=useState('');
  const query = useQuery({
    queryKey: ['global-tasks'], queryFn: () => api.getGlobalTasks(),
    refetchInterval: open ? 2000 : 5000,
  });
  const act = async (taskId: string, action: 'pause' | 'resume' | 'cancel') => {
    setError('');
    try {
    if (action === 'pause') await api.pauseTask(taskId);
    else if (action === 'resume') await api.resumeTask(taskId);
    else await api.cancelTask(taskId);
    await client.invalidateQueries({ queryKey: ['global-tasks'] });
    } catch(error) {setError(error instanceof Error ? error.message : '操作失败，请重试');}
  };
  if (!open) return null;
  const tasks = query.data?.tasks || [];
  return <OverlayDialog label="全局任务中心" onClose={onClose}><aside className="global-task-drawer" aria-label="全局任务中心">
    <header><div><small>所有项目</small><h2>任务中心</h2></div><button className="panel-close" aria-label="关闭任务中心" onClick={onClose}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg></button></header>
    <div className="global-task-list">
      {(error || query.error) && <p role="alert">{error || String(query.error)}<button onClick={() => void query.refetch()}>重新加载</button></p>}
      {query.isLoading && <p role="status">正在加载任务…</p>}
      {!tasks.length && !query.isLoading && <div className="global-task-empty"><strong>没有任务</strong><span>转写、翻译和导出等后台任务会显示在这里</span></div>}
      {tasks.map(task => <TaskCard key={task.id} initial={task} act={act} onOpenProject={onOpenProject} onOpenSettings={onOpenSettings}/>)}
    </div>
  </aside></OverlayDialog>;
}

const STATUS_TEXT: Record<string, string> = {
  pending: '排队中', running: '进行中', paused: '已暂停', success: '已完成', failed: '失败', cancelled: '已取消', partial: '部分完成',
};

function relativeTime(value: string) {
  const time = Date.parse(value.replace(' ', 'T'));
  if (!Number.isFinite(time)) return '';
  const minutes = Math.round((Date.now() - time) / 60000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} 小时前`;
  return `${Math.round(minutes / 1440)} 天前`;
}

function TaskCard({initial,act,onOpenProject,onOpenSettings}:{initial:TaskStatus;act:(id:string,action:'pause'|'resume'|'cancel')=>Promise<void>;onOpenProject:(id:string)=>void;onOpenSettings?:()=>void}) {
  const task=useTaskSnapshot(initial.id) || initial;
  const active=['pending','running','paused'].includes(task.status);
  const batchTitle=String(task.details?.batch_title||''); const batchItem=String(task.details?.batch_item_title||'');
  const title=batchTitle ? `${batchTitle} · ${task.details?.batch_position}. ${batchItem}` : TASK_LABELS[task.type] || task.type;
  const detail=active ? taskProgressLabel(task) : (batchTitle ? `${TASK_LABELS[task.type] || task.type} · ` : '') + (task.message || task.step || '');
  return <article className={task.status}>
        <button className="global-task-main" onClick={() => task.project_id && onOpenProject(task.project_id)}>
          <i className="task-status-icon" aria-hidden="true">{task.status === 'success' ? '✓' : task.status === 'failed' ? '!' : task.status === 'paused' ? '‖' : ''}</i>
          <span><strong>{title}</strong><small>{detail}</small></span>
          <em>{active && task.status !== 'pending' ? `${Math.round(task.progress || 0)}%` : STATUS_TEXT[task.status] || task.status}<time>{relativeTime(task.updated_at)}</time></em>
          {active && <progress max={100} value={task.progress || 0}/>}
        </button>
        {task.status === 'failed' && <section className="global-task-failure">
          <strong>{task.error_code === 'APP_INTERRUPTED' ? '上次运行被中断' : task.error_code || '任务失败'} · 第 {task.attempt || 1} 次尝试</strong>
          <span>{task.suggestion || task.details?.failure_suggestion || task.error || ''}</span>
        </section>}
        <div>{task.status === 'running' && <button onClick={() => void act(task.id, 'pause')}>暂停</button>}{task.status === 'paused' && <button onClick={() => void act(task.id, 'resume')}>继续</button>}{active && <button onClick={() => void act(task.id, 'cancel')}>取消</button>}{task.status === 'failed' && task.recoverable && task.project_id && <button onClick={() => onOpenProject(task.project_id)}>打开项目 · {recoveryActionLabel(task)}</button>}{task.available_actions?.includes('open_settings') && onOpenSettings && <button onClick={onOpenSettings}>打开设置</button>}</div>
      </article>;
}
