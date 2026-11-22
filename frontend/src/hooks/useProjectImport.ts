import {
  useCallback,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import * as api from '../api/backend';
import type { ImportSource } from '../components/ImportFlow';
import type { ProcessingConfig, Project, TaskStatus } from '../types';

interface UseProjectImportOptions {
  activeProjectIdRef: MutableRefObject<string | null>;
  config: ProcessingConfig;
  recommendedModel?: string;
  flowOptions: {
    enable_clean: boolean;
    enable_translate: boolean;
    target_language: string;
    clean_target_length: number;
    text_processing_consent: boolean;
  };
  setProjects: Dispatch<SetStateAction<Project[]>>;
  compatibleModel: () => string | null;
  runtimeForModel: (model: string) => string;
  requireRuntime: (model: string) => string;
  selectProject: (project: Project, requestedIntent?: number) => Promise<void>;
  attachTask: (status: TaskStatus, interval?: number) => void;
  showToast: (message: string, duration?: number) => void;
}

export function useProjectImport({
  activeProjectIdRef,
  config,
  recommendedModel,
  flowOptions,
  setProjects,
  compatibleModel,
  runtimeForModel,
  requireRuntime,
  selectProject,
  attachTask,
  showToast,
}: UseProjectImportOptions) {
  const [pendingImport, setPendingImport] = useState<ImportSource | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [quickImport, setQuickImport] = useState(
    () => localStorage.getItem('subtitle_factory_quick_import') || '',
  );

  const importActionLock = useRef(false);
  const preparedImports = useRef(
    new Map<File | string, { projectId: string; uploaded: boolean; started: boolean }>(),
  );

  const importSignature = JSON.stringify([
    config.model,
    config.language,
    runtimeForModel(config.model === 'auto' ? recommendedModel || '' : config.model),
    flowOptions,
  ]);

  const executeImport = useCallback(
    async (source: ImportSource, generate: boolean, remember = false) => {
      if (importActionLock.current) return;
      const model = generate ? compatibleModel() : config.model;
      if (!model) throw new Error('当前语言没有可用模型，请选择模型');
      const runtime = generate ? requireRuntime(model) : '';
      if (generate && !runtime) throw new Error('请选择可用的运行设备');
      importActionLock.current = true;
      setImportBusy(true);
      const items = source.kind === 'files' ? source.files : [source.url];
      try {
        for (const item of items) {
          const title = typeof item === 'string' ? `YouTube - ${item}` : item.name;
          let prepared = preparedImports.current.get(item);
          if (!prepared) {
            const created = await api.createProject({
              source_type: source.kind === 'files' ? 'local' : 'youtube',
              source_url: source.kind === 'link' ? source.url : undefined,
              title,
              language: config.language,
              target_language: config.target_language,
              media_mode: 'local',
            });
            prepared = { projectId: created.project_id, uploaded: false, started: false };
            preparedImports.current.set(item, prepared);
          }
          if (prepared.started) continue;
          let taskId: string | undefined;
          if (typeof item !== 'string' && !prepared.uploaded) {
            setUploadProgress(0);
            await api.importLocalVideo(prepared.projectId, item, {
              autostart: false,
              onProgress: setUploadProgress,
            });
            setUploadProgress(null);
            prepared.uploaded = true;
          }
          const project = await api.getProject(prepared.projectId);
          setProjects(current => [project, ...current.filter(entry => entry.id !== project.id)]);
          await selectProject(project);
          if (generate) {
            taskId = (
              await api.startWorkflow(project.id, {
                model,
                language: config.language,
                runtime,
                ...flowOptions,
                ...(source.kind === 'link' ? { source_url: source.url } : {}),
              })
            ).task_id;
          } else if (source.kind === 'link') {
            taskId = (await api.startDownload(project.id, source.url)).task_id;
          }
          prepared.started = true;
          if (taskId) {
            const status = await api.getTaskStatus(taskId);
            if (activeProjectIdRef.current === project.id) attachTask(status, 1000);
          }
        }
        if (remember && generate) {
          localStorage.setItem('subtitle_factory_quick_import', importSignature);
          setQuickImport(importSignature);
        }
        await api.saveAppSettings({ default_model: config.model, source_language: config.language });
        setPendingImport(null);
        preparedImports.current.clear();
      } finally {
        importActionLock.current = false;
        setImportBusy(false);
        setUploadProgress(null);
      }
    },
    [
      activeProjectIdRef,
      attachTask,
      compatibleModel,
      config.language,
      config.model,
      config.target_language,
      flowOptions,
      importSignature,
      requireRuntime,
      selectProject,
      setProjects,
    ],
  );

  const prepareImport = useCallback(
    (source: ImportSource) => {
      if (source.kind === 'files') {
        source = {
          ...source,
          files: source.files.filter(file => /\.(mp4|mkv|mov|webm|avi)$/i.test(file.name)),
        };
        if (!source.files.length) {
          showToast('请选择支持的视频文件');
          return;
        }
      }
      if (quickImport === importSignature) {
        void executeImport(source, true).catch(error => {
          setPendingImport(source);
          showToast(error.message);
        });
      } else {
        setPendingImport(source);
      }
    },
    [executeImport, importSignature, quickImport, showToast],
  );

  const importFiles = useCallback(
    (files: File[]) => prepareImport({ kind: 'files', files }),
    [prepareImport],
  );

  const handleImportLocal = useCallback(() => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = '.mp4,.mkv,.mov,.webm,.avi,video/*';
    input.hidden = true;
    input.onchange = () => {
      const files = Array.from(input.files || []);
      input.remove();
      importFiles(files);
    };
    input.oncancel = () => input.remove();
    document.body.appendChild(input);
    input.click();
  }, [importFiles]);

  const resetQuickImport = useCallback(() => {
    setQuickImport('');
    localStorage.removeItem('subtitle_factory_quick_import');
    showToast('下次导入视频时会重新确认转写配置');
  }, [showToast]);

  return {
    pendingImport,
    setPendingImport,
    importBusy,
    uploadProgress,
    quickImport,
    resetQuickImport,
    executeImport,
    prepareImport,
    importFiles,
    handleImportLocal,
  };
}
