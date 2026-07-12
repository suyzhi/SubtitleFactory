// 字幕工厂 - 主应用组件（集成字幕播放器 + 流程可视化）

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import type {
  Project, SubtitleSegment, TaskStatus, ProcessingConfig,
  ModelSize, ExportFormat,
  ProcessStep, ProcessLogEntry, TaskStepStatus,
  SubtitleStyleSettings, SubtitleStats, AISettings, AIProviderPreset,
  HealthStatus, AppSettings,
} from './types';
import * as api from './api/backend';
import './App.css';

import SubtitlePlayer, { type SubtitlePlayerHandle } from './components/SubtitlePlayer';
import { loadSubtitleStyle, saveSubtitleStyle } from './subtitleStyle';
import ProcessTimeline from './components/ProcessTimeline';
import ProcessLogViewer from './components/ProcessLogViewer';
import SubtitleStatsPanel from './components/SubtitleStatsPanel';
import SubtitleTimeline from './components/SubtitleTimeline';
import SubtitleStylePanel from './components/SubtitleStylePanel';
import SettingsCenter from './components/SettingsCenter';
import LanguagePicker from './components/LanguagePicker';
import { languageLabel } from './languages';
import appIcon from './assets/branding/app-icon-ui.png';
import settingsIcon from './assets/player-icons/settings.png';

const DEFAULT_CONFIG: ProcessingConfig = {
  model: 'auto', language: 'auto', target_language: 'zh',
  enable_clean: true, enable_translate: true, bilingual: false, clean_target_length: 42,
};

// ── 流程步骤定义 ──
const ALL_STEPS: { id: string; name: string; description: string }[] = [
  { id: 'create',       name: '创建项目',        description: '建立项目记录' },
  { id: 'download',     name: '下载/导入视频',   description: '从 YouTube 下载或导入本地视频' },
  { id: 'extract_audio', name: '提取音频',       description: '使用 ffmpeg 提取音频 (16kHz mono)' },
  { id: 'transcribe',   name: '语音转写',        description: '使用 Whisper 或 Parakeet Core ML 转写' },
  { id: 'clean',        name: 'AI 整理',         description: '所选 AI 模型修正错词、标点、断句' },
  { id: 'translate',    name: 'AI 翻译',         description: '所选 AI 模型翻译为目标语言' },
  { id: 'export',       name: '导出字幕',        description: '导出 SRT / VTT / ASS / 双语' },
  { id: 'render',       name: '压制视频',        description: 'ffmpeg 硬编码字幕到视频' },
];

function emptyProcess(): ProcessStep[] {
  return ALL_STEPS.map(s => ({
    ...s,
    status: 'waiting' as TaskStepStatus,
    progress: 0,
  }));
}

// ── 步骤 ID 到任务类型的映射 ──
const STEP_TASK_MAP: Record<string, string> = {
  download: 'download',
  extract_audio: 'extract_audio',
  transcribe: 'transcribe',
  clean: 'clean',
  translate: 'translate',
  export: 'export',
  render: 'render',
};

