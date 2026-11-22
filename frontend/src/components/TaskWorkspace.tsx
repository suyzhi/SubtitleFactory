import type {ReactNode} from 'react';
import type {TaskStatus,ProcessStep} from '../types';
import {taskProgressLabel} from '../projectState';

const STATUS_TEXT: Record<string, string> = {
  success: '已完成', failed: '需处理', running: '处理中', paused: '已暂停', cancelled: '已停止', partial: '部分完成',
};

export default function TaskWorkspace({steps,selected,onSelect,task,settings,diagnostics,onPause,onCancel}: {
  steps:Array<{id:string;label:string;state:ProcessStep}>;selected:string;onSelect:(id:string)=>void;
  task:TaskStatus|null;settings:ReactNode;diagnostics:ReactNode;onPause:()=>void;onCancel:()=>void;
}) {
  const active=task && ['pending','running','paused'].includes(task.status);
  const label=task ? taskProgressLabel(task) : '';
  return <section className="task-workspace">
    <ol className="task-stepper" aria-label="处理步骤">{steps.map((step,index)=>{
      const status=step.state.status;
      const hint=STATUS_TEXT[status] || (['clean','translate'].includes(step.id) ? '可选' : '待开始');
      return <li key={step.id} className={`step-${status}`}>
        <button type="button" aria-pressed={selected===step.id} aria-label={`${step.label}：${hint}`} onClick={()=>onSelect(step.id)}>
          <i aria-hidden="true">{status==='success' ? <svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7"/></svg> : status==='failed' ? '!' : status==='running' ? '' : index+1}</i>
          <strong>{step.label}</strong><small>{hint}</small>
        </button>
      </li>;
    })}</ol>
    {task && active && <div className={`task-live ${task.status}`} role="status">
      <div className="task-live-copy"><strong>{label}</strong>{!label.includes('%') && <small>{Math.round(task.progress || 0)}%</small>}</div>
      <progress max={100} value={task.progress || 0}/>
      <div className="task-live-actions"><button type="button" onClick={onPause}>{task.status==='paused' ? '继续' : '暂停'}</button><button type="button" className="danger" onClick={onCancel}>停止</button></div>
    </div>}
    {task && !active && <div className={`task-inline-status ${task.status}`} role="status"><span>{label}</span></div>}
    <div className="task-settings-body">{settings}</div>
    {diagnostics}
  </section>;
}
