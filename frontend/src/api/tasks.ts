// 任务查询、控制与失败批次重试 API

import { nextTaskRequest, publishTask } from '../taskStore';
import type { FailedCleanBatch, TaskStatus } from '../types';
import { request } from './client';

const pendingTaskReads = new Map<string, Promise<TaskStatus>>();

export async function getTaskStatus(taskId: string): Promise<TaskStatus> {
  const pending = pendingTaskReads.get(taskId);
  if (pending) return pending;
  const order = nextTaskRequest();
  const read = request<TaskStatus>(`/api/tasks/${taskId}`)
    .then(task => publishTask(task, order))
    .finally(() => pendingTaskReads.delete(taskId));
  pendingTaskReads.set(taskId, read);
  return read;
}

export async function getGlobalTasks(limit = 100): Promise<{ tasks: TaskStatus[] }> {
  const order = nextTaskRequest();
  const result = await request<{ tasks: TaskStatus[] }>(`/api/tasks?limit=${limit}`);
  return { tasks: result.tasks.map(task => publishTask(task, order)) };
}

export async function getLatestProjectTask(projectId: string): Promise<TaskStatus | null> {
  const order = nextTaskRequest();
  const data = await request<{ task: TaskStatus | null }>(
    `/api/projects/${projectId}/tasks/latest`,
  );
  return data.task ? publishTask(data.task, order) : null;
}

export async function pauseTask(taskId: string): Promise<TaskStatus> {
  const task = await request<TaskStatus>(`/api/tasks/${taskId}/pause`, { method: 'POST' });
  return publishTask(task);
}

export async function resumeTask(taskId: string): Promise<TaskStatus> {
  const task = await request<TaskStatus>(`/api/tasks/${taskId}/resume`, { method: 'POST' });
  return publishTask(task);
}

export async function cancelTask(taskId: string): Promise<TaskStatus> {
  const task = await request<TaskStatus>(`/api/tasks/${taskId}/cancel`, { method: 'POST' });
  return publishTask(task);
}

export async function getFailedCleanBatches(
  taskId: string,
): Promise<{ task_id: string; batches: FailedCleanBatch[] }> {
  return request(`/api/tasks/${taskId}/failed-batches`);
}

export async function retryFailedCleanBatch(
  taskId: string,
  batchIndex: number,
): Promise<{ task_id: string; retry_of: string; batch_index: number }> {
  return request(`/api/tasks/${taskId}/retry-failed-batches/${batchIndex}`, { method: 'POST' });
}
