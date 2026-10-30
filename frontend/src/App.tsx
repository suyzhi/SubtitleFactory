import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from './api/backend';
import NewTask, { type JobOptions, type JobSource } from './components/NewTask';
import ProjectView, { type ClientPhase } from './components/ProjectView';
import SettingsDialog from './components/SettingsDialog';
import { defaultRuntime, errorMessage, isTaskActive, TASK_STATE_LABELS } from './format';
import type { AIProvidersResponse, ExportFormat, Project, SubtitleSegment, TaskStatus, TranscriptionModel } from './types';
import './styles/app.css';

// 发布脚本在源码、Vite 产物和最终 App 中检查这两个标记，确认打包的是简洁版界面。
const UI_MARKER = 'subtitle-factory-ui:simple-v1';
const UI_LAYOUT_MARKER = 'subtitle-factory-ui:single-page-v1';

const OPTIONS_KEY = 'subtitle_factory_simple_options';
const DEFAULT_OPTIONS: JobOptions = { model: '', runtime: '', language: 'auto', clean: true, translate: true, targetLanguage: 'zh' };

function loadOptions(): JobOptions {
  try {
    return { ...DEFAULT_OPTIONS, ...JSON.parse(localStorage.getItem(OPTIONS_KEY) || '{}') };
  } catch {
    return DEFAULT_OPTIONS;
  }
}

function saveOptions(options: JobOptions) {
  try { localStorage.setItem(OPTIONS_KEY, JSON.stringify(options)); } catch { /* 仅为便利，失败不影响使用 */ }
}

const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

async function waitForTask(taskId: string, onUpdate: (task: TaskStatus) => void): Promise<TaskStatus> {
  for (;;) {
    const task = await api.getTask(taskId);
    onUpdate(task);
    if (!isTaskActive(task)) return task;
    await sleep(1000);
  }
}

