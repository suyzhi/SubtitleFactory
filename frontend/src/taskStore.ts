import { useCallback, useState, useSyncExternalStore } from 'react';
import type { TaskStatus } from './types';
import { taskIsActive } from './projectState';

// Both project controls and the global drawer read these canonical snapshots.
// Request order prevents a slow, older poll from undoing a pause/cancel response.
let sequence = 0;
const tasks = new Map<string,{task:TaskStatus;sequence:number}>();
const listeners = new Set<()=>void>();
export const nextTaskRequest = () => ++sequence;
export function publishTask(task:TaskStatus, order=nextTaskRequest()): TaskStatus {
  const current=tasks.get(task.id);
  if (current && current.sequence>order) return current.task;
  const normalized={...task,details:task.details || {},logs:task.logs || [],suggestion:task.suggestion || null,step_name:task.step_name || task.step || task.type || ''};
  tasks.set(task.id,{task:normalized,sequence:order});
  listeners.forEach(listener => listener());
  return normalized;
}
const subscribe=(listener:()=>void) => {listeners.add(listener);return () => {listeners.delete(listener);};};
export function useTaskSnapshot(id:string|null) {
  return useSyncExternalStore(subscribe,() => id ? tasks.get(id)?.task || null : null);
}
export function useSelectedTask(): [TaskStatus|null,(task:TaskStatus|null)=>void] {
  const [id,setId]=useState<string|null>(null);
  const task=useTaskSnapshot(id);
  const select=useCallback((next:TaskStatus|null) => {if(next)publishTask(next);setId(next?.id || null);},[]);
  return [task,select];
}

export function useActiveTaskCount() {
  return useSyncExternalStore(subscribe, () => [...tasks.values()].filter(({task}) => taskIsActive(task)).length);
}
