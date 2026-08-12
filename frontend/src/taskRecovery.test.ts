import { describe, expect, it } from 'vitest';
import type { TaskStatus } from './types';
import {
  isDownloadFailure,
  isTranscriptionFailure,
  recoveryAction,
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
    const workflow = failedTask('workflow', 'MODEL_RUNTIME_FAILED');
    workflow.step = 'transcribing';
    expect(recoveryAction(workflow)).toBe('transcription');
    expect(recoveryActionLabel(workflow)).toBe('使用备用模型重试');
  });

  it('routes interrupted tasks back to their actual workspaces', () => {
    const cases: Array<[string, ReturnType<typeof recoveryAction>, string]> = [
      ['extract_audio', 'extract_audio', '重新提取音频'],
      ['clean', 'clean', '重新开始 AI 整理'],
      ['translate', 'translate', '重新开始翻译'],
      ['render', 'render', '重新导出成片'],
      ['ocr', 'smart_tools', '打开智能工具重新开始'],
      ['content_generate', 'content', '打开内容工作区重新开始'],
      ['prepare_model', 'settings', '打开设置检查并重试'],
    ];
    for (const [type, action, label] of cases) {
      const task = failedTask(type, 'APP_INTERRUPTED');
      expect(recoveryAction(task)).toBe(action);
      expect(recoveryActionLabel(task)).toBe(label);
      expect(recoveryActionLabel(task)).not.toContain('备用模型');
    }
  });

  it('restarts an interrupted transcription with its chosen model instead of forcing a fallback', () => {
    const task = failedTask('transcribe', 'APP_INTERRUPTED');
    expect(recoveryAction(task)).toBe('transcription');
    expect(recoveryActionLabel(task)).toBe('重新开始转写');
  });
});
