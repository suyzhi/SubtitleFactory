import { useCallback, type Dispatch, type SetStateAction } from 'react';
import * as api from '../../api/backend';
import type { ProcessingConfig, Project, TaskStatus } from '../../types';

export type StartTaskFn = (
  name: string,
  stepId: string,
  fn: () => Promise<{ task_id: string }>,
  interval?: number,
) => Promise<void>;

interface UseTranscriptionActionsOptions {
  activeProject: Project | null;
  currentTask: TaskStatus | null;
  config: ProcessingConfig;
  flowOptions: {
    enable_clean: boolean;
    enable_translate: boolean;
    target_language: string;
    clean_target_length: number;
    text_processing_consent: boolean;
  };
  startTask: StartTaskFn;
  compatibleModel: () => string | null;
  runtimeForModel: (model: string) => string;
  requireRuntime: (model: string) => string;
  setSubtitleStats: Dispatch<SetStateAction<any>>;
  setSelectedStep: Dispatch<SetStateAction<string | null>>;
  setToolsTab: Dispatch<SetStateAction<'process' | 'quality' | 'smart' | 'content'>>;
  setToolsOpen: Dispatch<SetStateAction<boolean>>;
  setToast: Dispatch<SetStateAction<string>>;
}

export function useTranscriptionActions({
  activeProject,
  currentTask,
  config,
  flowOptions,
  startTask,
  compatibleModel,
  runtimeForModel,
  requireRuntime,
  setSubtitleStats,
  setSelectedStep,
  setToolsTab,
  setToolsOpen,
  setToast,
}: UseTranscriptionActionsOptions) {
  const doExtractAudio = useCallback(() => {
    if (!activeProject) return;
    void startTask('提取音频', 'extract_audio', () => api.startExtractAudio(activeProject.id));
  }, [activeProject, startTask]);

  const doTranscribe = useCallback(() => {
    if (!activeProject) return;
    const model = compatibleModel();
    if (!model) return;
    setSubtitleStats(null);
    const runtime = requireRuntime(model);
    if (!runtime) return;
    void startTask('转写', 'transcribe', () =>
      api.startTranscribe(activeProject.id, config.language, model, runtime),
    );
  }, [activeProject, compatibleModel, config.language, requireRuntime, setSubtitleStats, startTask]);

  const doGenerateSubtitles = useCallback(() => {
    if (!activeProject) return;
    const model = compatibleModel();
    if (!model) return;
    const runtime = requireRuntime(model);
    if (!runtime) return;
    setSubtitleStats(null);
    void startTask('自动生成字幕', 'transcribe', () =>
      api.startWorkflow(activeProject.id, {
        model,
        language: config.language,
        runtime,
        ...flowOptions,
      }),
    );
  }, [
    activeProject,
    compatibleModel,
    config.language,
    flowOptions,
    requireRuntime,
    setSubtitleStats,
    startTask,
  ]);

  const recoverTranscription = useCallback(
    async (preserveModel = false) => {
      if (!activeProject || !currentTask?.recoverable) return;
      try {
        const status = await api.getTranscriptionModels(activeProject.id, config.language);
        const failedModel = String(
          currentTask.details?.model_id ||
            currentTask.details?.model_resolution?.model_id ||
            currentTask.details?.resume_payload?.model ||
            currentTask.details?.resolved_model ||
            config.model,
        );
        if (preserveModel) {
          const selected = status.models.find(item => item.id === failedModel);
          if (!selected?.ready) {
            setSelectedStep('transcribe');
            setToolsTab('process');
            setToolsOpen(true);
            setToast('原转写模型当前未就绪，请检查模型与运行设备后重试');
            return;
          }
          const runtime = String(currentTask.details?.runtime || runtimeForModel(failedModel) || '');
          if (!runtime) {
            setSelectedStep('transcribe');
            setToolsTab('process');
            setToolsOpen(true);
            setToast('请重新选择转写运行设备后再重试');
            return;
          }
          if (
            !window.confirm(
              `上次转写在应用退出时被中断。原有已发布字幕未被覆盖。\n\n是否使用 ${selected.name} 从头重新转写？`,
            )
          ) {
            return;
          }
          await startTask('重新开始转写', 'transcribe', () =>
            api.retryTranscription(activeProject.id, {
              model: failedModel,
              language: String(currentTask.details?.language || config.language),
              runtime,
            }),
          );
          return;
        }
        const fallback =
          status.models.find(item => item.ready && item.id !== failedModel && item.id === 'small') ||
          status.models.find(item => item.ready && item.id !== failedModel);
        if (!fallback) {
          setToast('没有已就绪的备用模型，请打开转写参数选择模型');
          return;
        }
        const detail = fallback.download_required
          ? '该模型可能需要下载。'
          : '该模型已在本机就绪。';
        const runtime = requireRuntime(fallback.id);
        if (!runtime) return;
        if (
          !window.confirm(
            `当前转写失败：${currentTask.error || currentTask.message}\n\n是否改用 ${fallback.name} 重试？${detail}`,
          )
        ) {
          return;
        }
        await startTask('备用模型转写', 'transcribe', () =>
          api.retryTranscription(activeProject.id, {
            model: fallback.id,
            language: config.language,
            runtime,
          }),
        );
      } catch (error: any) {
        setToast(`无法启动恢复：${error.message}`);
      }
    },
    [
      activeProject,
      config.language,
      config.model,
      currentTask,
      requireRuntime,
      runtimeForModel,
      setSelectedStep,
      setToast,
      setToolsOpen,
      setToolsTab,
      startTask,
    ],
  );

  return {
    doExtractAudio,
    doTranscribe,
    doGenerateSubtitles,
    recoverTranscription,
  };
}
