// 字幕工厂 - 主应用组件（集成字幕播放器 + 流程可视化）

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import type {
  Project, SubtitleSegment, TaskStatus, ProcessingConfig,
  ModelSize, ExportFormat,
  ProcessStep, ProcessLogEntry, TaskStepStatus,
  SubtitleStyleSettings, SubtitleStats, AISettings, AIProviderPreset,
  HealthStatus, AppSettings,
  SegmentUpdate, SegmentOperationRequest,
  FailedCleanBatch, PlaylistBatchDetail,
} from './types';
import * as api from './api/backend';
import './App.css';

import SubtitlePlayer, { type PlayerPresentationMode, type SubtitlePlayerHandle } from './components/SubtitlePlayer';
import { loadSubtitleStyle, saveSubtitleStyle } from './subtitleStyle';
import ProcessTimeline from './components/ProcessTimeline';
import ProcessLogViewer from './components/ProcessLogViewer';
import SubtitleStatsPanel from './components/SubtitleStatsPanel';
import SubtitleTimeline from './components/SubtitleTimeline';
import QualityPanel from './components/QualityPanel';
import GlobalTaskDrawer from './components/GlobalTaskDrawer';
import StyleTemplateBar from './components/StyleTemplateBar';
import MediaSelectionPanel from './components/MediaSelectionPanel';
import GlossaryPanel from './components/GlossaryPanel';
import SmartToolsPanel from './components/SmartToolsPanel';
import ProductionCenter from './components/ProductionCenter';
import PlaylistBatchDialog from './components/PlaylistBatchDialog';
import PlaylistBatchGroups from './components/PlaylistBatchGroups';
import SubtitleStylePanel from './components/SubtitleStylePanel';
import SettingsCenter from './components/SettingsCenter';
import LanguagePicker from './components/LanguagePicker';
import AppSelect from './components/AppSelect';
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