export default function App() {
  const [connected, setConnected] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [segments, setSegments] = useState<SubtitleSegment[]>([]);
  const [tasks, setTasks] = useState<Record<string, TaskStatus>>({});
  const [phases, setPhases] = useState<Record<string, ClientPhase>>({});
  const [models, setModels] = useState<TranscriptionModel[]>([]);
  const [options, setOptions] = useState<JobOptions>(loadOptions);
  const [ai, setAI] = useState<AIProvidersResponse | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [toast, setToast] = useState('');
  const selectedRef = useRef<string | null>(null);
  selectedRef.current = selectedId;

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(current => (current === message ? '' : current)), 4200);
  }, []);

  const setTask = useCallback((task: TaskStatus) => setTasks(current => ({ ...current, [task.project_id]: task })), []);
  const setPhase = useCallback((projectId: string, phase: ClientPhase | null) => setPhases(current => {
    const next = { ...current };
    if (phase) next[projectId] = phase; else delete next[projectId];
    return next;
  }), []);

  const refreshProjects = useCallback(async () => setProjects(await api.listProjects()), []);
  const refreshModels = useCallback(async () => {
    const result = await api.getTranscriptionModels('auto');
    setModels(result.models);
    setOptions(current => {
      const model = result.models.find(item => item.id === current.model) || result.models.find(item => item.id === result.recommended_model) || result.models[0];
      const runtimeValid = model?.runtimes?.some(item => item.id === current.runtime && item.available);
      return { ...current, model: model?.id || '', runtime: runtimeValid ? current.runtime : defaultRuntime(model) };
    });
  }, []);
  const refreshAI = useCallback(() => api.getAIProviders().then(setAI).catch(() => setAI(null)), []);
  const refreshSegments = useCallback(async (projectId: string) => {
    const result = await api.getSegments(projectId);
    if (selectedRef.current === projectId) setSegments(result);
  }, []);

  useEffect(() => saveOptions(options), [options]);

  // 等待本地服务就绪，然后载入项目、模型和 AI 设置。
  useEffect(() => {
    let cancelled = false;
    (async () => {
      while (!cancelled && !(await api.checkHealth())) await sleep(1500);
      if (cancelled) return;
      setConnected(true);
      await Promise.all([refreshProjects(), refreshModels(), refreshAI()]).catch(error => showToast(errorMessage(error)));
    })();
    return () => { cancelled = true; };
  }, [refreshAI, refreshModels, refreshProjects, showToast]);

  // 进入项目时读取字幕和最近一次任务。
  useEffect(() => {
    setSegments([]);
    if (!selectedId) return;
    void refreshSegments(selectedId).catch(error => showToast(errorMessage(error)));
    void api.getLatestProjectTask(selectedId).then(task => { if (task) setTask(task); }).catch(() => undefined);
  }, [refreshSegments, selectedId, setTask, showToast]);

  // 轮询进行中的任务；转写/整理/翻译过程中字幕会逐步刷新。
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;
  useEffect(() => {
    if (!connected) return;
    let tick = 0;
    const timer = window.setInterval(() => {
      tick += 1;
      for (const task of Object.values(tasksRef.current)) {
        if (!isTaskActive(task)) continue;
        void api.getTask(task.id).then(updated => {
          setTask(updated);
          const isSelected = selectedRef.current === updated.project_id;
          if (!isTaskActive(updated)) {
            void refreshProjects();
            if (isSelected) void refreshSegments(updated.project_id);
            if (updated.status === 'success') showToast('字幕已完成');
          } else if (isSelected && tick % 2 === 0) {
            void refreshSegments(updated.project_id);
          }
        }).catch(() => undefined);
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [connected, refreshProjects, refreshSegments, setTask, showToast]);

  const aiProvider = ai?.providers.find(item => item.provider_id === ai.assignments.translate_provider_id);
  const aiReady = Boolean(aiProvider && (aiProvider.has_api_key || aiProvider.provider_id === 'custom'));

  const requireAI = useCallback(() => {
    if (aiReady) return true;
    showToast('请先填写 AI 服务的 API Key');
    setSettingsOpen(true);
    return false;
  }, [aiReady, showToast]);

  const startJob = useCallback(async (source: JobSource) => {
    if ((options.clean || options.translate) && !requireAI()) return;
    const title = source.kind === 'file' ? source.file.name.replace(/\.[^.]+$/, '') : source.url;
    let projectId = '';
    try {
      const created = await api.createProject({
        source_type: source.kind === 'file' ? 'local' : 'youtube',
        source_url: source.kind === 'link' ? source.url : undefined,
        title, language: options.language, target_language: options.targetLanguage,
      });
      projectId = created.project_id;
      await refreshProjects();
      setSelectedId(projectId);

      if (source.kind === 'file') {
        setPhase(projectId, { label: '正在导入视频', progress: 0 });
        await api.uploadLocalVideo(projectId, source.file, percent => setPhase(projectId, { label: '正在导入视频', progress: percent }));
      }

      const model = models.find(item => item.id === options.model);
      const runtime = model?.runtimes?.find(item => item.id === options.runtime);
      if (runtime && !runtime.model_ready) {
        setPhase(projectId, { label: `正在下载 ${model?.name}`, progress: 0 });
        const prepared = await api.prepareTranscriptionModel(options.model, options.runtime);
        const result = await waitForTask(prepared.task_id, task => setPhase(projectId, { label: `正在下载 ${model?.name}`, progress: task.progress }));
        if (result.status !== 'success') throw new Error(result.error || result.message || '模型下载失败');
        void refreshModels();
      }

      setPhase(projectId, null);
      const started = await api.startWorkflow(projectId, {
        model: options.model, runtime: options.runtime, language: options.language,
        source_url: source.kind === 'link' ? source.url : undefined,
        enable_clean: options.clean, enable_translate: options.translate, target_language: options.targetLanguage,
      });
      setTask(await api.getTask(started.task_id));
    } catch (error) {
      if (projectId) setPhase(projectId, { label: '', progress: 0, error: errorMessage(error) });
      else showToast(errorMessage(error));
    }
  }, [models, options, refreshModels, refreshProjects, requireAI, setPhase, setTask, showToast]);

  const runStep = useCallback(async (projectId: string, step: () => Promise<{ task_id: string }>) => {
    if (!requireAI()) return;
    try {
      setPhase(projectId, null);
      const started = await step();
      setTask(await api.getTask(started.task_id));
    } catch (error) {
      showToast(errorMessage(error));
    }
  }, [requireAI, setPhase, setTask, showToast]);

  const exportFile = useCallback(async (project: Project, format: ExportFormat) => {
    const suffix = { original: '', translated: `.${project.target_language}`, bilingual: '.双语' }[format];
    try {
      const saved = await api.exportSubtitles(project.id, format, `${project.title}${suffix}.srt`);
      if (saved) showToast('字幕已导出');
    } catch (error) {
      showToast(errorMessage(error));
    }
  }, [showToast]);

  const removeProject = useCallback(async (project: Project) => {
    if (isTaskActive(tasks[project.id])) { showToast('请先停止正在进行的任务'); return; }
    if (!window.confirm(`删除“${project.title}”？视频和字幕文件会一起删除。`)) return;
    try {
      await api.deleteProject(project.id);
      if (selectedId === project.id) setSelectedId(null);
      await refreshProjects();
    } catch (error) {
      showToast(errorMessage(error));
    }
  }, [refreshProjects, selectedId, showToast, tasks]);

  const selected = projects.find(project => project.id === selectedId) || null;

  return (
    <div className="app" data-ui-version={UI_MARKER} data-ui-layout={UI_LAYOUT_MARKER}>
      <aside className="sidebar">
        <div className="titlebar" data-tauri-drag-region />
        <button type="button" className={`new-button${selectedId ? '' : ' active'}`} onClick={() => setSelectedId(null)}>＋ 新建字幕</button>
        <nav className="history" aria-label="历史记录">
          {projects.length === 0 && connected && <p className="muted small">处理过的视频会出现在这里</p>}
          {projects.map(project => {
            const task = tasks[project.id];
            const status = phases[project.id]?.error ? '失败'
              : phases[project.id] || isTaskActive(task) ? '处理中…'
              : project.segments_count ? `${project.segments_count} 条字幕`
              : TASK_STATE_LABELS[task?.status || project.latest_task_status || ''] || '未完成';
            return (
              <div key={project.id} className={`history-item${project.id === selectedId ? ' active' : ''}`}>
                <button type="button" onClick={() => setSelectedId(project.id)}>
                  <span className="history-title">{project.title}</span>
                  <small>{status}</small>
                </button>
                <button type="button" className="delete" aria-label={`删除 ${project.title}`} onClick={() => void removeProject(project)}>×</button>
              </div>
            );
          })}
        </nav>
        <button type="button" className="settings-button" onClick={() => setSettingsOpen(true)}>
          <span aria-hidden>⚙</span> AI 服务{aiReady ? '' : <em className="dot" aria-label="未配置" />}
        </button>
      </aside>

      <main className="main">
        <div className="titlebar" data-tauri-drag-region />
        {!connected && <div className="banner">正在启动本地服务…</div>}
        <div className="content">
          {selected ? (
            <ProjectView
              key={selected.id}
              project={selected}
              segments={segments}
              task={tasks[selected.id] || null}
              phase={phases[selected.id] || null}
              onClean={() => void runStep(selected.id, () => api.startClean(selected.id))}
              onTranslate={target => void runStep(selected.id, () => api.startTranslate(selected.id, target))}
              onCancel={() => { const task = tasks[selected.id]; if (task) void api.cancelTask(task.id).then(setTask).catch(error => showToast(errorMessage(error))); }}
              onExport={format => void exportFile(selected, format)}
            />
          ) : (
            <NewTask
              models={models}
              options={options}
              onOptionsChange={setOptions}
              youtubeEnabled={api.youtubeFeaturesEnabled()}
              disabled={!connected}
              onStart={startJob}
            />
          )}
        </div>
      </main>

      {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} onSaved={() => { void refreshAI(); showToast('AI 服务已保存'); }} />}
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
