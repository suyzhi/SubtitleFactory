import type { ProcessStep, Project, TaskStepStatus } from './types';

const ALL_STEPS: { id: string; name: string; description: string }[] = [
  { id: 'create', name: '创建项目', description: '建立项目记录' },
  { id: 'download', name: '下载/导入视频', description: '从 YouTube 下载或导入本地视频' },
  { id: 'extract_audio', name: '提取音频', description: '使用 ffmpeg 提取音频 (16kHz mono)' },
  { id: 'transcribe', name: '语音转写', description: '使用 Whisper 或 Parakeet Core ML 转写' },
  { id: 'clean', name: 'AI 整理', description: '所选 AI 模型修正错词、标点、断句' },
  { id: 'translate', name: 'AI 翻译', description: '所选 AI 模型翻译为目标语言' },
  { id: 'export', name: '导出字幕', description: '导出 SRT / VTT / ASS / 双语' },
  { id: 'render', name: '压制视频', description: 'ffmpeg 硬编码字幕到视频' },
];

export function emptyProcess(): ProcessStep[] {
  return ALL_STEPS.map(step => ({
    ...step,
    status: 'waiting' as TaskStepStatus,
    progress: 0,
  }));
}

export function deriveProcessSteps(project: Project | null): ProcessStep[] {
  const steps = emptyProcess();
  if (!project) return steps;
  steps[0].status = 'success';
  steps[0].progress = 100;
  if (project.video_available || project.video_path || (project.media_mode === 'web' && project.audio_available)) {
    steps[1].status = 'success';
    steps[1].progress = 100;
  }
  if (project.audio_path) {
    steps[2].status = 'success';
    steps[2].progress = 100;
  }
  if (project.segments_count > 0) {
    steps[3].status = 'success';
    steps[3].progress = 100;
  }
  return steps;
}
