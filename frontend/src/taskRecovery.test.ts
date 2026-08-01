import { describe, expect, it } from 'vitest';
import type { TaskStatus } from './types';
import {
  isDownloadFailure,
  isTranscriptionFailure,
  recoveryActionLabel,
} from './taskRecovery';

function failedTask(type: string, errorCode: string): TaskStatus {
  return {
    id: 'task', project_id: 'project', type, status: 'failed',
    step: 'downloading', progress: 0, message: 'failed', error: 'failed',
    created_at: 'now', updated_at: 'now', error_code: errorCode,
    recoverable: true, available_actions: ['retry'],
  };
}

describe('task recovery routing', () => {
  it('routes download failures to download recovery without a model fallback', () => {
    for (const code of ['MEMBERSHIP_REQUIRED', 'MEDIA_ACCESS_DENIED', 'MERGE_FAILED']) {
      const task = failedTask('download', code);
      expect(isDownloadFailure(task)).toBe(true);
      expect(isTranscriptionFailure(task)).toBe(false);
      expect(recoveryActionLabel(task)).toBe('重新下载');
      expect(recoveryActionLabel(task)).not.toContain('备用模型');
    }
  });

  it('restarts an automatic workflow when its download stage fails', () => {
    const task = failedTask('workflow', 'NETWORK_TEMPORARY');
    expect(isDownloadFailure(task)).toBe(true);
    expect(recoveryActionLabel(task)).toBe('重新启动自动工作流');
  });

  it('offers a fallback model only for transcription failures', () => {
    const task = failedTask('transcribe', 'MODEL_RUNTIME_FAILED');
    expect(isTranscriptionFailure(task)).toBe(true);
    expect(recoveryActionLabel(task)).toBe('使用备用模型重试');
  });
});
