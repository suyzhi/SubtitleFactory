import { useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../api/backend';

const TASK_LABELS: Record<string, string> = {
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
  const query = useQuery({
    queryKey: ['global-tasks'], queryFn: () => api.getGlobalTasks(), enabled: open,
    refetchInterval: open ? 2000 : false,
  });
  const act = async (taskId: string, action: 'pause' | 'resume' | 'cancel') => {
    if (action === 'pause') await api.pauseTask(taskId);
    else if (action === 'resume') await api.resumeTask(taskId);
    else await api.cancelTask(taskId);
    await client.invalidateQueries({ queryKey: ['global-tasks'] });
  };
  if (!open) return null;
  const tasks = query.data?.tasks || [];
  return <aside className="global-task-drawer" aria-label="全局任务中心">
    <header><div><small>所有项目</small><h2>任务中心</h2></div><button aria-label="关闭任务中心" onClick={onClose}>×</button></header>
    <div className="global-task-list">
      {!tasks.length && <div className="global-task-empty">暂无任务</div>}
      {tasks.map(task => { const batchTitle=String(task.details?.batch_title||''); const batchItem=String(task.details?.batch_item_title||''); return <article key={task.id} className={task.status}>
        <button className="global-task-main" onClick={() => task.project_id && onOpenProject(task.project_id)}>
          <span><strong>{batchTitle ? `${batchTitle} · ${task.details?.batch_position}. ${batchItem}` : TASK_LABELS[task.type] || task.type}</strong><small>{batchTitle ? `${TASK_LABELS[task.type] || task.type} · ${task.message || task.step || '等待开始'}` : task.message || task.step || '等待开始'}</small></span><em>{Math.round(task.progress || 0)}%</em>
          <progress max={100} value={task.progress || 0}/>
        </button>
        {task.status === 'failed' && <section className="global-task-failure">
          <strong>{task.error_code || '任务失败'} · 第 {task.attempt || 1} 次尝试</strong>
          <span>{task.suggestion || task.details?.failure_suggestion || task.error || ''}</span>
        </section>}
        <div>{task.status === 'running' && <button onClick={() => void act(task.id, 'pause')}>暂停</button>}{task.status === 'paused' && <button onClick={() => void act(task.id, 'resume')}>继续</button>}{['pending', 'running', 'paused'].includes(task.status) && <button onClick={() => void act(task.id, 'cancel')}>取消</button>}{task.status === 'failed' && task.recoverable && task.project_id && <button onClick={() => onOpenProject(task.project_id)}>打开项目重试</button>}{task.available_actions?.includes('open_settings') && onOpenSettings && <button onClick={onOpenSettings}>打开设置</button>}</div>
      </article>; })}
    </div>
  </aside>;
}