function App() {
  // ── Core State ──
  const [projects, setProjects] = useState<Project[]>([]);
  const [trashProjects, setTrashProjects] = useState<Project[]>([]);
  const [activeProject, setActiveProject] = useState<Project | null>(null);
  const [segments, setSegments] = useState<SubtitleSegment[]>([]);
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [config, setConfig] = useState<ProcessingConfig>(() => ({
    ...DEFAULT_CONFIG,
    clean_target_length: Number(localStorage.getItem('subtitle_factory_clean_target_length')) || DEFAULT_CONFIG.clean_target_length,
  }));
  const [subtitleStyle, setSubtitleStyle] = useState<SubtitleStyleSettings>(loadSubtitleStyle);

  // ── Task State ──
  const [currentTask, setCurrentTask] = useState<TaskStatus | null>(null);
  const [pollInterval, setPollInterval] = useState<number | null>(null);
  const [processLogs, setProcessLogs] = useState<ProcessLogEntry[]>([]);
  const [processSteps, setProcessSteps] = useState<ProcessStep[]>(emptyProcess);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [subtitleStats, setSubtitleStats] = useState<SubtitleStats | null>(null);

  // ── UI State ──
  const [currentTime, setCurrentTime] = useState(0);
  const [videoDuration, setVideoDuration] = useState(0);
  const [activeSegmentIdx, setActiveSegmentIdx] = useState(-1);
  const [autoScrollTable, setAutoScrollTable] = useState(true);
  const [showAISettings, setShowAISettings] = useState(false);
  const [backendStatus, setBackendStatus] = useState<'connecting' | 'connected' | 'error'>('connecting');
  const [aiSettings, setAISettings] = useState<AISettings | null>(null);
  const [aiPresets, setAIPresets] = useState<AIProviderPreset[]>([]);
  const [toast, setToast] = useState('');
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [taskStarting, setTaskStarting] = useState(false);
  const [modelStatus, setModelStatus] = useState<Awaited<ReturnType<typeof api.getTranscriptionModels>> | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [appSettings, setAppSettings] = useState<AppSettings>({
    default_workflow: 'automatic', auto_save: true, startup_behavior: 'restore_last',
  });
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    const stored = localStorage.getItem('subtitle_factory_theme');
    if (stored === 'light' || stored === 'dark') return stored;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });
  const [motionEnabled, setMotionEnabled] = useState(() => localStorage.getItem('subtitle_factory_motion') !== 'off');
  const [density, setDensity] = useState<'comfortable' | 'compact'>(() => localStorage.getItem('subtitle_factory_density') === 'compact' ? 'compact' : 'comfortable');
  const [libraryView, setLibraryView] = useState<'projects' | 'trash'>('projects');
  const [bottomTab, setBottomTab] = useState<'subtitles' | 'style' | 'export' | 'logs'>('subtitles');
  const [inspectorMode, setInspectorMode] = useState<'style' | 'step' | null>(null);
  const [showLinkPopover, setShowLinkPopover] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; project: Project; trashed: boolean } | null>(null);
  const [renameProjectState, setRenameProjectState] = useState<Project | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [removingProjectIds, setRemovingProjectIds] = useState<Set<string>>(() => new Set());
  const [theaterMode, setTheaterMode] = useState(false);
  const [leftPanelWidth, setLeftPanelWidth] = useState(() => Number(localStorage.getItem('subtitle_factory_left_width')) || 258);
  const [rightPanelWidth, setRightPanelWidth] = useState(() => Number(localStorage.getItem('subtitle_factory_right_width')) || 336);
  const [viewerHeight, setViewerHeight] = useState(() => Number(localStorage.getItem('subtitle_factory_viewer_height')) || 470);
  const [collapsedProjectGroups, setCollapsedProjectGroups] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem('subtitle_factory_collapsed_groups') || '[]')); }
    catch { return new Set(); }
  });
  const [groupEditorProjectId, setGroupEditorProjectId] = useState<string | null>(null);
  const [groupDraft, setGroupDraft] = useState('');

  const logIdCounter = useRef(0);
  const lastTaskMessage = useRef('');
  const lastTaskBatch = useRef('');
  const lastTaskStep = useRef('');
  const lastTaskProgressBucket = useRef(-1);
  const backendLogTaskId = useRef('');
  const lastBackendLogCount = useRef(0);
  const videoPlayerRef = useRef<SubtitlePlayerHandle>(null);
  const contextMenuRef = useRef<HTMLDivElement>(null);
  const contextMenuReturnFocus = useRef<HTMLElement | null>(null);
  const downloadedRenderTask = useRef('');
  const restoredStartupProject = useRef(false);
  const taskStartLock = useRef(false);
  const sourceActionLock = useRef(false);
  const importActionLock = useRef(false);
  const exportActionLock = useRef(false);

  useEffect(() => {
    localStorage.setItem('subtitle_factory_theme', theme);
    document.documentElement.style.colorScheme = theme;
    document.documentElement.dataset.theme = theme;
    if ((window as any).__TAURI_INTERNALS__) {
      void getCurrentWindow().setTheme(theme).catch(() => undefined);
    }
  }, [theme]);

  useEffect(() => {
    localStorage.setItem('subtitle_factory_motion', motionEnabled ? 'on' : 'off');
    localStorage.setItem('subtitle_factory_density', density);
  }, [density, motionEnabled]);

  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      close();
      window.requestAnimationFrame(() => contextMenuReturnFocus.current?.focus());
    };
    const menuElement = contextMenuRef.current;
    const frame = window.requestAnimationFrame(() => contextMenuRef.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus());
    window.addEventListener('pointerdown', close);
    window.addEventListener('keydown', escape);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('pointerdown', close);
      window.removeEventListener('keydown', escape);
      window.removeEventListener('resize', close);
      window.cancelAnimationFrame(frame);
      if (menuElement?.contains(document.activeElement)) window.requestAnimationFrame(() => contextMenuReturnFocus.current?.focus());
    };
  }, [contextMenu]);

  useEffect(() => {
    localStorage.setItem('subtitle_factory_clean_target_length', String(config.clean_target_length));
  }, [config.clean_target_length]);

  useEffect(() => {
    localStorage.setItem('subtitle_factory_left_width', String(leftPanelWidth));
    localStorage.setItem('subtitle_factory_right_width', String(rightPanelWidth));
    localStorage.setItem('subtitle_factory_viewer_height', String(viewerHeight));
  }, [leftPanelWidth, rightPanelWidth, viewerHeight]);

  useEffect(() => {
    localStorage.setItem('subtitle_factory_collapsed_groups', JSON.stringify([...collapsedProjectGroups]));
  }, [collapsedProjectGroups]);

  useEffect(() => {
    const handleTheaterShortcut = (event: KeyboardEvent) => {
      if (event.repeat || event.metaKey || event.ctrlKey || event.altKey) return;

      const target = event.target;
      const isEditing = target instanceof HTMLElement && (
        target.isContentEditable || target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' || target.tagName === 'SELECT'
      );

      if (event.key.toLowerCase() === 't' && !isEditing && activeProject?.video_path) {
        event.preventDefault();
        setTheaterMode(enabled => !enabled);
        return;
      }

      // The browser owns Escape while its Fullscreen API is active. Once browser
      // fullscreen has closed, Escape can independently leave theater mode.
      if (event.key === 'Escape' && theaterMode && !document.fullscreenElement && !event.defaultPrevented) {
        setTheaterMode(false);
        return;
      }

      if (event.key === 'Escape' && !document.fullscreenElement && !event.defaultPrevented) {
        setInspectorMode(null);
        setShowLinkPopover(false);
      }
    };

    window.addEventListener('keydown', handleTheaterShortcut);
    return () => window.removeEventListener('keydown', handleTheaterShortcut);
  }, [activeProject?.video_path, theaterMode]);

  const beginResize = useCallback((kind: 'left' | 'right' | 'viewer', event: React.PointerEvent) => {
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const initialLeft = leftPanelWidth;
    const initialRight = rightPanelWidth;
    const initialViewer = viewerHeight;
    document.body.classList.add('is-resizing');
    const onMove = (move: PointerEvent) => {
      if (kind === 'left') setLeftPanelWidth(Math.max(210, Math.min(430, initialLeft + move.clientX - startX)));
      if (kind === 'right') setRightPanelWidth(Math.max(280, Math.min(480, initialRight - (move.clientX - startX))));
      if (kind === 'viewer') setViewerHeight(Math.max(260, Math.min(window.innerHeight - 250, initialViewer + move.clientY - startY)));
    };
    const onUp = () => {
      document.body.classList.remove('is-resizing');
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
  }, [leftPanelWidth, rightPanelWidth, viewerHeight]);

  // ── Add log ──
  const addLog = useCallback((level: 'info' | 'warning' | 'error', step: string, message: string, detail?: string, suggestion?: string) => {
    const entry: ProcessLogEntry = {
      id: String(++logIdCounter.current),
      time: new Date().toLocaleTimeString(),
      level, step, message, detail, suggestion,
    };
    setProcessLogs(prev => [...prev.slice(-199), entry]);
  }, []);

  const refreshModels = useCallback(() => {
    if (backendStatus !== 'connected') return;
    api.getTranscriptionModels(activeProject?.id, config.language).then(setModelStatus).catch(() => setModelStatus(null));
  }, [activeProject?.id, backendStatus, config.language]);

  const refreshHealth = useCallback(() => {
    api.checkHealth().then(setHealth).catch(() => undefined);
  }, []);

  const refreshLibraries = useCallback(async () => {
    const [active, deleted] = await Promise.all([
      api.listProjects(), api.listProjects({ deleted: true }).catch(() => ({ projects: [] as Project[] })),
    ]);
    setProjects(active.projects);
    setTrashProjects(deleted.projects);
  }, []);

  useEffect(refreshModels, [refreshModels]);

  // 后端任务日志通常包含更具体的 detail / suggestion。按任务和游标增量
  // 合并，避免每次轮询重复写入，也避免为了展示日志而增加轮询噪声。
  const ingestTaskLogs = useCallback((task: TaskStatus) => {
    if (backendLogTaskId.current !== task.id) {
      backendLogTaskId.current = task.id;
      lastBackendLogCount.current = 0;
    }
    const logs = task.logs || [];
    if (logs.length < lastBackendLogCount.current) lastBackendLogCount.current = 0;
    const start = lastBackendLogCount.current;
    const fresh = logs.slice(start);
    lastBackendLogCount.current = logs.length;
    if (!fresh.length) return;
    const entries: ProcessLogEntry[] = fresh.map((log, offset) => ({
      id: `backend:${task.id}:${start + offset}`,
      time: log.time?.slice(-8) || new Date().toLocaleTimeString(),
      level: log.level,
      step: log.step || task.type,
      message: log.message,
      detail: log.detail || undefined,
      suggestion: log.suggestion || undefined,
    }));
    setProcessLogs(previous => [...previous, ...entries].slice(-200));
  }, []);

  // ── Update process step status ──
  const setStepStatus = useCallback((stepId: string, status: TaskStepStatus, progress = 0, error?: string, suggestion?: string) => {
    setProcessSteps(prev => prev.map(s =>
      s.id === stepId ? { ...s, status, progress, error, suggestion } : s
    ));
  }, []);

  // ── Sync process steps from backend task ──
  const syncProcessFromTask = useCallback((task: TaskStatus) => {
    if (task.type === 'workflow' && task.details?.stages) {
      const stageMap = task.details.stages as Record<string, TaskStepStatus>;
      for (const [stepId, status] of Object.entries(stageMap)) {
        setStepStatus(stepId, status, status === 'success' ? 100 : status === 'running' ? task.progress : 0);
      }
      return;
    }
    // Map backend task type to step
    for (const [stepId, taskType] of Object.entries(STEP_TASK_MAP)) {
      if (taskType === task.type) {
        const taskStatusMap: Record<string, TaskStepStatus> = {
          pending: 'waiting',
          running: 'running',
          paused: 'paused',
          success: 'success',
          failed: 'failed',
          cancelled: 'cancelled',
          partial: 'partial',
        };
        const st = taskStatusMap[task.status] || 'waiting';
        setStepStatus(stepId, st, task.progress, task.error || undefined, task.suggestion || undefined);
      }
    }
  }, [setStepStatus]);

  // ── Rebuild steps from scratch based on project state ──
  const refreshProcessSteps = useCallback((proj: Project | null) => {
    const steps = emptyProcess();
    if (proj) {
      steps[0].status = 'success';      // create
      steps[0].progress = 100;
      if (proj.video_path) {
        steps[1].status = 'success';    // download/import
        steps[1].progress = 100;
      }
      if (proj.audio_path) {
        steps[2].status = 'success';    // extract audio
        steps[2].progress = 100;
      }
    }
    setProcessSteps(steps);
  }, []);

  // ── Poll task status ──
  useEffect(() => {
    if (!pollInterval || !currentTask) return;
    const id = window.setInterval(async () => {
      try {
        const status = await api.getTaskStatus(currentTask.id);
        setCurrentTask(status);
        syncProcessFromTask(status);
        ingestTaskLogs(status);
        if (status.message && status.message !== lastTaskMessage.current) {
          const progressBucket = Math.floor(Math.max(0, status.progress) / 10);
          const stepChanged = status.step !== lastTaskStep.current;
          const terminal = ['success', 'failed', 'cancelled', 'partial', 'paused'].includes(status.status);
          const hasBatchProgress = status.details?.current_batch !== undefined;
          lastTaskMessage.current = status.message;
          lastTaskStep.current = status.step;
          if (terminal || stepChanged || (!hasBatchProgress && progressBucket > lastTaskProgressBucket.current)) {
            lastTaskProgressBucket.current = progressBucket;
            addLog('info', status.type, status.message || status.step);
          }
        }

        if (status.type === 'transcribe' && (status.status === 'running' || status.status === 'paused') && activeProject) {
          api.getSegments(activeProject.id).then(result => setSegments(result.segments)).catch(() => {});
        }

        // Update batch progress details
        if (status.details) {
          const d = status.details;
          if (d.current_batch !== undefined && d.total_batches !== undefined) {
            const batchKey = `${status.id}:${d.current_batch}/${d.total_batches}`;
            if (batchKey !== lastTaskBatch.current) {
              lastTaskBatch.current = batchKey;
              addLog('info', status.type, `正在处理批次 ${d.current_batch}/${d.total_batches}`);
            }
          }
          // Subtitle stats after transcribe
          if ((status.type === 'transcribe' || status.type === 'workflow') && status.status === 'success' && d.total_segments) {
            setSubtitleStats({
              totalSegments: d.total_segments,
              audioDuration: d.audio_duration,
              averageDuration: d.avg_duration,
              minDuration: d.min_duration,
              maxDuration: d.max_duration,
              mergedShortSegments: d.merged_short,
              splitLongSegments: d.split_long,
              tooShortCount: d.too_short_count,
              tooLongCount: d.too_long_count,
            });
          }
          // Export result
          if ((status.type === 'export' || status.type === 'render') && status.status === 'success' && d.output_path) {
            addLog('info', status.type, `输出文件: ${d.output_path}${d.output_size ? ` (${(d.output_size/1024/1024).toFixed(1)}MB)` : ''}`);
            if (status.type === 'render' && downloadedRenderTask.current !== status.id) {
              downloadedRenderTask.current = status.id;
              window.open(api.getExportDownloadUrl(activeProject?.id || status.project_id || '', d.format || 'mp4'), '_blank');
            }
          }
        }

        if (status.status === 'success' || status.status === 'failed' || status.status === 'cancelled' || status.status === 'partial') {
          setPollInterval(null);
          if (activeProject) {
            api.getSegments(activeProject.id)
              .then(result => setSegments(result.segments))
              .catch(() => {});
            api.getProject(activeProject.id).then(setActiveProject).catch(() => {});
            api.listProjects().then(result => setProjects(result.projects)).catch(() => {});
          }
        }
      } catch (e: any) {
        addLog('error', '系统', `状态查询失败: ${e.message}`);
        setPollInterval(null);
      }
    }, pollInterval);
    return () => clearInterval(id);
  }, [pollInterval, currentTask, activeProject, addLog, ingestTaskLogs, syncProcessFromTask]);

  // ── Wait for the bundled backend, then load projects ──
  useEffect(() => {
    let stopped = false;
    let attempts = 0;
    const connect = async () => {
      try {
        const healthResult = await api.checkHealth();
        if (stopped) return;
        setHealth(healthResult);
        setBackendStatus('connected');
        const [data, deleted, ai, app] = await Promise.all([
          api.listProjects(),
          api.listProjects({ deleted: true }).catch(() => ({ projects: [] as Project[] })),
          api.getAISettings(),
          api.getAppSettings().catch(() => ({ settings: {} as AppSettings, warnings: [] })),
        ]);
        if (!stopped) {
          setProjects(data.projects);
          setTrashProjects(deleted.projects);
          setAISettings(ai.settings);
          setAIPresets(ai.presets);
          setAppSettings(app.settings);
          setConfig(current => ({
            ...current,
            model: String(app.settings.default_model || current.model),
            language: String(app.settings.source_language || current.language),
            target_language: String(app.settings.translation_target_language || current.target_language),
          }));
        }
      } catch {
        if (stopped) return;
        attempts += 1;
        // 首次启动的内置转写运行时需要加载较多原生库。在真正超时前保持
        // “正在启动”，避免把正常的冷启动误报成连接失败；超时后仍持续重试。
        setBackendStatus(attempts < 80 ? 'connecting' : 'error');
        window.setTimeout(connect, attempts < 80 ? 750 : 5000);
      }
    };
    connect();
    return () => { stopped = true; };
  }, []);

  // ── Refresh segments ──
  const refreshSegments = useCallback(async (projectId: string) => {
    try {
      const r = await api.getSegments(projectId);
      setSegments(r.segments);
    } catch { }
  }, []);

  // ── Select project ──
  const selectProject = useCallback(async (p: Project) => {
    setActiveProject(p);
    localStorage.setItem('subtitle_factory_last_project_id', p.id);
    lastTaskMessage.current = '';
    lastTaskBatch.current = '';
    lastTaskStep.current = '';
    lastTaskProgressBucket.current = -1;
    backendLogTaskId.current = '';
    lastBackendLogCount.current = 0;
    setCurrentTask(null);
    setPollInterval(null);
    setActiveSegmentIdx(-1);
    setCurrentTime(0);
    setVideoDuration(0);
    setSubtitleStats(null);
    setSelectedStep(null);
    refreshProcessSteps(p);
    await refreshSegments(p.id);
    try {
      const latestTask = await api.getLatestProjectTask(p.id);
      if (latestTask) {
        setCurrentTask(latestTask);
        syncProcessFromTask(latestTask);
        ingestTaskLogs(latestTask);
        if (['pending', 'running', 'paused'].includes(latestTask.status)) setPollInterval(1000);
      }
    } catch { /* projects created by older builds may not have task history */ }
    addLog('info', '项目', `打开项目: ${p.title}`);
  }, [refreshSegments, addLog, ingestTaskLogs, refreshProcessSteps, syncProcessFromTask]);

  useEffect(() => {
    if (restoredStartupProject.current || backendStatus !== 'connected' || !projects.length) return;
    if (activeProject) { restoredStartupProject.current = true; return; }
    restoredStartupProject.current = true;
    if (appSettings.startup_behavior !== 'restore_last') return;
    const lastId = localStorage.getItem('subtitle_factory_last_project_id');
    const project = projects.find(item => item.id === lastId);
    if (project) void selectProject(project);
  }, [activeProject, appSettings.startup_behavior, backendStatus, projects, selectProject]);

  // ── Start background task ──
  const startTask = useCallback(async (
    name: string, stepId: string,
    fn: () => Promise<{ task_id: string }>,
    interval: number = 1000
  ) => {
    if (taskStartLock.current) return;
    taskStartLock.current = true;
    setTaskStarting(true);
    setStepStatus(stepId, 'running', 0);
    try {
      const { task_id } = await fn();
      lastTaskMessage.current = '';
      lastTaskBatch.current = '';
      lastTaskStep.current = '';
      lastTaskProgressBucket.current = -1;
      backendLogTaskId.current = task_id;
      lastBackendLogCount.current = 0;
      addLog('info', name, `${name} 任务已创建`);
      const status = await api.getTaskStatus(task_id);
      setCurrentTask(status);
      syncProcessFromTask(status);
      ingestTaskLogs(status);
      setPollInterval(interval);
    } catch (e: any) {
      addLog('error', name, `${name} 失败: ${e.message}`);
      setStepStatus(stepId, 'failed', 0, e.message);
      const suggestionMap: Record<string, string> = {
        '下载视频': '检查网络，更新 yt-dlp，或改用本地视频导入',
        '提取音频': '检查 ffmpeg 是否已安装',
        '转写': '检查模型是否已下载，网络是否正常',
        'AI 整理': '检查 API Key、余额、网络连接',
        'AI 翻译': '检查 API Key、余额、网络连接',
        '导出': '检查 ffmpeg 和输出目录权限',
      };
      addLog('error', name, `建议: ${suggestionMap[name] || '请查看详细日志'}`, undefined, suggestionMap[name]);
    } finally {
      taskStartLock.current = false;
      setTaskStarting(false);
    }
  }, [addLog, ingestTaskLogs, setStepStatus, syncProcessFromTask]);

  const compatibleModel = useCallback((): string | null => {
    if (!config.model.startsWith('parakeet-') || config.language === 'auto') return config.model;
    const model = modelStatus?.models.find(item => item.id === config.model);
    const supported = model?.languages || ['en'];
    if (supported.includes('*') || supported.includes(config.language)) return config.model;
    const switchModel = window.confirm(
      `所选 Parakeet 模型不支持${languageLabel(config.language)}。\n\n是否切换到 Whisper Small 后继续？`,
    );
    if (!switchModel) return null;
    setConfig(current => ({ ...current, model: 'small' }));
    setToast('已切换到 Whisper Small');
    window.setTimeout(() => setToast(''), 2600);
    return 'small';
  }, [config.language, config.model, modelStatus?.models]);

  // ── Create project ──
  const handleCreateProject = useCallback(async () => {
    setStepStatus('create', 'running', 50);
    try {
      const r = await api.createProject({
        source_type: youtubeUrl ? 'youtube' : 'local',
        source_url: youtubeUrl || undefined,
        title: youtubeUrl ? `YouTube - ${youtubeUrl.slice(0, 50)}` : '新项目',
        language: config.language,
        target_language: config.target_language,
      });
      addLog('info', '创建项目', `项目已创建: ${r.project_id.slice(0, 8)}`);
      setStepStatus('create', 'success', 100);
      const d = await api.listProjects();
      setProjects(d.projects);
      const newProj = d.projects.find(p => p.id === r.project_id);
      if (newProj) await selectProject(newProj);
      return r.project_id;
    } catch (e: any) {
      addLog('error', '创建项目', `创建失败: ${e.message}`);
      setStepStatus('create', 'failed', 0, e.message);
    }
  }, [youtubeUrl, config, addLog, selectProject, setStepStatus]);

  // ── Full pipeline ──
  const handleFullPipeline = useCallback(async () => {
    if (sourceActionLock.current) return;
    sourceActionLock.current = true;
    setTaskStarting(true);
    const model = appSettings.default_workflow === 'manual' ? config.model : compatibleModel();
    try {
      if (!model) return;
      const pid = await handleCreateProject();
      if (!pid) return;
      if (youtubeUrl) {
        if (appSettings.default_workflow === 'manual') {
          await startTask('下载视频', 'download', () => api.startDownload(pid, youtubeUrl));
        } else {
          await startTask('自动生成字幕', 'download', () => api.startWorkflow(pid, {
            source_url: youtubeUrl, model, language: config.language,
          }));
        }
      }
    } finally {
      sourceActionLock.current = false;
      setTaskStarting(false);
    }
  }, [appSettings.default_workflow, youtubeUrl, config.language, config.model, compatibleModel, handleCreateProject, startTask]);

  // ── Import local videos; a project is created only after a real selection. ──
  const importFiles = useCallback(async (files: File[]) => {
    if (importActionLock.current) return;
    const supported = files.filter(file => /\.(mp4|mkv|mov|webm|avi)$/i.test(file.name));
    if (!supported.length) {
      setToast('请选择 MP4、MKV、MOV、WebM 或 AVI 视频');
      return;
    }
    importActionLock.current = true;
    setTaskStarting(true);
    try {
      for (const file of supported) {
        try {
        const created = await api.createProject({
          source_type: 'local', title: file.name,
          language: config.language, target_language: config.target_language,
        });
        setUploadProgress(0);
        addLog('info', '导入视频', `正在导入 ${file.name}`);
        const result = await api.importLocalVideo(created.project_id, file, {
          autostart: appSettings.default_workflow !== 'manual', model: config.model, language: config.language,
          onProgress: setUploadProgress,
        });
        setUploadProgress(null);
        const listing = await api.listProjects();
        setProjects(listing.projects);
        const project = listing.projects.find(item => item.id === created.project_id);
        if (project) await selectProject(project);
        if (result.task_id) {
          const status = await api.getTaskStatus(result.task_id);
          backendLogTaskId.current = result.task_id;
          lastBackendLogCount.current = 0;
          setCurrentTask(status);
          syncProcessFromTask(status);
          ingestTaskLogs(status);
          setPollInterval(1000);
        }
        } catch (error: any) {
          setUploadProgress(null);
          addLog('error', '导入视频', error.message);
          setToast(error.message);
        }
      }
    } finally {
      importActionLock.current = false;
      setTaskStarting(false);
    }
  }, [addLog, appSettings.default_workflow, config.language, config.model, config.target_language, ingestTaskLogs, selectProject, syncProcessFromTask]);

  const handleImportLocal = useCallback(() => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = '.mp4,.mkv,.mov,.webm,.avi,video/*';
    input.onchange = () => void importFiles(Array.from(input.files || []));
    input.click();
  }, [importFiles]);

  // ── Step actions ──
  const doExtractAudio = useCallback(() => {
    if (!activeProject) return;
    startTask('提取音频', 'extract_audio', () => api.startExtractAudio(activeProject.id));
  }, [activeProject, startTask]);

  const doTranscribe = useCallback(() => {
    if (!activeProject) return;
    const model = compatibleModel();
    if (!model) return;
    setSubtitleStats(null);
    startTask('转写', 'transcribe', () => api.startTranscribe(activeProject.id, config.language, model));
  }, [activeProject, compatibleModel, config.language, startTask]);

  const doGenerateSubtitles = useCallback(() => {
    if (!activeProject) return;
    const model = compatibleModel();
    if (!model) return;
    setSubtitleStats(null);
    startTask('自动生成字幕', 'transcribe', () => api.startWorkflow(activeProject.id, {
      model, language: config.language,
    }));
  }, [activeProject, compatibleModel, config.language, startTask]);

  const recoverTranscription = useCallback(async () => {
    if (!activeProject || !currentTask?.recoverable) return;
    try {
      const status = await api.getTranscriptionModels(activeProject.id, config.language);
      const failedModel = String(currentTask.details?.model_id || currentTask.details?.resolved_model || config.model);
      const fallback = status.models.find(item => item.ready && item.id !== failedModel && item.id === 'small')
        || status.models.find(item => item.ready && item.id !== failedModel);
      if (!fallback) {
        setToast('没有已就绪的备用模型，请打开转写参数选择模型');
        return;
      }
      const detail = fallback.download_required ? '该模型可能需要下载。' : '该模型已在本机就绪。';
      if (!window.confirm(`当前转写失败：${currentTask.error || currentTask.message}\n\n是否改用 ${fallback.name} 重试？${detail}`)) return;
      await startTask('备用模型转写', 'transcribe', () => api.retryTranscription(activeProject.id, {
        model: fallback.id, language: config.language,
      }));
    } catch (error: any) {
      setToast(`无法启动恢复：${error.message}`);
    }
  }, [activeProject, config.language, config.model, currentTask, startTask]);

  const doClean = useCallback(() => {
    if (!activeProject) return;
    startTask('AI 整理', 'clean', () => api.startClean(activeProject.id, config.clean_target_length));
  }, [activeProject, config.clean_target_length, startTask]);

  const undoClean = useCallback(async () => {
    if (!activeProject || (currentTask && ['running', 'pending', 'paused'].includes(currentTask.status))) return;
    try {
      const result = await api.undoClean(activeProject.id);
      await refreshSegments(activeProject.id);
      const listing = await api.listProjects();
      setProjects(listing.projects);
      setToast(result.message);
      window.setTimeout(() => setToast(''), 3000);
    } catch (error: any) {
      setToast(error.message.includes('404') ? '没有可撤销的整理记录' : error.message);
      window.setTimeout(() => setToast(''), 3500);
    }
  }, [activeProject, currentTask, refreshSegments]);

  const doTranslate = useCallback(() => {
    if (!activeProject) return;
    startTask('AI 翻译', 'translate', () => api.startTranslate(activeProject.id, config.target_language));
  }, [activeProject, config.target_language, startTask]);

  // ── Export ──
  const doExport = useCallback(async (fmt: ExportFormat) => {
    if (!activeProject || exportActionLock.current) return;
    exportActionLock.current = true;
    setTaskStarting(true);
    setStepStatus('export', 'running', 10);
    try {
      if (fmt === 'mp4' || fmt === 'mkv') {
        const r = await api.exportSubtitles(activeProject.id, {
          format: fmt, bilingual: config.bilingual,
          primary_language: 'original'
        });
        if (r.task_id) {
          const s = await api.getTaskStatus(r.task_id);
          backendLogTaskId.current = r.task_id;
          lastBackendLogCount.current = 0;
          setCurrentTask(s);
          ingestTaskLogs(s);
          setPollInterval(2000);
          setStepStatus('render', 'running', 10);
          addLog('info', '压制视频', `${fmt.toUpperCase()} 视频导出任务已创建`);
        }
      } else {
        await api.exportSubtitles(activeProject.id, {
          format: fmt, bilingual: config.bilingual,
          primary_language: 'original'
        });
        addLog('info', '导出', `${fmt.toUpperCase()} 导出成功`);
        setStepStatus('export', 'success', 100);
        window.open(api.getExportDownloadUrl(activeProject.id, fmt), '_blank');
      }
    } catch (e: any) {
      addLog('error', '导出', `${fmt} 导出失败: ${e.message}`);
      setStepStatus('export', 'failed', 0, e.message, '检查文件权限和 ffmpeg');
    } finally {
      exportActionLock.current = false;
      setTaskStarting(false);
    }
  }, [activeProject, config.bilingual, addLog, ingestTaskLogs, setStepStatus]);

  // ── Update segment ──
  const handleUpdateSegment = useCallback(async (idx: number, data: { clean_text?: string; translated_text?: string; locked?: boolean }) => {
    if (!activeProject) return;
    try {
      await api.updateSegment(activeProject.id, idx, data);
      await refreshSegments(activeProject.id);
    } catch (e: any) {
      addLog('error', '编辑', `更新字幕失败: ${e.message}`);
    }
  }, [activeProject, refreshSegments, addLog]);

  // ── Video time sync ──
  const handleTimeUpdate = useCallback((time: number) => {
    setCurrentTime(time);
    const idx = segments.findLastIndex(s => s.start <= time && s.end >= time);
    setActiveSegmentIdx(idx);
  }, [segments]);

  // ── Seek video from table ──
  const handleSeek = useCallback((time: number) => {
    setCurrentTime(time);
    videoPlayerRef.current?.seekTo(time);
  }, []);

  const toggleTaskPause = useCallback(async () => {
    if (!currentTask) return;
    try {
      const next = currentTask.status === 'paused'
        ? await api.resumeTask(currentTask.id)
        : await api.pauseTask(currentTask.id);
      setCurrentTask(next);
      syncProcessFromTask(next);
      addLog('info', next.type, next.status === 'paused' ? '任务已暂停' : '任务已继续');
    } catch (error: any) {
      addLog('error', currentTask.type, error.message);
    }
  }, [currentTask, syncProcessFromTask, addLog]);

  const cancelCurrentTask = useCallback(async () => {
    if (!currentTask || !['pending', 'running', 'paused'].includes(currentTask.status)) return;
    try {
      const next = await api.cancelTask(currentTask.id);
      setCurrentTask(next);
      ingestTaskLogs(next);
      setPollInterval(null);
      syncProcessFromTask(next);
      addLog('warning', next.type, '任务已终止');
      setToast('任务已安全终止');
      window.setTimeout(() => setToast(''), 2600);
      if (activeProject) {
        api.getProject(activeProject.id).then(setActiveProject).catch(() => {});
        api.listProjects().then(listing => setProjects(listing.projects)).catch(() => {});
        api.getSegments(activeProject.id).then(result => setSegments(result.segments)).catch(() => {});
      }
    } catch (error: any) {
      addLog('error', currentTask.type, `终止失败：${error.message}`);
      setToast('无法终止当前任务');
      window.setTimeout(() => setToast(''), 3000);
    }
  }, [currentTask, activeProject, syncProcessFromTask, addLog, ingestTaskLogs]);

  const handleStyleChange = useCallback((style: SubtitleStyleSettings) => {
    setSubtitleStyle(style);
    saveSubtitleStyle(style);
  }, []);

  const projectGroups = useMemo(() => {
    const groups = new Map<string, { key: string; label: string; projects: Project[]; rank: number }>();
    for (const project of projects) {
      const namedGroup = project.source_type === 'youtube' ? project.group_name?.trim() : '';
      const key = project.source_type === 'local'
        ? 'local'
        : namedGroup ? `youtube:named:${encodeURIComponent(namedGroup)}` : 'youtube:ungrouped';
      const label = project.source_type === 'local' ? '本地视频' : namedGroup || '未分组';
      const rank = project.source_type === 'local' ? 2 : namedGroup ? 0 : 1;
      const group = groups.get(key) || { key, label, projects: [], rank };
      group.projects.push(project);
      groups.set(key, group);
    }
    return [...groups.values()].sort((left, right) =>
      left.rank - right.rank || left.label.localeCompare(right.label, 'zh-CN')
    );
  }, [projects]);

  const knownProjectGroups = useMemo(() => Array.from(new Set(
    projects.filter(project => project.source_type === 'youtube' && project.group_name)
      .map(project => project.group_name as string)
  )).sort((left, right) => left.localeCompare(right, 'zh-CN')), [projects]);

  const toggleProjectGroup = useCallback((key: string) => {
    setCollapsedProjectGroups(current => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  const openProjectGroupEditor = useCallback((project: Project) => {
    if (groupEditorProjectId === project.id) {
      setGroupEditorProjectId(null);
      return;
    }
    setGroupDraft(project.group_name || '');
    setGroupEditorProjectId(project.id);
  }, [groupEditorProjectId]);

  const saveProjectGroup = useCallback(async (project: Project) => {
    const normalized = groupDraft.trim();
    try {
      const updated = await api.updateProjectGroup(project.id, normalized || null);
      setProjects(current => current.map(item => item.id === updated.id ? updated : item));
      setActiveProject(current => current?.id === updated.id ? updated : current);
      setGroupEditorProjectId(null);
      window.requestAnimationFrame(() => document.getElementById(`project-group-action-${project.id}`)?.focus());
      setToast(normalized ? `已移至“${normalized}”` : '已移至“未分组”');
      window.setTimeout(() => setToast(''), 2400);
    } catch (error: any) {
      setToast(`分组保存失败：${error.message}`);
      window.setTimeout(() => setToast(''), 3200);
    }
  }, [groupDraft]);

  const showToast = useCallback((message: string, duration = 2800) => {
    setToast(message);
    window.setTimeout(() => setToast(''), duration);
  }, []);

  const openProjectMenu = useCallback((event: React.MouseEvent, project: Project, trashed = false) => {
    event.preventDefault();
    event.stopPropagation();
    const menuWidth = 218;
    const menuHeight = trashed ? 132 : 190;
    const trigger = event.target instanceof HTMLElement ? event.target.closest<HTMLElement>('button') : null;
    contextMenuReturnFocus.current = trigger;
    const triggerRect = trigger?.getBoundingClientRect();
    const requestedX = event.clientX || triggerRect?.right || 10;
    const requestedY = event.clientY || triggerRect?.bottom || 10;
    setContextMenu({
      project, trashed,
      x: Math.min(requestedX, window.innerWidth - menuWidth - 10),
      y: Math.min(requestedY, window.innerHeight - menuHeight - 10),
    });
  }, []);

  const resetActiveProject = useCallback(() => {
    setActiveProject(null);
    setSegments([]);
    setCurrentTask(null);
    setPollInterval(null);
    setProcessSteps(emptyProcess());
    setSelectedStep(null);
    setInspectorMode(null);
  }, []);

  const animateProjectRemoval = useCallback(async (projectIds: string[]) => {
    setRemovingProjectIds(current => new Set([...current, ...projectIds]));
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (motionEnabled && !reducedMotion) {
      await new Promise(resolve => window.setTimeout(resolve, 180));
    }
  }, [motionEnabled]);

  const moveProjectToTrash = useCallback(async (project: Project) => {
    const activeTask = activeProject?.id === project.id && !!currentTask && ['pending', 'running', 'paused'].includes(currentTask.status);
    let terminate = false;
    if (activeTask) {
      terminate = window.confirm('这个项目正在处理。移入回收站会先终止当前任务，是否继续？');
      if (!terminate) return;
    }
    try {
      await api.trashProject(project.id, terminate);
    } catch (error: any) {
      if (!terminate && String(error.message).includes('ACTIVE_TASKS')) {
        if (!window.confirm('这个项目仍有运行中的任务。是否终止任务并移入回收站？')) return;
        await api.trashProject(project.id, true);
      } else {
        showToast(`无法移入回收站：${error.message}`, 3600);
        return;
      }
    }
    await animateProjectRemoval([project.id]);
    if (activeProject?.id === project.id) resetActiveProject();
    if (localStorage.getItem('subtitle_factory_last_project_id') === project.id) localStorage.removeItem('subtitle_factory_last_project_id');
    await refreshLibraries();
    showToast('项目已移入回收站');
    setRemovingProjectIds(current => { const next = new Set(current); next.delete(project.id); return next; });
  }, [activeProject?.id, animateProjectRemoval, currentTask, refreshLibraries, resetActiveProject, showToast]);

  const restoreProject = useCallback(async (project: Project) => {
    try {
      await api.restoreProject(project.id);
      await animateProjectRemoval([project.id]);
      await refreshLibraries();
      setRemovingProjectIds(current => { const next = new Set(current); next.delete(project.id); return next; });
      showToast('项目已恢复');
    } catch (error: any) { showToast(`恢复失败：${error.message}`, 3600); }
  }, [animateProjectRemoval, refreshLibraries, showToast]);

  const deleteProjectForever = useCallback(async (project: Project) => {
    if (!window.confirm(`永久删除“${project.title}”？\n\n视频、音频、字幕、任务和导出文件都会被清理，此操作无法撤销。`)) return;
    try {
      await api.permanentlyDeleteProject(project.id);
      await animateProjectRemoval([project.id]);
      await refreshLibraries();
      setRemovingProjectIds(current => { const next = new Set(current); next.delete(project.id); return next; });
      showToast('项目已永久删除');
    } catch (error: any) { showToast(`永久删除失败：${error.message}`, 3600); }
  }, [animateProjectRemoval, refreshLibraries, showToast]);

  const clearTrash = useCallback(async () => {
    if (!trashProjects.length || !window.confirm(`永久删除回收站中的 ${trashProjects.length} 个项目？此操作无法撤销。`)) return;
    try {
      const result = await api.emptyTrash();
      await animateProjectRemoval(trashProjects.map(project => project.id));
      await refreshLibraries();
      setRemovingProjectIds(new Set());
      showToast(result.message || '回收站已清空');
    } catch (error: any) { showToast(`清空失败：${error.message}`, 3600); }
  }, [animateProjectRemoval, refreshLibraries, showToast, trashProjects]);

  const saveRename = useCallback(async () => {
    if (!renameProjectState || !renameDraft.trim()) return;
    try {
      const updated = await api.renameProject(renameProjectState.id, renameDraft.trim());
      setProjects(current => current.map(project => project.id === updated.id ? updated : project));
      setActiveProject(current => current?.id === updated.id ? updated : current);
      setRenameProjectState(null);
      showToast('项目已重命名');
    } catch (error: any) { showToast(`重命名失败：${error.message}`, 3600); }
  }, [renameDraft, renameProjectState, showToast]);

  const retryDownload = useCallback(() => {
    if (!activeProject?.source_url) return;
    startTask('重新下载', 'download', () => api.startDownload(activeProject.id, activeProject.source_url as string));
  }, [activeProject, startTask]);

  const openWorkflowStep = useCallback((stepId: string) => {
    setSelectedStep(stepId);
    setInspectorMode('step');
  }, []);

  // ── Step indicators ──
  const hasAudio = activeProject?.audio_path;
  const hasSegments = segments.length > 0;
  const isProcessing = taskStarting || !!(currentTask && (currentTask.status === 'running' || currentTask.status === 'pending' || currentTask.status === 'paused'));
  const activeSegmentIndex = activeSegmentIdx >= 0 ? segments[activeSegmentIdx]?.index ?? -1 : -1;
  const activeAIPreset = aiPresets.find(item => item.id === aiSettings?.provider);

  const compactSteps = useMemo(() => {
    const find = (id: string) => processSteps.find(step => step.id === id);
    const combine = (...ids: string[]): ProcessStep => {
      const values = ids.map(find).filter(Boolean) as ProcessStep[];
      const failed = values.find(step => step.status === 'failed');
      const running = values.find(step => step.status === 'running' || step.status === 'paused');
      const allDone = values.length > 0 && values.every(step => step.status === 'success');
      return failed || running || { ...(values.at(-1) || emptyProcess()[0]), status: allDone ? 'success' : 'waiting', progress: allDone ? 100 : 0 };
    };
    return [
      { id: 'download', label: '下载', icon: '⇩', state: combine('download', 'extract_audio') },
      { id: 'transcribe', label: '转写', icon: '⌁', state: combine('transcribe') },
      { id: 'clean', label: '整理', icon: '✦', state: combine('clean') },
      { id: 'translate', label: '翻译', icon: '文', state: combine('translate') },
      { id: 'export', label: '导出', icon: '↗', state: combine('export', 'render') },
    ];
  }, [processSteps]);

  const primaryActionLabel = !activeProject ? '生成字幕'
    : currentTask?.status === 'paused' ? '继续'
      : currentTask?.status === 'failed' ? '重试'
        : !hasSegments ? '生成字幕' : '继续';

  const taskInspectorStep = (() => {
    const raw = currentTask?.step || currentTask?.type || 'transcribe';
    if (raw === 'extract_audio' || raw === 'workflow' || raw === 'prepare_model') return raw === 'extract_audio' ? 'download' : 'transcribe';
    if (raw === 'render') return 'export';
    return ['download', 'transcribe', 'clean', 'translate', 'export'].includes(raw) ? raw : 'transcribe';
  })();

  const runPrimaryAction = useCallback(() => {
    if (!activeProject) { setShowLinkPopover(true); return; }
    if (currentTask?.status === 'paused') { void toggleTaskPause(); return; }
    if (currentTask?.status === 'failed' && currentTask.recoverable) { void recoverTranscription(); return; }
    if (!hasSegments || currentTask?.status === 'failed') { doGenerateSubtitles(); return; }
    openWorkflowStep(config.target_language === 'none' ? 'clean' : 'translate');
  }, [activeProject, config.target_language, currentTask, doGenerateSubtitles, hasSegments, openWorkflowStep, recoverTranscription, toggleTaskPause]);

  // 总进度计算
  const totalProgress = Math.round(
    processSteps.reduce((sum, s) => {
      const weights: Record<TaskStepStatus, number> = {
        waiting: 0, skipped: 0,
        running: s.progress * 0.01,
        paused: s.progress * 0.01,
        success: 1, failed: 1, cancelled: 0, partial: 0.5,
      };
      return sum + (weights[s.status] || 0);
    }, 0) / processSteps.length * 100
  );

  return (
    <div className={`app pro-app theme-${theme} density-${density} ${motionEnabled ? '' : 'motion-off'} ${theaterMode ? 'theater-active' : ''}`}
      onDragEnter={event => { event.preventDefault(); setDragActive(true); }}
      onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
      onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragActive(false); }}
      onDrop={event => { event.preventDefault(); setDragActive(false); void importFiles(Array.from(event.dataTransfer.files)); }}>
      <header className="studio-topbar" data-tauri-drag-region>
        <div className="brand-block" data-tauri-drag-region>
          <img className="brand-mark" src={appIcon} alt=""/><strong data-tauri-drag-region>字幕工厂</strong>
        </div>
        <div className="active-project-title" data-tauri-drag-region>
          <strong data-tauri-drag-region>{activeProject?.title || '项目库'}</strong>
          <span data-tauri-drag-region>{activeProject ? `${segments.length} 条字幕 · ${languageLabel(config.language)}` : '本地优先的专业字幕工作台'}</span>
        </div>
        <div className="topbar-actions">
          <button className="topbar-button" disabled={backendStatus !== 'connected'} onClick={handleImportLocal}><span>＋</span>导入</button>
          <button className={`topbar-button ${showLinkPopover ? 'active' : ''}`} disabled={backendStatus !== 'connected'} onClick={() => setShowLinkPopover(value => !value)}><span>⌁</span>链接</button>
          <button className={`task-status-pill ${backendStatus}`} onClick={() => isProcessing && openWorkflowStep(taskInspectorStep)}>
            <i className={`backend-dot ${backendStatus}`}/><span>{isProcessing ? `${currentTask?.message || '正在处理'} · ${Math.round(currentTask?.progress || 0)}%` : backendStatus === 'connected' ? '引擎就绪' : backendStatus === 'connecting' ? '正在启动' : '引擎异常'}</span>
          </button>
          <button className="icon-action" aria-label={theme === 'dark' ? '切换浅色模式' : '切换深色模式'} onClick={() => setTheme(value => value === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☀︎' : '◐'}</button>
          <button className="icon-action" aria-label="打开设置" onClick={() => setShowAISettings(true)}><img className="topbar-control-icon" src={settingsIcon} alt=""/></button>
        </div>
        {showLinkPopover && <div className="link-popover">
          <div><strong>从 YouTube 链接创建</strong><button aria-label="关闭" onClick={() => setShowLinkPopover(false)}>×</button></div>
          <input autoFocus type="url" value={youtubeUrl} placeholder="https://www.youtube.com/watch?v=…" onChange={event => setYoutubeUrl(event.target.value)}/>
          <p>播放定位参数会自动移除并下载完整视频。</p>
          <button className="button primary" disabled={!youtubeUrl || backendStatus !== 'connected'} onClick={() => { setShowLinkPopover(false); void handleFullPipeline(); }}>下载并生成字幕</button>
        </div>}
      </header>

      {backendStatus === 'error' && <div className="engine-error-banner"><strong>本地引擎未能启动</strong><span>打开设置查看 FFmpeg、yt-dlp、模型与存储诊断。</span><button onClick={refreshHealth}>重新检查</button></div>}
      {uploadProgress !== null && <div className="upload-progress-banner" role="status"><span>正在导入视频</span><progress value={uploadProgress} max={100}/><strong>{uploadProgress}%</strong></div>}

      <div className={`studio-shell ${inspectorMode ? 'inspector-open' : ''}`} style={{
        '--left-panel-width': `${leftPanelWidth}px`, '--right-panel-width': `${rightPanelWidth}px`,
      } as React.CSSProperties}>
        <aside className="project-sidebar">
          <div className="library-switcher" role="tablist" aria-label="项目库视图">
            <button role="tab" aria-selected={libraryView === 'projects'} className={libraryView === 'projects' ? 'active' : ''} onClick={() => setLibraryView('projects')}>项目 <span>{projects.length}</span></button>
            <button role="tab" aria-selected={libraryView === 'trash'} className={libraryView === 'trash' ? 'active' : ''} onClick={() => setLibraryView('trash')}>回收站 <span>{trashProjects.length}</span></button>
          </div>
          <div className="project-list">
            <datalist id="project-group-options">{knownProjectGroups.map(name => <option key={name} value={name}/>)}</datalist>
            {libraryView === 'projects' && projectGroups.map(group => {
              const collapsed = collapsedProjectGroups.has(group.key);
              return <section className="project-group" key={group.key}>
                <button className="project-group-header" aria-expanded={!collapsed} onClick={() => toggleProjectGroup(group.key)}><span><i>{collapsed ? '›' : '⌄'}</i>{group.label}</span><small>{group.projects.length}</small></button>
                {!collapsed && <div className="project-group-items">{group.projects.map(project => {
                  const thumbnailUrl = api.getProjectThumbnailUrl(project);
                  const editingGroup = groupEditorProjectId === project.id;
                  return <div className={`project-card-shell ${removingProjectIds.has(project.id) ? 'removing' : ''}`} key={project.id} onContextMenu={event => openProjectMenu(event, project)}>
                    <button className={`project-card ${activeProject?.id === project.id ? 'active' : ''}`} onClick={() => void selectProject(project)}>
                      <span className="project-thumb"><span className="project-thumb-fallback">{project.source_type === 'youtube' ? '▶' : '▣'}</span>{thumbnailUrl && <img src={thumbnailUrl} alt="" loading="lazy" onError={event => { event.currentTarget.style.display = 'none'; }}/>}</span>
                      <span className="project-card-copy"><strong>{project.title}</strong><small>{project.segments_count} 条 · {project.created_at.slice(0, 10)}</small></span>
                      <span className="project-more" aria-hidden="true">•••</span>
                    </button>
                    {editingGroup && <div className="project-group-editor"><input autoFocus list="project-group-options" value={groupDraft} placeholder="分组名称" onChange={event => setGroupDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void saveProjectGroup(project); if (event.key === 'Escape') setGroupEditorProjectId(null); }}/><button onClick={() => void saveProjectGroup(project)}>保存</button><button aria-label="取消" onClick={() => setGroupEditorProjectId(null)}>×</button></div>}
                  </div>;
                })}</div>}
              </section>;
            })}
            {libraryView === 'projects' && backendStatus === 'connecting' && !projects.length && <div className="library-skeleton" aria-label="正在载入项目"><i/><i/><i/></div>}
            {libraryView === 'projects' && backendStatus !== 'connecting' && !projects.length && <div className="project-empty"><span>▱</span><strong>还没有项目</strong><small>导入视频、拖放文件或粘贴链接开始。</small></div>}
            {libraryView === 'trash' && trashProjects.map(project => <button className={`project-card trash-card ${removingProjectIds.has(project.id) ? 'removing' : ''}`} key={project.id} onContextMenu={event => openProjectMenu(event, project, true)} onClick={event => openProjectMenu(event, project, true)}>
              <span className="project-thumb"><span className="project-thumb-fallback">♲</span></span><span className="project-card-copy"><strong>{project.title}</strong><small>{project.deleted_at?.slice(0, 10) || '已删除'} · 媒体仍保留</small></span><span className="project-more">•••</span>
            </button>)}
            {libraryView === 'trash' && !trashProjects.length && <div className="project-empty"><span>♲</span><strong>回收站为空</strong><small>移入回收站的项目会保留媒体与字幕。</small></div>}
          </div>
          <div className="sidebar-footer">
            {libraryView === 'trash' ? <button className="sidebar-action danger" disabled={!trashProjects.length} onClick={clearTrash}>清空回收站</button> : <><button className="sidebar-action" onClick={handleImportLocal}>＋ 导入视频</button><button className="sidebar-action" onClick={() => setShowLinkPopover(true)}>⌁ 添加链接</button></>}
          </div>
        </aside>

        <div className="panel-resizer panel-resizer-left" role="separator" aria-label="调整项目库宽度" tabIndex={0} onPointerDown={event => beginResize('left', event)} onKeyDown={event => { if (event.key === 'ArrowLeft') setLeftPanelWidth(value => Math.max(210, value - 16)); if (event.key === 'ArrowRight') setLeftPanelWidth(value => Math.min(430, value + 16)); }}/>

        <main className="editor-workspace">
          <section className="fixed-viewer" style={{ '--viewer-height': `${viewerHeight}px` } as React.CSSProperties}>
            {activeProject?.video_path ? <SubtitlePlayer ref={videoPlayerRef} videoUrl={api.getVideoUrl(activeProject.id)} segments={segments} style={subtitleStyle} activeIdx={activeSegmentIdx} onTimeUpdate={handleTimeUpdate} onDurationChange={setVideoDuration} onStyleChange={handleStyleChange} theaterMode={theaterMode} onTheaterModeChange={setTheaterMode}/>
              : <div className="viewer-welcome"><span>▶</span><h2>开始创作字幕</h2><p>导入视频或粘贴 YouTube 链接</p><div><button className="button primary" onClick={handleImportLocal}>导入视频</button><button className="button secondary" onClick={() => setShowLinkPopover(true)}>添加链接</button></div></div>}
          </section>
          {activeProject && <SubtitleTimeline segments={segments} currentTime={currentTime} duration={videoDuration} onSeek={handleSeek}/>}
          <div className="viewer-resizer" role="separator" aria-label="调整播放器高度" tabIndex={0} onPointerDown={event => beginResize('viewer', event)} onKeyDown={event => { if (event.key === 'ArrowUp') setViewerHeight(value => Math.max(260, value - 20)); if (event.key === 'ArrowDown') setViewerHeight(value => Math.min(window.innerHeight - 250, value + 20)); }}><span/></div>

          <section className="workflow-bar" aria-label="字幕流程">
            <button className="workflow-primary" disabled={isProcessing && currentTask?.status !== 'paused'} onClick={runPrimaryAction}><span>{currentTask?.status === 'failed' ? '↻' : currentTask?.status === 'paused' ? '▶' : '✦'}</span>{primaryActionLabel}</button>
            <div className="compact-flow">
              {compactSteps.map(step => <button key={step.id} className={`compact-step ${step.state.status} ${selectedStep === step.id && inspectorMode === 'step' ? 'selected' : ''}`} onClick={() => openWorkflowStep(step.id)}>
                <i>{step.state.status === 'success' ? '✓' : step.state.status === 'failed' ? '!' : step.icon}</i><span>{step.label}</span><em>{step.state.status === 'running' ? `${Math.round(step.state.progress)}%` : step.state.status === 'paused' ? '已暂停' : ''}</em>
                {(step.state.status === 'running' || step.state.status === 'paused') && <b style={{ width: `${step.state.progress}%` }}/>}
              </button>)}
            </div>
            {currentTask && isProcessing && <div className="workflow-task-controls"><button onClick={toggleTaskPause}>{currentTask.status === 'paused' ? '继续' : '暂停'}</button><button className="danger" onClick={cancelCurrentTask}>停止</button></div>}
            <span className="workflow-total">{totalProgress}%</span>
          </section>

          <section className="lower-workspace">
            <nav className="workspace-tabs" role="tablist" aria-label="项目工作区">
              {([['subtitles', '字幕'], ['style', '样式'], ['export', '导出'], ['logs', '日志']] as const).map(([id, label]) => <button key={id} role="tab" aria-selected={bottomTab === id} className={bottomTab === id ? 'active' : ''} onClick={() => { setBottomTab(id); if (id === 'style') setInspectorMode('style'); }}>{label}{id === 'subtitles' && <span>{segments.length}</span>}{id === 'logs' && processLogs.length > 0 && <span>{processLogs.length}</span>}</button>)}
            </nav>
            <div className="workspace-tab-content" key={bottomTab}>
              {bottomTab === 'subtitles' && (activeProject ? <SubtitleTable segments={segments} currentTime={currentTime} activeIdx={activeSegmentIndex} onSeek={handleSeek} onUpdate={handleUpdateSegment} onAutoScrollChange={setAutoScrollTable} autoScroll={autoScrollTable} disabled={isProcessing}/> : <div className="transcript-empty">选择项目后，字幕会在这里按时间排列并可直接编辑。</div>)}
              {bottomTab === 'style' && <div className="style-overview"><div className="style-preview-card" style={{ fontFamily: subtitleStyle.fontFamily }}><span style={{ color: subtitleStyle.originalTextColor, fontSize: Math.min(24, subtitleStyle.originalFontSize) }}>为每一句话找到恰好的位置。</span><small style={{ color: subtitleStyle.translatedTextColor }}>Give every line its perfect place.</small></div><div><h3>字幕样式</h3><p>调整字体、字号、双语顺序、颜色、背景与垂直位置。更改会立即显示在播放器中。</p><button className="button primary" onClick={() => setInspectorMode('style')}>打开样式检查器</button></div></div>}
              {bottomTab === 'export' && <div className="export-workspace"><header><div><h3>导出项目</h3><p>字幕文件会立即下载；视频导出将在后台压制。</p></div><label><input type="checkbox" checked={config.bilingual} onChange={event => setConfig({ ...config, bilingual: event.target.checked })}/> 包含双语</label></header><div className="export-cards">{(['srt', 'vtt', 'ass', 'srt-bilingual', 'mp4', 'mkv'] as ExportFormat[]).map(format => <button key={format} disabled={!hasSegments || isProcessing} onClick={() => void doExport(format)}><strong>{format === 'srt-bilingual' ? '双语 SRT' : format.toUpperCase()}</strong><small>{format === 'mp4' || format === 'mkv' ? '带字幕视频' : '字幕文件'}</small><span>↗</span></button>)}</div></div>}
              {bottomTab === 'logs' && <div className="logs-workspace"><ProcessLogViewer logs={processLogs} collapsed={false} onToggle={() => undefined} onClear={() => setProcessLogs([])}/></div>}
            </div>
          </section>
        </main>

        {inspectorMode && <><div className="panel-resizer panel-resizer-right" role="separator" aria-label="调整检查器宽度" tabIndex={0} onPointerDown={event => beginResize('right', event)} onKeyDown={event => { if (event.key === 'ArrowLeft') setRightPanelWidth(value => Math.min(480, value + 16)); if (event.key === 'ArrowRight') setRightPanelWidth(value => Math.max(280, value - 16)); }}/>
          <aside className="inspector-sidebar">
            <header className="inspector-title"><div><strong>{inspectorMode === 'style' ? '样式检查器' : compactSteps.find(step => step.id === selectedStep)?.label || '步骤详情'}</strong><small>{inspectorMode === 'style' ? '更改将实时预览' : '确认设置后再开始高成本操作'}</small></div><button aria-label="关闭检查器" onClick={() => setInspectorMode(null)}>×</button></header>
            {inspectorMode === 'style' && <SubtitleStylePanel style={subtitleStyle} onChange={handleStyleChange}/>}
            {inspectorMode === 'step' && <div className="step-inspector">
              {selectedStep === 'download' && <section className="inspector-section"><h3>下载与音频</h3><label>项目链接<input value={activeProject?.source_url || youtubeUrl} placeholder="YouTube URL" onChange={event => setYoutubeUrl(event.target.value)}/></label><div className="runtime-mini"><span className={health?.runtime?.ffmpeg?.ok ? 'ok' : 'error'}>FFmpeg {health?.runtime?.ffmpeg?.ok ? '可用' : '需检查'}</span><span className={health?.runtime?.yt_dlp?.ok ? 'ok' : 'error'}>yt-dlp {health?.runtime?.yt_dlp?.ok ? '可用' : '需检查'}</span></div><p>下载会移除 t=110s 等定位参数；失败时保留原项目，可在此重新下载。</p><button className="button primary" disabled={!activeProject?.source_url || isProcessing} onClick={retryDownload}>重新下载</button>{activeProject?.video_path && <button className="button secondary" disabled={isProcessing} onClick={doExtractAudio}>重新提取音频</button>}</section>}
              {selectedStep === 'transcribe' && <section className="inspector-section"><h3>语音转写</h3><label>模型<select value={config.model} onChange={event => setConfig({ ...config, model: event.target.value as ModelSize })}><option value="auto">自动选择 · Whisper Small</option><option value="small">Whisper Small</option><option value="medium">Whisper Medium</option><option value="large-v3">Whisper Large V3</option>{modelStatus?.models.filter(model => model.id.includes('parakeet') && (model.ready || model.download_required)).map(model => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label><label>源语言<LanguagePicker value={config.language} onChange={language => setConfig({ ...config, language })}/></label>{modelStatus && <div className="model-readiness"><strong>推荐：{modelStatus.models.find(model => model.id === modelStatus.recommended_model)?.name || modelStatus.recommended_model}</strong>{modelStatus.models.filter(model => model.ready).slice(0, 3).map(model => <small className="ready" key={model.id}>✓ {model.name} 已就绪</small>)}</div>}<button className="button primary" disabled={!hasAudio || isProcessing} onClick={doTranscribe}>开始转写</button></section>}
              {selectedStep === 'clean' && <section className="inspector-section"><h3>AI 忠实整理</h3><div className="ai-summary-row"><span className="ai-logo">✦</span><div><strong>{activeAIPreset?.name || aiSettings?.provider || '未配置 AI'}</strong><small>{aiSettings?.model || '请先打开设置中心'}</small></div></div><label>参考单句长度 <span>{config.clean_target_length} 字</span><input type="range" min={16} max={100} step={2} value={config.clean_target_length} onChange={event => setConfig({ ...config, clean_target_length: Number(event.target.value) })}/></label><p>只修正明显错词、标点和断句，不改变原意。完整长句不会被强行截断。</p><button className="button primary" disabled={!hasSegments || isProcessing || !aiSettings?.has_api_key} onClick={doClean}>确认并开始整理</button><button className="button secondary" disabled={!hasSegments || isProcessing} onClick={undoClean}>撤销上次整理</button></section>}
              {selectedStep === 'translate' && <section className="inspector-section"><h3>AI 翻译</h3><label>目标语言<LanguagePicker mode="target" allowCustom allowNone value={config.target_language} onChange={target_language => setConfig({ ...config, target_language })}/></label><label className="check-row"><input type="checkbox" checked={config.bilingual} onChange={event => setConfig({ ...config, bilingual: event.target.checked })}/> 导出时包含原文与译文</label><p>翻译由已配置的 {activeAIPreset?.name || aiSettings?.provider || 'AI 服务'} 完成，结果可继续编辑。</p><button className="button primary" disabled={!hasSegments || isProcessing || !aiSettings?.has_api_key || config.target_language === 'none'} onClick={doTranslate}>确认并开始翻译</button></section>}
              {selectedStep === 'export' && <section className="inspector-section"><h3>导出</h3><div className="export-grid">{(['srt', 'vtt', 'ass', 'srt-bilingual', 'mp4', 'mkv'] as ExportFormat[]).map(format => <button key={format} disabled={!hasSegments || isProcessing} onClick={() => void doExport(format)}>{format === 'srt-bilingual' ? '双语 SRT' : format.toUpperCase()}</button>)}</div></section>}
              {currentTask?.status === 'failed' && <section className="recovery-card"><strong>{currentTask.error_code || '任务失败'}</strong><span>{currentTask.error || currentTask.message}</span>{currentTask.suggestion && <small>{currentTask.suggestion}</small>}{currentTask.recoverable && <button onClick={recoverTranscription}>使用备用模型重试</button>}</section>}
              <details className="inspector-details"><summary>流程诊断</summary><ProcessTimeline steps={processSteps} currentStepId={selectedStep} totalProgress={totalProgress} onStepClick={setSelectedStep}/><SubtitleStatsPanel stats={subtitleStats}/></details>
            </div>}
          </aside></>}
      </div>

      {contextMenu && <div ref={contextMenuRef} className="context-menu" role="menu" style={{ left: contextMenu.x, top: contextMenu.y }} onPointerDown={event => event.stopPropagation()} onKeyDown={event => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
        const current = Math.max(0, items.indexOf(document.activeElement as HTMLButtonElement));
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : event.key === 'ArrowDown' ? (current + 1) % items.length : (current - 1 + items.length) % items.length;
        items[next]?.focus();
      }}>
        {contextMenu.trashed ? <><button role="menuitem" onClick={() => { setContextMenu(null); void restoreProject(contextMenu.project); }}>↶ <span>恢复项目</span></button><button className="danger" role="menuitem" onClick={() => { setContextMenu(null); void deleteProjectForever(contextMenu.project); }}>⌫ <span>永久删除…</span></button></> : <>
          <button role="menuitem" onClick={() => { setContextMenu(null); void selectProject(contextMenu.project); }}>↗ <span>打开</span></button>
          <button role="menuitem" onClick={() => { setRenameDraft(contextMenu.project.title); setRenameProjectState(contextMenu.project); setContextMenu(null); }}>✎ <span>重命名…</span></button>
          <button role="menuitem" disabled={contextMenu.project.source_type !== 'youtube'} onClick={() => { openProjectGroupEditor(contextMenu.project); setContextMenu(null); }}>⌘ <span>移动分组…</span></button>
          <hr/><button className="danger" role="menuitem" onClick={() => { setContextMenu(null); void moveProjectToTrash(contextMenu.project); }}>♲ <span>移入回收站</span></button>
        </>}
      </div>}
      {renameProjectState && <div className="modal-backdrop" onMouseDown={() => setRenameProjectState(null)}><form className="rename-dialog" onMouseDown={event => event.stopPropagation()} onSubmit={event => { event.preventDefault(); void saveRename(); }}><header><div><h2>重命名项目</h2><p>媒体与字幕文件不会移动。</p></div><button type="button" aria-label="关闭" onClick={() => setRenameProjectState(null)}>×</button></header><input autoFocus maxLength={120} value={renameDraft} onChange={event => setRenameDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Escape') setRenameProjectState(null); }}/><footer><button type="button" className="button secondary" onClick={() => setRenameProjectState(null)}>取消</button><button className="button primary" disabled={!renameDraft.trim()}>保存</button></footer></form></div>}
      {dragActive && <div className="drop-overlay"><div><span>⇩</span><strong>松开以导入视频</strong><small>支持 MP4、MKV、MOV、WebM 和 AVI</small></div></div>}
      {toast && <div className="studio-toast" role="status" aria-live="polite"><span>✓</span>{toast}</div>}
      <SettingsCenter open={showAISettings} onClose={() => setShowAISettings(false)} config={config} onConfigChange={setConfig} appSettings={appSettings} onAppSettingsChange={setAppSettings} aiSettings={aiSettings} onAISaved={setAISettings} theme={theme} onThemeChange={setTheme} motionEnabled={motionEnabled} onMotionEnabledChange={setMotionEnabled} density={density} onDensityChange={setDensity} health={health} onRefreshHealth={refreshHealth} modelStatus={modelStatus} onRefreshModels={refreshModels} onOpenLogs={() => { setBottomTab('logs'); setInspectorMode(null); }}/>
    </div>
  );
}

// ── Subtitle Table Component (enhanced) ──

function fmtTime(seconds: number): string {
  if (!seconds && seconds !== 0) return '--:--.---';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 1000);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${ms.toString().padStart(3, '0')}`;
}

function SubtitleTable({
  segments, currentTime, activeIdx, onSeek, onUpdate, onAutoScrollChange, autoScroll, disabled
}: {
  segments: SubtitleSegment[];
  currentTime: number;
  activeIdx: number;
  onSeek: (time: number) => void;
  onUpdate: (idx: number, data: any) => void;
  onAutoScrollChange?: (v: boolean) => void;
  autoScroll?: boolean;
  disabled?: boolean;
}) {
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editField, setEditField] = useState<'clean_text' | 'translated_text' | null>(null);
  const [editValue, setEditValue] = useState('');
  const [searchText, setSearchText] = useState('');
  const [replaceText, setReplaceText] = useState('');
  const tableRef = useRef<HTMLDivElement>(null);
  const userScrolling = useRef(false);
  const programmaticScroll = useRef(false);
  const userScrollTimer = useRef<number | undefined>(undefined);
  const programmaticTimer = useRef<number | undefined>(undefined);
  const [rowHeight, setRowHeight] = useState(34);
  const [windowRange, setWindowRange] = useState({ start: 0, end: 90 });
  const visibleSegments = useMemo(() => {
    const needle = searchText.trim().toLocaleLowerCase();
    if (!needle) return segments;
    return segments.filter(segment =>
      `${segment.clean_text || segment.raw_text}\n${segment.translated_text}`.toLocaleLowerCase().includes(needle)
    );
  }, [searchText, segments]);
  const virtualized = visibleSegments.length > 240;
  const renderedSegments = virtualized ? visibleSegments.slice(windowRange.start, windowRange.end) : visibleSegments;
  const topSpacer = virtualized ? windowRange.start * rowHeight : 0;
  const bottomSpacer = virtualized ? Math.max(0, (visibleSegments.length - windowRange.end) * rowHeight) : 0;

  const updateWindow = useCallback(() => {
    const element = tableRef.current;
    if (!element || !virtualized) return;
    const start = Math.max(0, Math.floor(element.scrollTop / rowHeight) - 18);
    const end = Math.min(visibleSegments.length, Math.ceil((element.scrollTop + element.clientHeight) / rowHeight) + 18);
    setWindowRange(current => current.start === start && current.end === end ? current : { start, end });
  }, [rowHeight, virtualized, visibleSegments.length]);

  useEffect(() => {
    const element = tableRef.current;
    if (!element) return;
    const readRowHeight = () => {
      const value = Number.parseFloat(getComputedStyle(element).getPropertyValue('--subtitle-row-height'));
      if (Number.isFinite(value) && value > 0) setRowHeight(value);
    };
    readRowHeight();
    const observer = new ResizeObserver(() => { readRowHeight(); updateWindow(); });
    observer.observe(element);
    return () => observer.disconnect();
  }, [updateWindow]);

  useEffect(() => {
    setWindowRange({ start: 0, end: 90 });
    if (tableRef.current) tableRef.current.scrollTop = 0;
  }, [searchText]);

  const replaceAll = () => {
    if (disabled || !searchText) return;
    for (const segment of segments) {
      const clean = segment.clean_text || segment.raw_text;
      if (clean.includes(searchText)) onUpdate(segment.index, { clean_text: clean.split(searchText).join(replaceText) });
      if (segment.translated_text.includes(searchText)) onUpdate(segment.index, { translated_text: segment.translated_text.split(searchText).join(replaceText) });
    }
  };

  // Auto-scroll to active segment
  useEffect(() => {
    if (autoScroll && activeIdx >= 0 && tableRef.current && !userScrolling.current) {
      const position = visibleSegments.findIndex(segment => segment.index === activeIdx);
      if (position < 0) return;
      const element = tableRef.current;
      programmaticScroll.current = true;
      window.clearTimeout(programmaticTimer.current);
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches || !!document.querySelector('.motion-off');
      if (virtualized) {
        const top = Math.max(0, position * rowHeight - element.clientHeight / 2 + rowHeight / 2);
        element.scrollTo({ top, behavior: reduced ? 'auto' : 'smooth' });
        updateWindow();
      } else {
        const row = element.querySelector(`[data-idx="${activeIdx}"]`);
        if (row) row.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' });
      }
      programmaticTimer.current = window.setTimeout(() => { programmaticScroll.current = false; }, 380);
    }
  }, [activeIdx, autoScroll, rowHeight, updateWindow, virtualized, visibleSegments]);

  // Detect user scrolling
  const handleScroll = useCallback(() => {
    updateWindow();
    if (programmaticScroll.current) return;
    userScrolling.current = true;
    window.clearTimeout(userScrollTimer.current);
    userScrollTimer.current = window.setTimeout(() => { userScrolling.current = false; }, 2000);
    if (onAutoScrollChange) onAutoScrollChange(false);
  }, [onAutoScrollChange, updateWindow]);

  const startEdit = (seg: SubtitleSegment, field: 'clean_text' | 'translated_text') => {
    if (disabled) return;
    setEditingIdx(seg.index);
    setEditField(field);
    setEditValue(seg[field] || seg.raw_text);
  };

  const saveEdit = () => {
    if (editingIdx === null || !editField) return;
    onUpdate(editingIdx, { [editField]: editValue });
    setEditingIdx(null);
    setEditField(null);
  };

  const cancelEdit = () => {
    setEditingIdx(null);
    setEditField(null);
  };

  return (
    <div className={`subtitle-table-container ${disabled ? 'editing-disabled' : ''}`}>
      <div className="subtitle-table-header">
        <h3>字幕时间轴与编辑 ({segments.length} 条)</h3>
        <div className="table-header-right">
          <button className={`btn btn-ghost btn-xs ${autoScroll ? '' : 'inactive'}`}
            onClick={() => onAutoScrollChange?.(!autoScroll)}
            title={autoScroll ? '自动滚动已开启' : '自动滚动已关闭'}>
            {autoScroll ? '🔁 自动' : '⏸ 锁定'}
          </button>
          <span className="current-time">⏱ {fmtTime(currentTime)}</span>
        </div>
      </div>
      <div className="subtitle-findbar">
        <input value={searchText} onChange={event => setSearchText(event.target.value)} placeholder="搜索字幕" />
        <input value={replaceText} onChange={event => setReplaceText(event.target.value)} placeholder="替换为" />
        <button disabled={disabled || !searchText} onClick={replaceAll}>全部替换</button>
        {searchText && <span>{visibleSegments.length} 条匹配</span>}
      </div>
      <div className="subtitle-table-scroll" ref={tableRef} onScroll={handleScroll}>
        <table className="subtitle-table">
          <thead>
            <tr>
              <th className="col-idx">#</th>
              <th className="col-time">开始</th>
              <th className="col-time">结束</th>
              <th className="col-text">原文/整理</th>
              <th className="col-text">译文</th>
              <th className="col-lock">🔒</th>
            </tr>
          </thead>
          <tbody>
            {topSpacer > 0 && <tr className="virtual-spacer" aria-hidden="true"><td colSpan={6} style={{ height: topSpacer }}/></tr>}
            {renderedSegments.map(seg => {
              const isActive = seg.index === activeIdx;
              const isEditing = seg.index === editingIdx;
              const displayText = seg.clean_text || seg.raw_text;
              const duration = seg.end - seg.start;
              const charsPerSecond = displayText.length / Math.max(duration, .1);
              const qualityIssue = duration < .35 ? '时长过短' : duration > 8 ? '时长过长' : charsPerSecond > 18 ? '语速过快' : '';
              return (
                <tr key={seg.index}
                  data-idx={seg.index}
                  title={qualityIssue}
                  className={`${isActive ? 'active-row' : ''} ${seg.locked ? 'locked-row' : ''} ${qualityIssue ? 'quality-warning' : ''}`}
                >
                  <td className="col-idx">{seg.index}</td>
                  <td className="col-time" onClick={() => onSeek(seg.start)}>
                    {fmtTime(seg.start)}
                  </td>
                  <td className="col-time" onClick={() => onSeek(seg.end)}>
                    {fmtTime(seg.end)}
                  </td>
                  <td className="col-text editable"
                    onClick={() => !isEditing && startEdit(seg, 'clean_text')}>
                    {isEditing && editField === 'clean_text' ? (
                      <input className="edit-input" autoFocus
                        value={editValue}
                        onChange={e => setEditValue(e.target.value)}
                        onBlur={saveEdit}
                        onKeyDown={e => e.key === 'Enter' ? saveEdit() : e.key === 'Escape' ? cancelEdit() : undefined}
                      />
                    ) : (
                      <span className="text-preview">{displayText.slice(0, 40) || '...'}</span>
                    )}
                  </td>
                  <td className="col-text editable"
                    onClick={() => !isEditing && startEdit(seg, 'translated_text')}>
                    {isEditing && editField === 'translated_text' ? (
                      <input className="edit-input" autoFocus
                        value={editValue}
                        onChange={e => setEditValue(e.target.value)}
                        onBlur={saveEdit}
                        onKeyDown={e => e.key === 'Enter' ? saveEdit() : e.key === 'Escape' ? cancelEdit() : undefined}
                      />
                    ) : (
                      <span className="text-preview">{seg.translated_text.slice(0, 40) || '...'}</span>
                    )}
                  </td>
                  <td className="col-lock">
                    <input type="checkbox" checked={seg.locked} disabled={disabled}
                      onChange={e => onUpdate(seg.index, { locked: e.target.checked })} />
                  </td>
                </tr>
              );
            })}
            {bottomSpacer > 0 && <tr className="virtual-spacer" aria-hidden="true"><td colSpan={6} style={{ height: bottomSpacer }}/></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default App;
