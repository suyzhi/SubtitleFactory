import type {ReactNode} from 'react';
import type {TaskStatus,ProcessStep} from '../types';
import {taskProgressLabel} from '../projectState';
export default function TaskWorkspace({steps,selected,onSelect,task,settings,diagnostics,onPause,onCancel}: {
  steps:Array<{id:string;label:string;state:ProcessStep}>;selected:string;onSelect:(id:string)=>void;
  task:TaskStatus|null;settings:ReactNode;diagnostics:ReactNode;onPause:()=>void;onCancel:()=>void;
}) {
  const active=task && ['pending','running','paused'].includes(task.status);
  return <section className="task-workspace"><nav className="task-step-nav" aria-label="处理步骤">{steps.map((step,index)=><button key={step.id} aria-pressed={selected===step.id} onClick={()=>onSelect(step.id)}><strong>{step.state.status==='success' ? '✓' : index+1} {step.label}</strong><small>{step.state.status==='success' ? '已完成' : step.state.status==='failed' ? '需处理' : step.state.status==='running' ? '处理中' : step.state.status==='paused' ? '已暂停' : step.state.status==='cancelled' ? '已停止' : step.state.status==='partial' ? '部分完成' : ['clean','translate'].includes(step.id) ? '可选' : '待开始'}</small></button>)}</nav>{task && <div className="task-inline-status" role="status"><span>{taskProgressLabel(task)}</span>{active && <><button onClick={onPause}>{task.status==='paused' ? '继续' : '暂停'}</button><button onClick={onCancel}>停止</button></>}</div>}<div className="task-settings-body">{settings}</div>{diagnostics}</section>;
}