function isPlaylistUrl(value: string) {
  try {
    const url = new URL(value);
    return /(^|\.)youtube\.com$/i.test(url.hostname) && !!url.searchParams.get('list');
  } catch { return false; }
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
  const [draftItems, setDraftItems] = useState<Record<number, SegmentUpdate>>({});
  const [editorSaveState, setEditorSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
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
  const [failedCleanBatches, setFailedCleanBatches] = useState<FailedCleanBatch[]>([]);

  // ── UI State ──
  const [currentTime, setCurrentTime] = useState(0);
  const [videoDuration, setVideoDuration] = useState(0);
  const [activeSegmentIdx, setActiveSegmentIdx] = useState(-1);
  const [autoScrollTable, setAutoScrollTable] = useState(true);
  const [showAISettings, setShowAISettings] = useState(false);
  const [showTaskDrawer, setShowTaskDrawer] = useState(false);
  const [showProductionCenter, setShowProductionCenter] = useState(false);
  const [showFirstRunPreflight, setShowFirstRunPreflight] = useState(() => localStorage.getItem('subtitle_factory_preflight_v1') !== 'done');
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
  const [librarySearch, setLibrarySearch] = useState('');
  const [librarySort, setLibrarySort] = useState('updated_desc');
  const [showProjectWorkspace, setShowProjectWorkspace] = useState(false);
  const [projectWorkspace, setProjectWorkspace] = useState<'preview' | 'subtitles' | 'quality' | 'smart' | 'process' | 'style' | 'export'>('preview');
  const [bottomTab, setBottomTab] = useState<'subtitles' | 'style' | 'export' | 'logs'>('subtitles');
  const [inspectorMode, setInspectorMode] = useState<'style' | 'step' | null>(null);
  const [showLinkPopover, setShowLinkPopover] = useState(false);
  const [playlistDialogUrl, setPlaylistDialogUrl] = useState<string | null>(null);
  const [playlistBatches, setPlaylistBatches] = useState<PlaylistBatchDetail[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; project: Project; trashed: boolean } | null>(null);
  const [renameProjectState, setRenameProjectState] = useState<Project | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [removingProjectIds, setRemovingProjectIds] = useState<Set<string>>(() => new Set());
  const [presentationMode, setPresentationMode] = useState<PlayerPresentationMode>('normal');
  const [leftPanelWidth, setLeftPanelWidth] = useState(() => Number(localStorage.getItem('subtitle_factory_left_width')) || 258);
  const [rightPanelWidth, setRightPanelWidth] = useState(() => Number(localStorage.getItem('subtitle_factory_right_width')) || 336);
  const [viewerHeight, setViewerHeight] = useState(() => Number(localStorage.getItem('subtitle_factory_viewer_height')) || 470);
  const [subtitleFocus, setSubtitleFocus] = useState(false);
  const [transcriptionRuntimes, setTranscriptionRuntimes] = useState<Record<string,string>>(() => { try{return JSON.parse(localStorage.getItem('subtitle_factory_transcription_runtimes')||'{}');}catch{return {};}});
  const [collapsedProjectGroups, setCollapsedProjectGroups] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem('subtitle_factory_collapsed_groups') || '[]')); }
    catch { return new Set(); }
  });
  const [collapsedPlaylistBatches, setCollapsedPlaylistBatches] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem('subtitle_factory_collapsed_playlist_batches') || '[]')); }
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
  const ownsWindowFullscreen = useRef(false);
  const editorRevision = useRef(0);
  const editorQueue = useRef<Promise<unknown>>(Promise.resolve());
  const draftItemsRef = useRef<Record<number, SegmentUpdate>>({});
  const styleSaveTimer = useRef<number | null>(null);

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
    if (!(window as any).__TAURI_INTERNALS__) return;
    const appWindow = getCurrentWindow();
    let cancelled = false;

    const syncWindowFullscreen = async () => {
      try {
        const isFullscreen = await appWindow.isFullscreen();
        if (cancelled) return;
        if (presentationMode === 'fullscreen') {
          ownsWindowFullscreen.current = !isFullscreen;
          if (!isFullscreen) await appWindow.setFullscreen(true);
        } else if (ownsWindowFullscreen.current) {
          ownsWindowFullscreen.current = false;
          if (isFullscreen) await appWindow.setFullscreen(false);
        }
      } catch (error) {
        console.error('同步播放器全屏状态失败', error);
      }
    };

    void syncWindowFullscreen();
    return () => { cancelled = true; };
  }, [presentationMode]);

  useEffect(() => {
    if (!(window as any).__TAURI_INTERNALS__) return;
    const appWindow = getCurrentWindow();
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void appWindow.onResized(async () => {
      if (disposed || presentationMode !== 'fullscreen' || !ownsWindowFullscreen.current) return;
      if (!(await appWindow.isFullscreen())) {
        ownsWindowFullscreen.current = false;
        setPresentationMode('normal');
      }
    }).then(fn => { unlisten = fn; });
    return () => { disposed = true; unlisten?.(); };
  }, [presentationMode]);

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
    localStorage.setItem('subtitle_factory_collapsed_playlist_batches', JSON.stringify([...collapsedPlaylistBatches]));
  }, [collapsedPlaylistBatches]);

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
        setPresentationMode(mode => mode === 'theater' ? 'normal' : 'theater');
        return;
      }

      if (event.key.toLowerCase() === 'f' && !isEditing && activeProject?.video_path) {
        event.preventDefault();
        setPresentationMode(mode => mode === 'fullscreen' ? 'normal' : 'fullscreen');
        return;
      }

      if (event.key === 'Escape' && presentationMode !== 'normal' && !document.fullscreenElement && !event.defaultPrevented) {
        setPresentationMode('normal');
        return;
      }

      if (event.key === 'Escape' && !document.fullscreenElement && !event.defaultPrevented) {
        setInspectorMode(null);
        setShowLinkPopover(false);
      }
    };

    window.addEventListener('keydown', handleTheaterShortcut);
    return () => window.removeEventListener('keydown', handleTheaterShortcut);
  }, [activeProject?.video_path, presentationMode]);

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

  const refreshPlaylistBatches = useCallback(async () => {
    if (backendStatus !== 'connected') return;
    const result = await api.getPlaylistBatches();
    setPlaylistBatches(result.batches);
  }, [backendStatus]);

  useEffect(() => {
    if (backendStatus !== 'connected' || libraryView !== 'projects') return;
    const timer = window.setTimeout(() => {
      void api.listProjects({ search: librarySearch.trim(), sort: librarySort, page_size: 200 })
        .then(result => setProjects(result.projects)).catch(() => undefined);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [backendStatus, librarySearch, librarySort, libraryView]);

  useEffect(() => {
    if (backendStatus !== 'connected' || libraryView !== 'projects') return;
    void refreshPlaylistBatches().catch(() => undefined);
    const timer = window.setInterval(() => void refreshPlaylistBatches().catch(() => undefined), 2000);
    return () => window.clearInterval(timer);
  }, [backendStatus, libraryView, refreshPlaylistBatches]);

  const refreshModels = useCallback(() => {
    if (backendStatus !== 'connected') return;
    api.getTranscriptionModels(activeProject?.id, config.language).then(setModelStatus).catch(() => setModelStatus(null));
  }, [activeProject?.id, backendStatus, config.language]);

  const refreshHealth = useCallback(() => {
    api.checkHealth().then(setHealth).catch(() => undefined);
  }, []);

  const refreshLibraries = useCallback(async () => {
    const [active, deleted, batches] = await Promise.all([
      api.listProjects(), api.listProjects({ deleted: true }).catch(() => ({ projects: [] as Project[] })),
      api.getPlaylistBatches().catch(() => ({ batches: [] as PlaylistBatchDetail[] })),
    ]);
    setProjects(active.projects);
    setTrashProjects(deleted.projects);
    setPlaylistBatches(batches.batches);
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
              void api.downloadExport(activeProject?.id || status.project_id || '', d.format || 'mp4');
            }
          }
        }

        if (status.status === 'success' || status.status === 'failed' || status.status === 'cancelled' || status.status === 'partial') {
          setPollInterval(null);
          if (activeProject) {
            api.getSegments(activeProject.id)
              .then(result => setSegments(result.segments))
              .catch(() => {});
            api.getProject(activeProject.id).then(project => { setActiveProject(project); editorRevision.current = Number(project.edit_revision || 0); }).catch(() => {});
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

  useEffect(() => {
    let cancelled = false;
    const shouldLoad = currentTask?.type === 'clean'
      && (currentTask.status === 'partial' || Number(currentTask.details?.failed_batches || 0) > 0);
    if (!shouldLoad || !currentTask) {
      setFailedCleanBatches([]);
      return;
    }
    api.getFailedCleanBatches(currentTask.id)
      .then(result => { if (!cancelled) setFailedCleanBatches(result.batches); })
      .catch(() => { if (!cancelled) setFailedCleanBatches([]); });
    return () => { cancelled = true; };
  }, [currentTask]);

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
    editorRevision.current = Number(p.edit_revision || 0);
    draftItemsRef.current = {};
    setDraftItems({});
    setEditorSaveState('idle');
    setShowProjectWorkspace(true);
    setProjectWorkspace(p.segments_count > 0 ? 'subtitles' : 'preview');
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
    await Promise.all([
      refreshSegments(p.id),
      api.getProjectStyle(p.id).then(result => {
        setSubtitleStyle(result.settings
          ? { ...loadSubtitleStyle(), ...result.settings } as SubtitleStyleSettings
          : loadSubtitleStyle());
      }).catch(() => setSubtitleStyle(loadSubtitleStyle())),
    ]);
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
    if (!activeProject?.id) return;
    let cancelled = false;
    void api.getSegmentDraft(activeProject.id).then(({ draft }) => {
      if (cancelled || !draft?.items.length) return;
      const next = Object.fromEntries(draft.items.map(item => {
        const { index, ...data } = item;
        return [index, data];
      }));
      draftItemsRef.current = next;
      setDraftItems(next);
      if (draft.base_revision !== editorRevision.current) {
        setEditorSaveState('error');
        setToast('检测到旧版字幕草稿；正式字幕已变化，请保存或放弃草稿前先检查内容');
        return;
      }
      setSegments(current => current.map(segment => ({ ...segment, ...(next[segment.index] || {}) })));
      setEditorSaveState('saved');
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [activeProject?.id]);

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

  const runtimeForModel = useCallback((model:string) => transcriptionRuntimes[model]
    || String((appSettings.transcription_runtime_by_model || {})[model] || '')
    || modelStatus?.models.find(item=>item.id===model)?.selected_runtime || '',
  [appSettings.transcription_runtime_by_model,modelStatus,transcriptionRuntimes]);
  const chooseRuntime = useCallback((model:string,runtime:string)=>{
    setTranscriptionRuntimes(current=>{const next={...current,[model]:runtime};localStorage.setItem('subtitle_factory_transcription_runtimes',JSON.stringify(next));return next;});
  },[]);
  const requireRuntime = useCallback((model:string)=>{
    const runtime=runtimeForModel(model); const option=modelStatus?.models.find(item=>item.id===model)?.runtimes?.find(item=>item.id===runtime);
    if(!runtime||!option?.available){setSelectedStep('transcribe');setInspectorMode('step');setToast(!runtime?'请选择转写运行设备：CPU、Apple GPU 或 Core ML':'所选运行设备当前不可用，请重新选择');return '';}
    return runtime;
  },[modelStatus,runtimeForModel]);

  // ── Full pipeline ──
  const handleFullPipeline = useCallback(async () => {
    if (sourceActionLock.current) return;
    sourceActionLock.current = true;
    setTaskStarting(true);
    const model = appSettings.default_workflow === 'manual' ? config.model : compatibleModel();
    try {
      if (!model) return;
      const workflowRuntime=appSettings.default_workflow==='manual'?'':requireRuntime(model);
      if(appSettings.default_workflow!=='manual'&&!workflowRuntime)return;
      const pid = await handleCreateProject();
      if (!pid) return;
      if (youtubeUrl) {
        if (appSettings.default_workflow === 'manual') {
          await startTask('下载视频', 'download', () => api.startDownload(pid, youtubeUrl));
        } else {
          await startTask('自动生成字幕', 'download', () => api.startWorkflow(pid, {
            source_url: youtubeUrl, model, language: config.language, runtime:workflowRuntime,
          }));
        }
      }
    } finally {
      sourceActionLock.current = false;
      setTaskStarting(false);
    }
  }, [appSettings.default_workflow, youtubeUrl, config.language, config.model, compatibleModel, handleCreateProject, requireRuntime, startTask]);

  // ── Import local videos; a project is created only after a real selection. ──
  const importFiles = useCallback(async (files: File[]) => {
    if (importActionLock.current) return;
    const supported = files.filter(file => /\.(mp4|mkv|mov|webm|avi)$/i.test(file.name));
    if (!supported.length) {
      setToast('请选择 MP4、MKV、MOV、WebM 或 AVI 视频');
      return;
    }
    const workflowModel=compatibleModel();
    const workflowRuntime=appSettings.default_workflow==='manual'||!workflowModel?'':requireRuntime(workflowModel);
    if(appSettings.default_workflow!=='manual'&&!workflowRuntime)return;
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
          autostart: appSettings.default_workflow !== 'manual', model: workflowModel||config.model, language: config.language, runtime:workflowRuntime,
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
  }, [addLog, appSettings.default_workflow, compatibleModel, config.language, config.model, config.target_language, ingestTaskLogs, requireRuntime, selectProject, syncProcessFromTask]);

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
    const runtime = requireRuntime(model); if(!runtime)return;
    startTask('转写', 'transcribe', () => api.startTranscribe(activeProject.id, config.language, model, runtime));
  }, [activeProject, compatibleModel, config.language, requireRuntime, startTask]);

  const doGenerateSubtitles = useCallback(() => {
    if (!activeProject) return;
    const model = compatibleModel();
    if (!model) return;
    const runtime=requireRuntime(model);if(!runtime)return;
    setSubtitleStats(null);
    startTask('自动生成字幕', 'transcribe', () => api.startWorkflow(activeProject.id, {
      model, language: config.language, runtime,
    }));
  }, [activeProject, compatibleModel, config.language, requireRuntime, startTask]);

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
      const runtime=requireRuntime(fallback.id);if(!runtime)return;
      if (!window.confirm(`当前转写失败：${currentTask.error || currentTask.message}\n\n是否改用 ${fallback.name} 重试？${detail}`)) return;
      await startTask('备用模型转写', 'transcribe', () => api.retryTranscription(activeProject.id, {
        model: fallback.id, language: config.language, runtime,
      }));
    } catch (error: any) {
      setToast(`无法启动恢复：${error.message}`);
    }
  }, [activeProject, config.language, config.model, currentTask, requireRuntime, startTask]);

  const doClean = useCallback(() => {
    if (!activeProject) return;
    startTask('AI 整理', 'clean', () => api.startClean(activeProject.id, config.clean_target_length));
  }, [activeProject, config.clean_target_length, startTask]);

  const retryFailedCleanBatch = useCallback(async (batchIndex: number) => {
    if (!currentTask || taskStarting || ['running', 'pending', 'paused'].includes(currentTask.status)) return;
    const originalTaskId = currentTask.id;
    setTaskStarting(true);
    try {
      const result = await api.retryFailedCleanBatch(originalTaskId, batchIndex);
      const status = await api.getTaskStatus(result.task_id);
      setCurrentTask(status);
      syncProcessFromTask(status);
      ingestTaskLogs(status);
      setFailedCleanBatches(items => items.filter(item => item.batch_index !== batchIndex));
      setPollInterval(1000);
      setToast(`已启动第 ${batchIndex} 批的单独重试`);
      window.setTimeout(() => setToast(''), 3000);
    } catch (error: any) {
      setToast(`无法重试第 ${batchIndex} 批：${error.message}`);
      window.setTimeout(() => setToast(''), 4500);
    } finally {
      setTaskStarting(false);
    }
  }, [currentTask, ingestTaskLogs, syncProcessFromTask, taskStarting]);

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
          primary_language: subtitleStyle.mode === 'bilingual_translated_first' ? 'translated' : 'original', style: subtitleStyle,
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
          primary_language: subtitleStyle.mode === 'bilingual_translated_first' ? 'translated' : 'original', style: subtitleStyle,
        });
        addLog('info', '导出', `${fmt.toUpperCase()} 导出成功`);
        setStepStatus('export', 'success', 100);
        await api.downloadExport(activeProject.id, fmt);
      }
    } catch (e: any) {
      addLog('error', '导出', `${fmt} 导出失败: ${e.message}`);
      setStepStatus('export', 'failed', 0, e.message, '检查文件权限和 ffmpeg');
    } finally {
      exportActionLock.current = false;
      setTaskStarting(false);
    }
  }, [activeProject, config.bilingual, subtitleStyle, addLog, ingestTaskLogs, setStepStatus]);

  const acceptEditorResult = useCallback((projectId: string, result: Awaited<ReturnType<typeof api.applySegmentOperation>>) => {
    editorRevision.current = result.revision;
    setSegments(result.segments);
    setActiveProject(current => current?.id === projectId
      ? { ...current, edit_revision: result.revision, segments_count: result.segments.length }
      : current);
    setEditorSaveState('saved');
  }, []);

  const runEditorOperation = useCallback((data: Omit<SegmentOperationRequest, 'expected_revision'>) => {
    if (!activeProject) return Promise.reject(new Error('请先选择项目'));
    const projectId = activeProject.id;
    const execute = async () => {
      setEditorSaveState('saving');
      try {
        const result = await api.applySegmentOperation(projectId, {
          ...data, expected_revision: editorRevision.current,
        });
        acceptEditorResult(projectId, result);
        return result;
      } catch (error: any) {
        setEditorSaveState('error');
        addLog('error', '编辑', `字幕操作失败: ${error.message}`);
        if (error.code === 'EDIT_REVISION_CONFLICT') {
          const [project, latest] = await Promise.all([
            api.getProject(projectId), api.getSegments(projectId),
          ]);
          editorRevision.current = Number(project.edit_revision || 0);
          setActiveProject(project);
          setSegments(latest.segments);
          setToast('字幕已刷新，请重新执行刚才的操作');
        }
        throw error;
      }
    };
    const queued = editorQueue.current.then(execute, execute);
    editorQueue.current = queued.then(() => undefined, () => undefined);
    return queued;
  }, [acceptEditorResult, activeProject, addLog]);

  // ── Update segment / persist manual drafts ──
  const handleUpdateSegment = useCallback(async (idx: number, data: SegmentUpdate) => {
    if (!activeProject) return;
    const manualDraft = appSettings.auto_save === false || Object.keys(draftItemsRef.current).length > 0;
    if (manualDraft) {
      const next = { ...draftItemsRef.current, [idx]: { ...(draftItemsRef.current[idx] || {}), ...data } };
      draftItemsRef.current = next;
      setDraftItems(next);
      setSegments(current => current.map(segment => segment.index === idx ? { ...segment, ...data } : segment));
      setEditorSaveState('saving');
      try {
        await api.saveSegmentDraft(activeProject.id, editorRevision.current,
          Object.entries(next).map(([index, value]) => ({ index: Number(index), ...value })));
        setEditorSaveState('saved');
      } catch (error: any) {
        setEditorSaveState('error');
        addLog('error', '编辑', `草稿保存失败: ${error.message}`);
      }
      return;
    }
    setSegments(current => current.map(segment => segment.index === idx ? { ...segment, ...data } : segment));
    await runEditorOperation({
      operation: 'update_many', items: [{ index: idx, ...data }],
      include_locked: data.locked !== undefined,
    }).catch(() => undefined);
  }, [activeProject, appSettings.auto_save, addLog, runEditorOperation]);

  const commitDraft = useCallback(async () => {
    if (!activeProject || !Object.keys(draftItemsRef.current).length) return;
    setEditorSaveState('saving');
    try {
      const result = await api.commitSegmentDraft(activeProject.id);
      draftItemsRef.current = {};
      setDraftItems({});
      acceptEditorResult(activeProject.id, result);
      setToast('字幕草稿已保存');
    } catch (error: any) {
      setEditorSaveState('error');
      setToast(error.message);
    }
  }, [acceptEditorResult, activeProject]);

  const discardDraft = useCallback(async () => {
    if (!activeProject) return;
    await api.discardSegmentDraft(activeProject.id);
    draftItemsRef.current = {};
    setDraftItems({});
    await refreshSegments(activeProject.id);
    setEditorSaveState('idle');
    setToast('字幕草稿已放弃');
  }, [activeProject, refreshSegments]);

  const replaceSegments = useCallback(async (
    search: string, replacement: string, fields: Array<'clean_text' | 'translated_text'>,
    options: { matchCase: boolean; includeLocked: boolean },
  ) => {
    await runEditorOperation({
      operation: 'replace', search, replacement, fields, match_case: options.matchCase, include_locked: options.includeLocked,
    });
  }, [runEditorOperation]);

  const splitSegment = useCallback(async (index: number, splitAt: number) => {
    await runEditorOperation({ operation: 'split', split_index: index, split_at: splitAt });
  }, [runEditorOperation]);

  const mergeSegments = useCallback(async (indices: number[]) => {
    await runEditorOperation({ operation: 'merge', indices });
  }, [runEditorOperation]);

  const undoEditor = useCallback(async () => {
    if (!activeProject) return;
    try {
      const result = await api.undoEditorOperation(activeProject.id, editorRevision.current);
      acceptEditorResult(activeProject.id, result);
    } catch (error: any) { setToast(error.message); }
  }, [acceptEditorResult, activeProject]);

  const redoEditor = useCallback(async () => {
    if (!activeProject) return;
    try {
      const result = await api.redoEditorOperation(activeProject.id, editorRevision.current);
      acceptEditorResult(activeProject.id, result);
    } catch (error: any) { setToast(error.message); }
  }, [acceptEditorResult, activeProject]);

  const importSubtitleFile = useCallback(() => {
    if (!activeProject) return;
    const input = document.createElement('input');
    input.type = 'file'; input.accept = '.srt,.vtt,.ass';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        const result = await api.importSubtitleFile(activeProject.id, file, editorRevision.current);
        acceptEditorResult(activeProject.id, result);
        setToast(`已导入 ${result.affected_count} 条字幕，可使用撤销恢复`);
      } catch (error: any) { setToast(error.message); }
    };
    input.click();
  }, [acceptEditorResult, activeProject]);

  const exportProjectPackage = useCallback(async (includeMedia: boolean) => {
    if (!activeProject) return;
    try {
      const result = await api.createProjectPackage(activeProject.id, includeMedia);
      await api.downloadProjectPackage(result.package_id, result.filename);
      setToast(includeMedia ? '完整项目包已导出' : '精简项目包已导出');
    } catch (error: any) { setToast(error.message); }
  }, [activeProject]);

  const importProjectPackage = useCallback(() => {
    const input = document.createElement('input'); input.type = 'file'; input.accept = '.sfproject';
    input.onchange = async () => {
      const file = input.files?.[0]; if (!file) return;
      try {
        const result = await api.importProjectPackage(file);
        const listing = await api.listProjects(); setProjects(listing.projects);
        setToast(result.media_status === 'relink_required' ? '项目已导入，需要重新关联媒体' : '项目已完整导入');
      } catch (error: any) { setToast(error.message); }
    };
    input.click();
  }, []);

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
    if (!activeProject) return;
    if (styleSaveTimer.current !== null) window.clearTimeout(styleSaveTimer.current);
    const projectId = activeProject.id;
    styleSaveTimer.current = window.setTimeout(() => {
      styleSaveTimer.current = null;
      void api.saveProjectStyle(projectId, style as unknown as Record<string, unknown>)
        .catch(error => {
          setToast(`样式保存失败：${error.message}`);
          window.setTimeout(() => setToast(''), 3000);
        });
    }, 350);
  }, [activeProject]);

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

  const togglePlaylistBatch = useCallback((id: string) => {
    setCollapsedPlaylistBatches(current => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
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
    setShowProjectWorkspace(false);
    setProjectWorkspace('preview');
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
    setProjectWorkspace('process');
    setShowProjectWorkspace(true);
    setInspectorMode(null);
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
  const inspectorModelId=config.model==='auto'?(modelStatus?.recommended_model||'small'):config.model;
  const inspectorModel=modelStatus?.models.find(item=>item.id===inspectorModelId);
  const modelOptions=[{value:'auto',label:'自动选择',description:`推荐 ${modelStatus?.models.find(item=>item.id===modelStatus.recommended_model)?.name||'Whisper Small'}`},...(modelStatus?.models||[]).map(item=>({value:item.id,label:item.name,description:[item.version,item.format,item.source].filter(Boolean).join(' · ')}))];
  const playlistWorkflow = {
    model: inspectorModelId,
    runtime: runtimeForModel(inspectorModelId),
    language: config.language,
    target_language: config.target_language,
    clean_target_length: config.clean_target_length,
  };

  const primaryActionLabel = !activeProject ? '生成字幕'
    : currentTask?.status === 'paused' ? '继续'
      : currentTask?.status === 'failed' ? '重试'
        : !hasSegments ? '生成字幕' : '继续';

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

  const activeProcessStep = selectedStep || (!hasAudio ? 'download' : !hasSegments ? 'transcribe' : 'clean');
  const renderFailedBatchRecovery = () => failedCleanBatches.length > 0 && <section className="failed-batch-recovery" aria-label="失败批次恢复">
    <header><div><strong>有 {failedCleanBatches.length} 个批次需要重试</strong><small>只会重新整理对应时间范围，不会重跑整份字幕。</small></div></header>
    <div className="failed-batch-list">{failedCleanBatches.map(batch => <article key={batch.batch_index}>
      <div><strong>第 {batch.batch_index} 批 · {batch.segment_count} 条</strong><small>{batch.start === null || batch.end === null ? '时间范围不可用' : `${batch.start.toFixed(1)}s – ${batch.end.toFixed(1)}s`} · 已尝试 {batch.attempts} 次</small><p title={batch.error}>{batch.error || 'AI 未返回有效 JSON'}</p></div>
      <button type="button" disabled={taskStarting || isProcessing} onClick={() => void retryFailedCleanBatch(batch.batch_index)}>只重试这一批</button>
    </article>)}</div>
  </section>;
  const renderProcessSettings = () => <div className="process-settings-content">
    {activeProcessStep === 'download' && <section className="inspector-section"><h3>下载与音频</h3><label>项目链接<input value={activeProject?.source_url || youtubeUrl} placeholder="YouTube URL" onChange={event => setYoutubeUrl(event.target.value)}/></label><div className="runtime-mini"><span className={health?.runtime?.ffmpeg?.ok ? 'ok' : 'error'}>FFmpeg {health?.runtime?.ffmpeg?.ok ? '可用' : '需检查'}</span><span className={health?.runtime?.yt_dlp?.ok ? 'ok' : 'error'}>yt-dlp {health?.runtime?.yt_dlp?.ok ? '可用' : '需检查'}</span></div>{activeProject?.video_path && <MediaSelectionPanel projectId={activeProject.id} onChanged={() => { setActiveProject(current => current ? { ...current, audio_path: null } : current); setToast('音轨或范围已更新，请重新提取音频'); }}/>}<p>下载会自动移除播放定位参数，并保留完整源视频。失败时可在这里重试。</p><button className="button primary" disabled={!activeProject?.source_url || isProcessing} onClick={retryDownload}>重新下载</button>{activeProject?.video_path && <button className="button secondary" disabled={isProcessing} onClick={doExtractAudio}>重新提取音频</button>}</section>}
    {activeProcessStep === 'transcribe' && <section className="inspector-section transcription-inspector"><h3>语音转写</h3><label>转写模型<AppSelect value={config.model} onChange={model=>setConfig({...config,model:model as ModelSize})} options={modelOptions} label="转写模型" searchable/></label><div className="runtime-picker"><header><strong>运行设备</strong><small>{runtimeForModel(inspectorModelId)?'已为此模型记住':'首次使用必须选择'}</small></header><div className="runtime-choice-grid">{inspectorModel?.runtimes?.map(runtime=><button type="button" key={runtime.id} disabled={!runtime.available} className={runtimeForModel(inspectorModelId)===runtime.id?'selected':''} onClick={()=>chooseRuntime(inspectorModelId,runtime.id)}><i>{runtime.id==='cpu'?'CPU':runtime.id==='mlx'?'GPU':runtime.id==='coreml'?'ANE':'ML'}</i><span><strong>{runtime.name}</strong><small>{runtime.engine}</small>{!runtime.available&&<em>{runtime.reason}</em>}</span>{runtimeForModel(inspectorModelId)===runtime.id&&<b>✓</b>}</button>)}</div>{!inspectorModel?.runtimes?.length&&<p className="runtime-empty">正在读取此模型支持的运行设备…</p>}</div><label>源语言<LanguagePicker value={config.language} onChange={language => setConfig({ ...config, language })}/></label>{modelStatus && <div className="model-readiness"><strong>{inspectorModel?.name||inspectorModelId}</strong><small>{inspectorModel?.ready?'模型已就绪':inspectorModel?.download_required?'首次运行时下载到 App 数据目录':'模型不可用'}</small></div>}<button className="button primary" disabled={!hasAudio || isProcessing || !runtimeForModel(inspectorModelId)} onClick={doTranscribe}>开始转写</button>{!runtimeForModel(inspectorModelId)&&<p className="runtime-required">请选择上方运行设备后再开始转写。</p>}</section>}
    {activeProcessStep === 'clean' && <section className="inspector-section"><h3>AI 忠实整理</h3><div className="ai-summary-row"><span className="ai-logo">✦</span><div><strong>{activeAIPreset?.name || aiSettings?.provider || '未配置 AI'}</strong><small>{aiSettings?.model || '请先打开设置中心'}</small></div></div><label>参考单句长度 <span>{config.clean_target_length} 字</span><input type="range" min={16} max={100} step={2} value={config.clean_target_length} onChange={event => setConfig({ ...config, clean_target_length: Number(event.target.value) })}/></label><p>只修正明显错词、标点和断句，不改变原意。</p><button className="button primary" disabled={!hasSegments || isProcessing || !aiSettings?.has_api_key} onClick={doClean}>确认并开始整理</button><button className="button secondary" disabled={!hasSegments || isProcessing} onClick={undoClean}>撤销上次整理</button></section>}
    {activeProcessStep === 'clean' && renderFailedBatchRecovery()}
    {activeProcessStep === 'translate' && <section className="inspector-section"><h3>AI 翻译</h3><label>目标语言<LanguagePicker mode="target" allowCustom allowNone value={config.target_language} onChange={target_language => setConfig({ ...config, target_language })}/></label><label className="check-row"><input type="checkbox" checked={config.bilingual} onChange={event => setConfig({ ...config, bilingual: event.target.checked })}/> 导出时包含原文与译文</label><p>翻译结果会单独保存，可继续逐句校对。</p><button className="button primary" disabled={!hasSegments || isProcessing || !aiSettings?.has_api_key || config.target_language === 'none'} onClick={doTranslate}>确认并开始翻译</button></section>}
    {activeProcessStep === 'export' && <section className="inspector-section"><h3>快速导出</h3><p>字幕文件立即生成；带字幕视频会在后台压制。</p><button className="button primary" onClick={() => setProjectWorkspace('export')}>前往导出工作区</button></section>}
    {currentTask?.status === 'failed' && <section className="recovery-card"><strong>{currentTask.error_code || '任务失败'}</strong><span>{currentTask.error || currentTask.message}</span>{currentTask.suggestion && <small>{currentTask.suggestion}</small>}{currentTask.recoverable && <button onClick={recoverTranscription}>使用备用模型重试</button>}</section>}
  </div>;

  return (
    <div className={`app pro-app theme-${theme} density-${density} ${motionEnabled ? '' : 'motion-off'} presentation-${presentationMode} ${showProjectWorkspace && activeProject ? 'workspace-active' : 'library-home'}`}
      onDragEnter={event => { event.preventDefault(); setDragActive(true); }}
      onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
      onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragActive(false); }}
      onDrop={event => { event.preventDefault(); setDragActive(false); void importFiles(Array.from(event.dataTransfer.files)); }}>
      <header className="studio-topbar" data-tauri-drag-region>
        {showProjectWorkspace && activeProject ? <button className="topbar-back" aria-label="返回项目库" onClick={() => setShowProjectWorkspace(false)}><span aria-hidden="true">‹</span> 项目库</button> : <span className="topbar-spacer"/>}
        <div className="brand-block" data-tauri-drag-region>
          <img className="brand-mark" src={appIcon} alt=""/><strong data-tauri-drag-region>字幕工厂</strong>
        </div>
        <div className="active-project-title" data-tauri-drag-region>
          <strong data-tauri-drag-region>{showProjectWorkspace && activeProject ? activeProject.title : '项目库'}</strong>
          <span data-tauri-drag-region>{showProjectWorkspace && activeProject ? `${segments.length} 条字幕 · ${languageLabel(config.language)}` : '本地优先的专业字幕工作台'}</span>
        </div>
        <div className="topbar-actions">
          <button className="topbar-button" disabled={backendStatus !== 'connected'} onClick={handleImportLocal}><span>＋</span>导入</button>
          <button className={`topbar-button ${showLinkPopover ? 'active' : ''}`} disabled={backendStatus !== 'connected'} onClick={() => setShowLinkPopover(value => !value)}><span>⌁</span>链接</button>
          <button className={`task-status-pill ${backendStatus}`} onClick={() => setShowTaskDrawer(value => !value)} aria-expanded={showTaskDrawer}>
            <i className={`backend-dot ${backendStatus}`}/><span>{isProcessing ? `${currentTask?.message || '正在处理'} · ${Math.round(currentTask?.progress || 0)}%` : backendStatus === 'connected' ? '引擎就绪' : backendStatus === 'connecting' ? '正在启动' : '引擎异常'}</span>
          </button>
          <button className="icon-action" aria-label={theme === 'dark' ? '切换浅色模式' : '切换深色模式'} onClick={() => setTheme(value => value === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☀︎' : '◐'}</button>
          <button className="icon-action" aria-label="打开设置" onClick={() => setShowAISettings(true)}><img className="topbar-control-icon" src={settingsIcon} alt=""/></button>
        </div>
        {showLinkPopover && <div className="link-popover">
          <div><strong>从 YouTube 链接创建</strong><button aria-label="关闭" onClick={() => setShowLinkPopover(false)}>×</button></div>
          <input autoFocus type="url" value={youtubeUrl} placeholder="https://www.youtube.com/watch?v=…" onChange={event => setYoutubeUrl(event.target.value)}/>
          <p>{isPlaylistUrl(youtubeUrl) ? '将先读取播放列表，确认批量转写与 AI 流水线后创建归组。' : '播放定位参数会自动移除并下载完整视频。'}</p>
          <button className="button primary" disabled={!youtubeUrl || backendStatus !== 'connected'} onClick={() => {
            setShowLinkPopover(false);
            if (isPlaylistUrl(youtubeUrl)) setPlaylistDialogUrl(youtubeUrl); else void handleFullPipeline();
          }}>{isPlaylistUrl(youtubeUrl) ? '解析播放列表' : '下载并生成字幕'}</button>
        </div>}
      </header>
      <GlobalTaskDrawer open={showTaskDrawer} onClose={() => setShowTaskDrawer(false)} onOpenProject={projectId => {
        const project = projects.find(item => item.id === projectId)
          || playlistBatches.flatMap(batch => batch.items).find(item => item.project_id === projectId)?.project;
        if (project) void selectProject(project); else void api.getProject(projectId).then(selectProject).catch(() => undefined);
        setShowTaskDrawer(false);
      }}/>
      {playlistDialogUrl && <PlaylistBatchDialog
        url={playlistDialogUrl} workflow={playlistWorkflow} appSettings={appSettings} health={health}
        aiReady={!!aiSettings?.has_api_key} onClose={() => setPlaylistDialogUrl(null)}
        onCreated={message => { setPlaylistDialogUrl(null); showToast(message, 4200); void refreshPlaylistBatches(); }}
      />}
      {showProductionCenter && (() => { const batchModel = compatibleModel() || config.model; return <ProductionCenter workflow={{ model: batchModel, language: config.language, target_language: config.target_language, runtime: runtimeForModel(batchModel) }} onClose={() => setShowProductionCenter(false)} onProjectsCreated={() => void api.listProjects({ search: librarySearch, sort: librarySort }).then(result => setProjects(result.projects))} onShowTasks={() => setShowTaskDrawer(true)}/>; })()}
      {showFirstRunPreflight && backendStatus === 'connected' && modelStatus && (() => {
        const modelId = String(appSettings.default_model || modelStatus.recommended_model || 'small');
        const model = modelStatus.models.find(item => item.id === modelId) || modelStatus.models[0];
        const recommended = model?.runtimes?.find(item => item.available && item.id !== 'cpu') || model?.runtimes?.find(item => item.available);
        return <div className="preflight-overlay" role="dialog" aria-modal="true" aria-label="首次运行预检"><section className="preflight-card"><header><small>首次运行</small><h2>本机准备就绪</h2><p>基础转写在本机完成；AI、OCR 和说话人模型只在首次使用对应功能时准备。</p></header><div className="preflight-checks"><span className={health?.runtime?.ffmpeg?.ok ? 'ok' : 'warning'}><i>{health?.runtime?.ffmpeg?.ok ? '✓' : '!'}</i><strong>FFmpeg</strong><small>{health?.runtime?.ffmpeg?.ok ? '可用' : '需要在设置中检查'}</small></span><span className={health?.runtime?.disk?.ok ? 'ok' : 'warning'}><i>{health?.runtime?.disk?.ok ? '✓' : '!'}</i><strong>磁盘</strong><small>{health?.runtime?.disk?.message || '已检查可用空间'}</small></span><span className={model?.ready ? 'ok' : 'warning'}><i>{model?.ready ? '✓' : '↓'}</i><strong>{model?.name || '默认模型'}</strong><small>{model?.ready ? '已就绪' : '首次转写时按需下载'}</small></span></div><div className="preflight-device"><strong>推荐运行设备</strong><span>{recommended?.name || 'CPU'}<small>{recommended?.engine || '本地运行'}</small></span></div><footer><button className="button primary" disabled={!recommended} onClick={() => { if (model && recommended) chooseRuntime(model.id, recommended.id); localStorage.setItem('subtitle_factory_preflight_v1', 'done'); setShowFirstRunPreflight(false); }}>确认并开始使用</button></footer></section></div>;
      })()}

      {backendStatus === 'error' && <div className="engine-error-banner"><strong>本地引擎未能启动</strong><span>打开设置查看 FFmpeg、yt-dlp、模型与存储诊断。</span><button onClick={refreshHealth}>重新检查</button></div>}
      {uploadProgress !== null && <div className="upload-progress-banner" role="status"><span>正在导入视频</span><progress value={uploadProgress} max={100}/><strong>{uploadProgress}%</strong></div>}

      <div className={`studio-shell v05-shell ${showProjectWorkspace && activeProject ? `app-project project-view-${projectWorkspace}` : 'app-library'} ${inspectorMode ? 'inspector-open' : ''}`} style={{
        '--left-panel-width': `${leftPanelWidth}px`, '--right-panel-width': `${rightPanelWidth}px`,
      } as React.CSSProperties}>
        <aside className="project-sidebar">
          <header className="library-page-header"><div><small>字幕工厂</small><h1>你的项目</h1><p>选择一个项目继续工作，或从视频和链接开始新的字幕任务。</p></div><div><button className="button secondary" disabled={backendStatus !== 'connected'} onClick={() => setShowLinkPopover(true)}>添加链接</button><button className="button primary" disabled={backendStatus !== 'connected'} onClick={handleImportLocal}>导入视频</button></div></header>
          <div className="library-switcher" role="tablist" aria-label="项目库视图">
            <button role="tab" aria-selected={libraryView === 'projects'} className={libraryView === 'projects' ? 'active' : ''} onClick={() => setLibraryView('projects')}>项目 <span>{projects.length}</span></button>
            <button role="tab" aria-selected={libraryView === 'trash'} className={libraryView === 'trash' ? 'active' : ''} onClick={() => setLibraryView('trash')}>回收站 <span>{trashProjects.length}</span></button>
          </div>
          {libraryView === 'projects' && <div className="library-filters"><input type="search" value={librarySearch} onChange={event => setLibrarySearch(event.target.value)} placeholder="搜索项目名称" aria-label="搜索项目"/><select aria-label="项目排序" value={librarySort} onChange={event => setLibrarySort(event.target.value)}><option value="updated_desc">最近更新</option><option value="created_desc">最近创建</option><option value="name_asc">名称 A–Z</option><option value="name_desc">名称 Z–A</option></select></div>}
          <div className="project-list">
            {libraryView === 'projects' && <PlaylistBatchGroups
              batches={playlistBatches} search={librarySearch} collapsed={collapsedPlaylistBatches}
              workflow={playlistWorkflow} onToggle={togglePlaylistBatch}
              onOpenProject={project => void selectProject(project)}
              onChanged={() => void refreshPlaylistBatches()} onMessage={showToast}
            />}
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
                      <span className="project-card-copy"><strong>{project.title}</strong><small>{languageLabel(project.language)} · {project.segments_count} 条 · {project.created_at.slice(0, 10)}</small>{project.latest_task_status && <em className={`project-task-hint ${project.latest_task_status}`}>{project.latest_task_status === 'failed' ? `失败 · ${project.latest_task_message || '可重试'}` : project.latest_task_status === 'running' ? project.latest_task_message || '正在处理' : project.latest_task_status === 'pending' ? '排队中' : project.latest_task_status === 'success' ? '最近任务已完成' : project.latest_task_status}</em>}</span>
                      <span className="project-more" aria-hidden="true">•••</span>
                    </button>
                    {editingGroup && <div className="project-group-editor"><AppSelect value={groupDraft} onChange={setGroupDraft} label="项目分组" placeholder="搜索或输入分组名称" searchable allowCustom options={knownProjectGroups.map(name=>({value:name,label:name}))}/><button onClick={() => void saveProjectGroup(project)}>保存</button><button aria-label="取消" onClick={() => setGroupEditorProjectId(null)}>×</button></div>}
                  </div>;
                })}</div>}
              </section>;
            })}
            {libraryView === 'projects' && backendStatus === 'connecting' && !projects.length && !playlistBatches.length && <div className="library-skeleton" aria-label="正在载入项目"><i/><i/><i/></div>}
            {libraryView === 'projects' && backendStatus !== 'connecting' && !projects.length && !playlistBatches.length && <div className="project-empty"><span>▱</span><strong>还没有项目</strong><small>导入视频、拖放文件或粘贴链接开始。</small></div>}
            {libraryView === 'trash' && trashProjects.map(project => <button className={`project-card trash-card ${removingProjectIds.has(project.id) ? 'removing' : ''}`} key={project.id} onContextMenu={event => openProjectMenu(event, project, true)} onClick={event => openProjectMenu(event, project, true)}>
              <span className="project-thumb"><span className="project-thumb-fallback">♲</span></span><span className="project-card-copy"><strong>{project.title}</strong><small>{project.deleted_at?.slice(0, 10) || '已删除'} · 媒体仍保留</small></span><span className="project-more">•••</span>
            </button>)}
            {libraryView === 'trash' && !trashProjects.length && <div className="project-empty"><span>♲</span><strong>回收站为空</strong><small>移入回收站的项目会保留媒体与字幕。</small></div>}
          </div>
          <div className="sidebar-footer">
            {libraryView === 'trash' ? <button className="sidebar-action danger" disabled={!trashProjects.length} onClick={clearTrash}>清空回收站</button> : <><button className="sidebar-action" onClick={handleImportLocal}>＋ 导入视频</button><button className="sidebar-action" onClick={() => setShowProductionCenter(true)}>▦ 批量与监听</button><button className="sidebar-action" onClick={importProjectPackage}>⇧ 导入项目包</button><button className="sidebar-action" onClick={() => setShowLinkPopover(true)}>⌁ 添加链接</button></>}
          </div>
        </aside>

        <div className="panel-resizer panel-resizer-left" role="separator" aria-label="调整项目库宽度" tabIndex={0} onPointerDown={event => beginResize('left', event)} onKeyDown={event => { if (event.key === 'ArrowLeft') setLeftPanelWidth(value => Math.max(210, value - 16)); if (event.key === 'ArrowRight') setLeftPanelWidth(value => Math.min(430, value + 16)); }}/>

        <main className={`editor-workspace ${subtitleFocus ? 'subtitle-focus' : ''}`}>
          <nav className="project-workspace-nav" aria-label="项目工作区">
            {([['preview', '预览', '播放与检查'], ['subtitles', '字幕', `${segments.length} 条`], ['quality', '质检', '时间与术语'], ['smart', '智能', '说话人与 OCR'], ['process', '处理', `${totalProgress}%`], ['style', '样式', '外观与位置'], ['export', '导出', '文件与视频']] as const).map(([id, label, detail]) => <button key={id} className={projectWorkspace === id ? 'active' : ''} aria-current={projectWorkspace === id ? 'page' : undefined} onClick={() => { setProjectWorkspace(id); setInspectorMode(null); }}><span>{label}</span><small>{detail}</small></button>)}
          </nav>
          <header className="workspace-page-heading">
            <div><small>{activeProject?.title}</small><h1>{projectWorkspace === 'preview' ? '视频预览' : projectWorkspace === 'subtitles' ? '字幕编辑' : projectWorkspace === 'quality' ? '字幕质检' : projectWorkspace === 'smart' ? '智能工具' : projectWorkspace === 'process' ? '处理流程' : projectWorkspace === 'style' ? '字幕样式' : '导出交付'}</h1></div>
            {isProcessing && <div className="page-task-status"><i/><span>{currentTask?.message || '正在处理'}</span><strong>{Math.round(currentTask?.progress || 0)}%</strong></div>}
          </header>
          <div className="workbench-split" style={{ '--viewer-height': `${viewerHeight}px` } as React.CSSProperties}>
          <section className="media-workspace">
          <section className="fixed-viewer">
            {activeProject?.video_path && activeProject.video_url ? <SubtitlePlayer ref={videoPlayerRef} videoUrl={api.getBackendMediaUrl(activeProject.video_url) || ''} segments={segments} style={subtitleStyle} activeIdx={activeSegmentIdx} onTimeUpdate={handleTimeUpdate} onDurationChange={setVideoDuration} onStyleChange={handleStyleChange} presentationMode={presentationMode} onPresentationModeChange={setPresentationMode}/>
              : <div className="viewer-welcome"><span>▶</span><h2>开始创作字幕</h2><p>导入视频或粘贴 YouTube 链接</p><div><button className="button primary" onClick={handleImportLocal}>导入视频</button><button className="button secondary" onClick={() => setShowLinkPopover(true)}>添加链接</button></div></div>}
          </section>
          {activeProject && <SubtitleTimeline projectId={activeProject.id} segments={segments} currentTime={currentTime} duration={videoDuration} onSeek={handleSeek} onUpdateTime={(index, update) => void handleUpdateSegment(index, update)}/>}
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

          </section>
          <section className="lower-workspace">
            <nav className="workspace-tabs" role="tablist" aria-label="项目工作区">
              {([['subtitles', '字幕'], ['style', '样式'], ['export', '导出'], ['logs', '日志']] as const).map(([id, label]) => <button key={id} role="tab" aria-selected={bottomTab === id} className={bottomTab === id ? 'active' : ''} onClick={() => { setBottomTab(id); if (id === 'style') setInspectorMode('style'); }}>{label}{id === 'subtitles' && <span>{segments.length}</span>}{id === 'logs' && processLogs.length > 0 && <span>{processLogs.length}</span>}</button>)}
              <button className="focus-subtitles" onClick={() => setSubtitleFocus(value => !value)}>{subtitleFocus ? '显示播放器' : '专注字幕'}</button>
            </nav>
            {bottomTab === 'subtitles' && <div className="subtitle-language-bar"><span>目标语言</span><LanguagePicker mode="target" allowCustom allowNone value={config.target_language} onChange={target_language => { setConfig({ ...config, target_language }); if (activeProject) void api.updateProjectTargetLanguage(activeProject.id, target_language).then(setActiveProject); }}/><button className="button primary" disabled={!hasSegments || isProcessing || config.target_language === 'none'} onClick={doTranslate}>开始翻译</button></div>}
            <div className="workspace-tab-content" key={bottomTab}>
              {bottomTab === 'subtitles' && (activeProject ? <SubtitleTable segments={segments} currentTime={currentTime} activeIdx={activeSegmentIndex} onSeek={handleSeek} onUpdate={handleUpdateSegment} onReplaceAll={replaceSegments} onSplit={splitSegment} onMerge={mergeSegments} onUndo={undoEditor} onRedo={redoEditor} saveState={editorSaveState} draftCount={Object.keys(draftItems).length} onCommitDraft={commitDraft} onDiscardDraft={discardDraft} onAutoScrollChange={setAutoScrollTable} autoScroll={autoScrollTable} disabled={isProcessing}/> : <div className="transcript-empty">选择项目后，字幕会在这里按时间排列并可直接编辑。</div>)}
              {bottomTab === 'style' && <div className="style-overview"><div className="style-preview-card" style={{ fontFamily: subtitleStyle.fontFamily }}><span style={{ color: subtitleStyle.originalTextColor, fontSize: Math.min(24, subtitleStyle.originalFontSize) }}>为每一句话找到恰好的位置。</span><small style={{ color: subtitleStyle.translatedTextColor }}>Give every line its perfect place.</small></div><div><h3>字幕样式</h3><p>调整字体、字号、双语顺序、颜色、背景与垂直位置。更改会立即显示在播放器中。</p><button className="button primary" onClick={() => setInspectorMode('style')}>打开样式检查器</button></div></div>}
              {bottomTab === 'export' && <div className="export-workspace"><header><div><h3>导出项目</h3><p>字幕文件会立即下载；视频导出将在后台压制。</p></div><label><input type="checkbox" checked={config.bilingual} onChange={event => setConfig({ ...config, bilingual: event.target.checked })}/> 包含双语</label></header><div className="export-cards">{(['srt', 'vtt', 'ass', 'srt-bilingual', 'mp4', 'mkv'] as ExportFormat[]).map(format => <button key={format} disabled={!hasSegments || isProcessing} onClick={() => void doExport(format)}><strong>{format === 'srt-bilingual' ? '双语 SRT' : format.toUpperCase()}</strong><small>{format === 'mp4' || format === 'mkv' ? '带字幕视频' : '字幕文件'}</small><span>↗</span></button>)}</div></div>}
              {bottomTab === 'logs' && <div className="logs-workspace"><ProcessLogViewer logs={processLogs} collapsed={false} onToggle={() => undefined} onClear={() => setProcessLogs([])}/></div>}
            </div>
          </section>
          {projectWorkspace === 'subtitles' && <section className="task-page subtitle-task-page">
            <header className="task-page-toolbar"><div><h2>逐句校对</h2><p>播放器不再挤占编辑空间；点击时间码可跳回预览页核对画面。</p></div><div className="toolbar-cluster"><label>目标语言<LanguagePicker mode="target" allowCustom allowNone value={config.target_language} onChange={target_language => { setConfig({ ...config, target_language }); if (activeProject) void api.updateProjectTargetLanguage(activeProject.id, target_language).then(setActiveProject); }}/></label><button className="button secondary" onClick={importSubtitleFile}>导入字幕</button><button className="button secondary" onClick={() => setProjectWorkspace('preview')}>打开预览</button><button className="button primary" disabled={!hasSegments || isProcessing || config.target_language === 'none'} onClick={doTranslate}>开始翻译</button></div></header>
            <div className="subtitle-page-table"><SubtitleTable segments={segments} currentTime={currentTime} activeIdx={activeSegmentIndex} onSeek={time => { handleSeek(time); setProjectWorkspace('preview'); }} onUpdate={handleUpdateSegment} onReplaceAll={replaceSegments} onSplit={splitSegment} onMerge={mergeSegments} onUndo={undoEditor} onRedo={redoEditor} saveState={editorSaveState} draftCount={Object.keys(draftItems).length} onCommitDraft={commitDraft} onDiscardDraft={discardDraft} onAutoScrollChange={setAutoScrollTable} autoScroll={autoScrollTable} disabled={isProcessing}/></div>
          </section>}
          {projectWorkspace === 'quality' && activeProject && <section className="task-page quality-task-page"><div className="quality-page-grid"><QualityPanel projectId={activeProject.id} segments={segments} revision={editorRevision.current} onEditorResult={result => acceptEditorResult(activeProject.id, result)} onSeek={time => { handleSeek(time); setProjectWorkspace('preview'); }}/><GlossaryPanel projectId={activeProject.id}/></div></section>}
          {projectWorkspace === 'smart' && activeProject && <section className="task-page smart-task-page"><SmartToolsPanel projectId={activeProject.id} revision={editorRevision.current} duration={videoDuration} onEditorResult={result => acceptEditorResult(activeProject.id, result)} onProjectChanged={() => { void api.getProject(activeProject.id).then(project => { setActiveProject(project); editorRevision.current = Number(project.edit_revision || 0); return refreshSegments(project.id); }); }}/></section>}
          {projectWorkspace === 'process' && <section className="task-page process-task-page">
            <div className="process-overview"><header><div><h2>从素材到成片</h2><p>一次只配置一个步骤，已完成的内容可以随时回看或重做。</p></div><div className="process-total"><span style={{'--progress': `${totalProgress}%`} as React.CSSProperties}>{totalProgress}%</span><small>整体进度</small></div></header><div className="process-step-list">{compactSteps.map((step, index) => <button key={step.id} className={`${step.state.status} ${activeProcessStep === step.id ? 'selected' : ''}`} onClick={() => setSelectedStep(step.id)}><i>{step.state.status === 'success' ? '✓' : index + 1}</i><span><strong>{step.label}</strong><small>{step.id === 'download' ? '获取素材并提取音频' : step.id === 'transcribe' ? '本地语音识别' : step.id === 'clean' ? '修正错词与断句' : step.id === 'translate' ? '生成目标语言字幕' : '输出字幕或成片'}</small></span><em>{step.state.status === 'success' ? '已完成' : step.state.status === 'running' ? `${Math.round(step.state.progress)}%` : step.state.status === 'failed' ? '需处理' : '待开始'}</em></button>)}</div>{currentTask && isProcessing && <div className="process-live-controls"><span>{currentTask.message}</span><button onClick={toggleTaskPause}>{currentTask.status === 'paused' ? '继续' : '暂停'}</button><button className="danger" onClick={cancelCurrentTask}>停止</button></div>}</div>
            <aside className="process-settings"><header><small>步骤设置</small><h2>{compactSteps.find(step => step.id === activeProcessStep)?.label}</h2></header>{renderProcessSettings()}</aside>
            <details className="process-diagnostics"><summary>任务日志与诊断 <span>{processLogs.length}</span></summary><div><ProcessTimeline steps={processSteps} currentStepId={activeProcessStep} totalProgress={totalProgress} onStepClick={setSelectedStep}/><ProcessLogViewer logs={processLogs} collapsed={false} onToggle={() => undefined} onClear={() => setProcessLogs([])}/></div></details>
          </section>}
          {projectWorkspace === 'style' && <section className="task-page style-task-page">
            <div className="style-canvas"><StyleTemplateBar style={subtitleStyle} onApply={handleStyleChange}/><header><h2>实时外观预览</h2><p>在接近成片的画面比例中调整字幕，不受其他工具干扰。</p></header><div className="style-canvas-stage">{activeProject?.video_path && activeProject.video_url ? <SubtitlePlayer ref={videoPlayerRef} videoUrl={api.getBackendMediaUrl(activeProject.video_url) || ''} segments={segments} style={subtitleStyle} activeIdx={activeSegmentIdx} onTimeUpdate={handleTimeUpdate} onDurationChange={setVideoDuration} onStyleChange={handleStyleChange} presentationMode={presentationMode} onPresentationModeChange={setPresentationMode}/> : <div className="style-preview-card"><span>为每一句话找到恰好的位置。</span><small>Give every line its perfect place.</small></div>}</div></div>
            <aside className="style-controls-page"><header><small>字幕检查器</small><h2>字体与排版</h2></header><SubtitleStylePanel style={subtitleStyle} onChange={handleStyleChange}/></aside>
          </section>}
          {projectWorkspace === 'export' && <section className="task-page export-task-page">
            <header className="export-page-hero"><div><small>最后一步</small><h2>选择交付格式</h2><p>字幕文件立即下载；MP4 与 MKV 会在本机后台压制，不上传媒体。</p></div><label className="bilingual-switch"><input type="checkbox" checked={config.bilingual} onChange={event => setConfig({ ...config, bilingual: event.target.checked })}/><span>包含双语字幕</span></label></header>
            <div className="export-format-groups"><section><header><h3>字幕文件</h3><p>适合剪辑软件、平台上传与继续协作</p></header><div className="export-large-cards">{(['srt', 'vtt', 'ass', 'srt-bilingual'] as ExportFormat[]).map(format => <button key={format} disabled={!hasSegments || isProcessing} onClick={() => void doExport(format)}><i>TXT</i><span><strong>{format === 'srt-bilingual' ? '双语 SRT' : format.toUpperCase()}</strong><small>{format === 'ass' ? '保留完整字幕样式' : format === 'vtt' ? '网页与流媒体字幕' : '通用时间轴字幕'}</small></span><em>导出 ↗</em></button>)}</div></section><section><header><h3>带字幕视频</h3><p>直接获得可以发布的最终成片</p></header><div className="export-large-cards">{(['mp4', 'mkv'] as ExportFormat[]).map(format => <button key={format} disabled={!hasSegments || isProcessing} onClick={() => void doExport(format)}><i>▶</i><span><strong>{format.toUpperCase()}</strong><small>{format === 'mp4' ? '兼容社交平台与移动设备' : '高质量封装与多音轨'}</small></span><em>开始压制 →</em></button>)}</div></section><section><header><h3>项目包</h3><p>迁移字幕、历史、说话人、术语与项目设置</p></header><div className="export-large-cards"><button onClick={() => void exportProjectPackage(false)}><i>ZIP</i><span><strong>精简项目包</strong><small>不包含原始媒体，适合快速迁移</small></span><em>导出 ↗</em></button><button onClick={() => void exportProjectPackage(true)}><i>ZIP</i><span><strong>完整项目包</strong><small>包含视频与音频，文件可能很大</small></span><em>导出 ↗</em></button></div></section></div>
            {currentTask && <div className={`export-task-card ${currentTask.status}`}><div><strong>{currentTask.message || '导出任务'}</strong><small>{currentTask.status === 'success' ? '文件已准备完成' : '可离开此页面，任务会继续运行'}</small></div><progress max={100} value={currentTask.progress || 0}/><span>{Math.round(currentTask.progress || 0)}%</span></div>}
          </section>}
          </div>
        </main>

        {inspectorMode && <><div className="panel-resizer panel-resizer-right" role="separator" aria-label="调整检查器宽度" tabIndex={0} onPointerDown={event => beginResize('right', event)} onKeyDown={event => { if (event.key === 'ArrowLeft') setRightPanelWidth(value => Math.min(480, value + 16)); if (event.key === 'ArrowRight') setRightPanelWidth(value => Math.max(280, value - 16)); }}/>
          <aside className="inspector-sidebar">
            <header className="inspector-title"><div><strong>{inspectorMode === 'style' ? '样式检查器' : compactSteps.find(step => step.id === selectedStep)?.label || '步骤详情'}</strong><small>{inspectorMode === 'style' ? '更改将实时预览' : '确认设置后再开始高成本操作'}</small></div><button aria-label="关闭检查器" onClick={() => setInspectorMode(null)}>×</button></header>
            {inspectorMode === 'style' && <SubtitleStylePanel style={subtitleStyle} onChange={handleStyleChange}/>}
            {inspectorMode === 'step' && <div className="step-inspector">
              {selectedStep === 'download' && <section className="inspector-section"><h3>下载与音频</h3><label>项目链接<input value={activeProject?.source_url || youtubeUrl} placeholder="YouTube URL" onChange={event => setYoutubeUrl(event.target.value)}/></label><div className="runtime-mini"><span className={health?.runtime?.ffmpeg?.ok ? 'ok' : 'error'}>FFmpeg {health?.runtime?.ffmpeg?.ok ? '可用' : '需检查'}</span><span className={health?.runtime?.yt_dlp?.ok ? 'ok' : 'error'}>yt-dlp {health?.runtime?.yt_dlp?.ok ? '可用' : '需检查'}</span></div><p>下载会移除 t=110s 等定位参数；失败时保留原项目，可在此重新下载。</p><button className="button primary" disabled={!activeProject?.source_url || isProcessing} onClick={retryDownload}>重新下载</button>{activeProject?.video_path && <button className="button secondary" disabled={isProcessing} onClick={doExtractAudio}>重新提取音频</button>}</section>}
              {selectedStep === 'transcribe' && <section className="inspector-section transcription-inspector"><h3>语音转写</h3><label>转写模型<AppSelect value={config.model} onChange={model=>setConfig({...config,model:model as ModelSize})} options={modelOptions} label="转写模型" searchable/></label><div className="runtime-picker"><header><strong>运行设备</strong><small>{runtimeForModel(inspectorModelId)?'已为此模型记住':'首次使用必须选择'}</small></header><div className="runtime-choice-grid">{inspectorModel?.runtimes?.map(runtime=><button type="button" key={runtime.id} disabled={!runtime.available} className={runtimeForModel(inspectorModelId)===runtime.id?'selected':''} onClick={()=>chooseRuntime(inspectorModelId,runtime.id)}><i>{runtime.id==='cpu'?'CPU':runtime.id==='mlx'?'GPU':runtime.id==='coreml'?'ANE':'ML'}</i><span><strong>{runtime.name}</strong><small>{runtime.engine}</small>{!runtime.available&&<em>{runtime.reason}</em>}</span>{runtimeForModel(inspectorModelId)===runtime.id&&<b>✓</b>}</button>)}</div>{!inspectorModel?.runtimes?.length&&<p className="runtime-empty">正在读取此模型支持的运行设备…</p>}</div><label>源语言<LanguagePicker value={config.language} onChange={language => setConfig({ ...config, language })}/></label>{modelStatus && <div className="model-readiness"><strong>{inspectorModel?.name||inspectorModelId}</strong><small>{inspectorModel?.ready?'模型已就绪':inspectorModel?.download_required?'首次运行时下载到 App 数据目录':'模型不可用'}</small></div>}<button className="button primary" disabled={!hasAudio || isProcessing || !runtimeForModel(inspectorModelId)} onClick={doTranscribe}>开始转写</button>{!runtimeForModel(inspectorModelId)&&<p className="runtime-required">请选择上方运行设备后再开始转写。</p>}</section>}
              {selectedStep === 'clean' && <section className="inspector-section"><h3>AI 忠实整理</h3><div className="ai-summary-row"><span className="ai-logo">✦</span><div><strong>{activeAIPreset?.name || aiSettings?.provider || '未配置 AI'}</strong><small>{aiSettings?.model || '请先打开设置中心'}</small></div></div><label>参考单句长度 <span>{config.clean_target_length} 字</span><input type="range" min={16} max={100} step={2} value={config.clean_target_length} onChange={event => setConfig({ ...config, clean_target_length: Number(event.target.value) })}/></label><p>只修正明显错词、标点和断句，不改变原意。完整长句不会被强行截断。</p><button className="button primary" disabled={!hasSegments || isProcessing || !aiSettings?.has_api_key} onClick={doClean}>确认并开始整理</button><button className="button secondary" disabled={!hasSegments || isProcessing} onClick={undoClean}>撤销上次整理</button></section>}
              {selectedStep === 'clean' && renderFailedBatchRecovery()}
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
      <SettingsCenter open={showAISettings} onClose={() => setShowAISettings(false)} config={config} onConfigChange={setConfig} appSettings={appSettings} onAppSettingsChange={setAppSettings} aiSettings={aiSettings} onAISaved={setAISettings} theme={theme} onThemeChange={setTheme} motionEnabled={motionEnabled} onMotionEnabledChange={setMotionEnabled} density={density} onDensityChange={setDensity} health={health} onRefreshHealth={refreshHealth} modelStatus={modelStatus} onRefreshModels={refreshModels} onOpenLogs={() => { setBottomTab('logs'); setProjectWorkspace('process'); setShowProjectWorkspace(!!activeProject); setInspectorMode(null); }}/>
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
  segments, currentTime, activeIdx, onSeek, onUpdate, onReplaceAll, onSplit, onMerge,
  onUndo, onRedo, saveState, draftCount, onCommitDraft, onDiscardDraft,
  onAutoScrollChange, autoScroll, disabled
}: {
  segments: SubtitleSegment[];
  currentTime: number;
  activeIdx: number;
  onSeek: (time: number) => void;
  onUpdate: (idx: number, data: SegmentUpdate) => void;
  onReplaceAll: (search: string, replacement: string, fields: Array<'clean_text' | 'translated_text'>, options: {matchCase: boolean; includeLocked: boolean}) => Promise<void>;
  onSplit: (index: number, splitAt: number) => Promise<void>;
  onMerge: (indices: number[]) => Promise<void>;
  onUndo: () => void | Promise<void>;
  onRedo: () => void | Promise<void>;
  saveState: 'idle' | 'saving' | 'saved' | 'error';
  draftCount: number;
  onCommitDraft: () => void | Promise<void>;
  onDiscardDraft: () => void | Promise<void>;
  onAutoScrollChange?: (v: boolean) => void;
  autoScroll?: boolean;
  disabled?: boolean;
}) {
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editField, setEditField] = useState<'start' | 'end' | 'clean_text' | 'translated_text' | null>(null);
  const [editValue, setEditValue] = useState('');
  const [searchText, setSearchText] = useState('');
  const [replaceText, setReplaceText] = useState('');
  const [replacePreview, setReplacePreview] = useState(false);
  const [replaceOriginal, setReplaceOriginal] = useState(true);
  const [replaceTranslation, setReplaceTranslation] = useState(true);
  const [replaceMatchCase, setReplaceMatchCase] = useState(false);
  const [replaceIncludeLocked, setReplaceIncludeLocked] = useState(false);
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(() => new Set());
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

  const replaceMatchCount = useMemo(() => {
    if (!searchText || (!replaceOriginal && !replaceTranslation)) return 0;
    const normalize = (value: string) => replaceMatchCase ? value : value.toLocaleLowerCase();
    const needle = normalize(searchText);
    return segments.filter(segment => (replaceIncludeLocked || !segment.locked) && (
      (replaceOriginal && normalize(segment.clean_text || segment.raw_text).includes(needle)) ||
      (replaceTranslation && normalize(segment.translated_text).includes(needle))
    )).length;
  }, [replaceIncludeLocked, replaceMatchCase, replaceOriginal, replaceTranslation, searchText, segments]);

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

  const startEdit = (seg: SubtitleSegment, field: 'start' | 'end' | 'clean_text' | 'translated_text') => {
    if (disabled) return;
    setEditingIdx(seg.index);
    setEditField(field);
    setEditValue(field === 'start' || field === 'end'
      ? String(seg[field].toFixed(3))
      : (seg[field] || (field === 'clean_text' ? seg.raw_text : '')));
  };

  const saveEdit = () => {
    if (editingIdx === null || !editField) return;
    if (editField === 'start' || editField === 'end') {
      const value = Number(editValue);
      if (!Number.isFinite(value) || value < 0) return;
      onUpdate(editingIdx, { [editField]: value });
    } else {
      onUpdate(editingIdx, { [editField]: editValue });
    }
    setEditingIdx(null);
    setEditField(null);
  };

  const cancelEdit = () => {
    setEditingIdx(null);
    setEditField(null);
  };

  const selected = [...selectedIndices].sort((a, b) => a - b);
  const selectedSegment = selected.length === 1 ? segments.find(segment => segment.index === selected[0]) : undefined;
  const canSplit = !!selectedSegment && currentTime > selectedSegment.start && currentTime < selectedSegment.end;
  const canMerge = selected.length >= 2 && selected.every((value, index) => index === 0 || value === selected[index - 1] + 1);

  const toggleSelected = (index: number) => setSelectedIndices(current => {
    const next = new Set(current);
    if (next.has(index)) next.delete(index); else next.add(index);
    return next;
  });

  return (
    <div className={`subtitle-table-container ${disabled ? 'editing-disabled' : ''}`}>
      <div className="subtitle-table-header">
        <h3>字幕时间轴与编辑 ({segments.length} 条)</h3>
        <div className="table-header-right">
          <button className="btn btn-ghost btn-xs" disabled={disabled} onClick={() => void onUndo()} title="撤销上次编辑">↶ 撤销</button>
          <button className="btn btn-ghost btn-xs" disabled={disabled} onClick={() => void onRedo()} title="重做上次编辑">↷ 重做</button>
          <button className="btn btn-ghost btn-xs" disabled={disabled || !canSplit} onClick={() => {
            if (!selectedSegment) return;
            void onSplit(selectedSegment.index, currentTime).then(() => setSelectedIndices(new Set()));
          }}>拆分</button>
          <button className="btn btn-ghost btn-xs" disabled={disabled || !canMerge} onClick={() => void onMerge(selected).then(() => setSelectedIndices(new Set()))}>合并</button>
          <button className={`btn btn-ghost btn-xs ${autoScroll ? '' : 'inactive'}`}
            onClick={() => onAutoScrollChange?.(!autoScroll)}
            title={autoScroll ? '自动滚动已开启' : '自动滚动已关闭'}>
            {autoScroll ? '🔁 自动' : '⏸ 锁定'}
          </button>
          <span className="current-time">⏱ {fmtTime(currentTime)}</span>
          <span className={`editor-save-state ${saveState}`}>{saveState === 'saving' ? '保存中…' : saveState === 'error' ? '保存失败' : saveState === 'saved' ? '已保存' : ''}</span>
        </div>
      </div>
      {draftCount > 0 && <div className="subtitle-draft-bar" role="status"><span>{draftCount} 条未提交草稿已安全保存在本机</span><button onClick={() => void onDiscardDraft()}>放弃</button><button className="primary" onClick={() => void onCommitDraft()}>保存全部</button></div>}
      <div className="subtitle-findbar">
        <input value={searchText} onChange={event => setSearchText(event.target.value)} placeholder="搜索字幕" />
        <input value={replaceText} onChange={event => setReplaceText(event.target.value)} placeholder="替换为" />
        <button disabled={disabled || !searchText || !replaceMatchCount} onClick={() => setReplacePreview(true)}>全部替换</button>
        {searchText && <span>{visibleSegments.length} 条匹配</span>}
        <label><input type="checkbox" checked={replaceOriginal} onChange={event => setReplaceOriginal(event.target.checked)}/> 原文/整理</label><label><input type="checkbox" checked={replaceTranslation} onChange={event => setReplaceTranslation(event.target.checked)}/> 译文</label><label><input type="checkbox" checked={replaceMatchCase} onChange={event => setReplaceMatchCase(event.target.checked)}/> 区分大小写</label><label><input type="checkbox" checked={replaceIncludeLocked} onChange={event => setReplaceIncludeLocked(event.target.checked)}/> 覆盖锁定项</label>
      </div>
      {replacePreview && <div className="replace-preview" role="dialog" aria-label="确认全部替换"><div><strong>预览全部替换</strong><span>将在{replaceOriginal ? '原文/整理' : ''}{replaceOriginal && replaceTranslation ? '和' : ''}{replaceTranslation ? '译文' : ''}中修改 {replaceMatchCount} 条字幕，{replaceIncludeLocked ? '包括锁定字幕' : '锁定字幕不会改变'}。此操作可撤销。</span></div><button onClick={() => setReplacePreview(false)}>取消</button><button className="primary" onClick={() => void onReplaceAll(searchText, replaceText, [...(replaceOriginal ? ['clean_text' as const] : []), ...(replaceTranslation ? ['translated_text' as const] : [])], { matchCase: replaceMatchCase, includeLocked: replaceIncludeLocked }).then(() => setReplacePreview(false))}>确认替换</button></div>}
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
                  <td className="col-idx"><input type="checkbox" aria-label={`选择第 ${seg.index} 条字幕`} checked={selectedIndices.has(seg.index)} onChange={() => toggleSelected(seg.index)}/><span>{seg.index}</span></td>
                  <td className="col-time">
                    {isEditing && editField === 'start' ? <input className="time-edit-input" type="number" min="0" step="0.001" autoFocus value={editValue} onChange={event => setEditValue(event.target.value)} onBlur={saveEdit} onKeyDown={event => event.key === 'Enter' ? saveEdit() : event.key === 'Escape' ? cancelEdit() : undefined}/>
                      : <><button className="time-seek" onClick={() => onSeek(seg.start)}>{fmtTime(seg.start)}</button><button className="time-edit" aria-label={`编辑第 ${seg.index} 条开始时间`} onClick={() => startEdit(seg, 'start')}>✎</button></>}
                  </td>
                  <td className="col-time">
                    {isEditing && editField === 'end' ? <input className="time-edit-input" type="number" min="0" step="0.001" autoFocus value={editValue} onChange={event => setEditValue(event.target.value)} onBlur={saveEdit} onKeyDown={event => event.key === 'Enter' ? saveEdit() : event.key === 'Escape' ? cancelEdit() : undefined}/>
                      : <><button className="time-seek" onClick={() => onSeek(seg.end)}>{fmtTime(seg.end)}</button><button className="time-edit" aria-label={`编辑第 ${seg.index} 条结束时间`} onClick={() => startEdit(seg, 'end')}>✎</button></>}
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
