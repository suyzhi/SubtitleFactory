import TaskWorkspace from './components/TaskWorkspace';
import {useSelectedTask,useActiveTaskCount} from './taskStore';
import ExportWorkspace from './components/ExportWorkspace';
// 字幕工厂 - 主应用组件（集成字幕播放器 + 流程可视化）

import {
  lazy, Suspense, useState, useEffect, useCallback, useMemo, useRef,
} from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import type {
  Project, SubtitleSegment, TaskStatus, ProcessingConfig,
  ExportFormat,
  ProcessStep, ProcessLogEntry, TaskStepStatus,
  SubtitleStyleSettings, SubtitleStats,
  HealthStatus, AppSettings,
  SegmentUpdate, SegmentOperationRequest,
  FailedCleanBatch, PlaylistBatchDetail, SegmentSearchHit,
} from './types';
import * as api from './api/backend';
import { findSubtitleFocusIndex } from './subtitleTableVirtualization';
import { deriveProcessSteps, emptyProcess } from './processSteps';
import { loadAppBootstrap } from './appBootstrap';
import {
  clearRecoveredSegmentDraft,
  readRecoveredSegmentDraft,
  writeRecoveredSegmentDraft,
} from './draftRecovery';
import './App.css';
import TranscriptionCandidates from './components/TranscriptionCandidates';
import LibraryControls from './components/LibraryControls';
import { projectReadiness, taskLabel, taskProgressLabel, taskIsActive } from './projectState';
import ImportFlow, { type ImportSource } from './components/ImportFlow';
import TranscriptionSetup from './components/TranscriptionSetup';
import EditorWorkbench from './components/EditorWorkbench';
import WorkspacePanel from './components/WorkspacePanel';
import DeferredPanel from './components/DeferredPanel';
import SubtitleTable from './components/SubtitleTable';
import {
  highlightedSnippet, isPlaylistUrl, libraryTimecode, projectExportFilename,
} from './utils/library';

const PROFESSIONAL_UI_MARKER = 'subtitle-factory-ui:professional-v2';
const LIBRARY_WORKSPACE_UI_MARKER = 'subtitle-factory-ui:library-workspace-v2';

import type { PlayerPresentationMode, SubtitlePlayerHandle } from './components/SubtitlePlayer';
import { loadSubtitleStyle, saveSubtitleStyle } from './subtitleStyle';
import ProcessTimeline from './components/ProcessTimeline';
import ProcessLogViewer from './components/ProcessLogViewer';
import SubtitleTimeline from './components/SubtitleTimeline';
import QualityPanel from './components/QualityPanel';
import GlobalTaskDrawer from './components/GlobalTaskDrawer';
import StyleTemplateBar from './components/StyleTemplateBar';
import MediaSelectionPanel from './components/MediaSelectionPanel';
import GlossaryPanel from './components/GlossaryPanel';
import SmartToolsPanel from './components/SmartToolsPanel';
import PlaylistBatchGroups from './components/PlaylistBatchGroups';
import SubtitleStylePanel from './components/SubtitleStylePanel';
import {
  recoveryAction,
  recoveryActionLabel as taskRecoveryActionLabel,
} from './taskRecovery';
import LanguagePicker from './components/LanguagePicker';
import AppSelect from './components/AppSelect';
import { languageLabel } from './languages';
import { resolveConfiguredModel, resolveRuntimeSelection } from './transcriptionSelection';
import appIcon from './assets/branding/app-icon-ui.png';
import settingsIcon from './assets/player-icons/settings.png';

const SubtitlePlayer = lazy(() => import('./components/SubtitlePlayer'));
const ProductionCenter = lazy(() => import('./components/ProductionCenter'));
const PlaylistBatchDialog = lazy(() => import('./components/PlaylistBatchDialog'));
const SettingsCenter = lazy(() => import('./components/SettingsCenter'));
const ContentCenter = lazy(() => import('./components/ContentCenter'));

const DEFAULT_CONFIG: ProcessingConfig = {
  model: 'auto', language: 'auto', target_language: 'zh',
  enable_clean: false, enable_translate: false, bilingual: localStorage.getItem('subtitle_factory_export_bilingual') === 'true', clean_target_length: 42,
};

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
  const youtubeEnabled = api.youtubeFeaturesEnabled();
  const filesystemAutomationEnabled = api.filesystemAutomationEnabled();
  // ── Core State ──
  const [projects, setProjects] = useState<Project[]>([]);
  const [trashProjects, setTrashProjects] = useState<Project[]>([]);
  const [activeProject, setActiveProject] = useState<Project | null>(null);
  const [segments, setSegments] = useState<SubtitleSegment[]>([]);
  const [draftItems, setDraftItems] = useState<Record<number, SegmentUpdate>>({});
  const [draftIsStale, setDraftIsStale] = useState(false);
  const [editorSaveState, setEditorSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [config, setConfig] = useState<ProcessingConfig>(() => ({
    ...DEFAULT_CONFIG,
    enable_clean: localStorage.getItem('subtitle_factory_flow_clean') === 'true',
    enable_translate: localStorage.getItem('subtitle_factory_flow_translate') === 'true',
    clean_target_length: Number(localStorage.getItem('subtitle_factory_clean_target_length')) || DEFAULT_CONFIG.clean_target_length,
  }));
  const [subtitleStyle, setSubtitleStyle] = useState<SubtitleStyleSettings>(loadSubtitleStyle);

  // ── Task State ──
  const [currentTask, setCurrentTask] = useSelectedTask();
  const activeTaskCount = useActiveTaskCount();
  const [pollInterval, setPollInterval] = useState<number | null>(null);
  const [processLogs, setProcessLogs] = useState<ProcessLogEntry[]>([]);
  const [processSteps, setProcessSteps] = useState<ProcessStep[]>(emptyProcess);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [, setSubtitleStats] = useState<SubtitleStats | null>(null);
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
  const [aiProviderState, setAIProviderState] = useState<api.AIProvidersResponse | null>(null);
  const [toast, setToast] = useState('');
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [taskStarting, setTaskStarting] = useState(false);
  const [modelError,setModelError]=useState('');
  const modelRequest=useRef(0);
  const [modelStatus, setModelStatus] = useState<Awaited<ReturnType<typeof api.getTranscriptionModels>> | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [appSettings, setAppSettings] = useState<AppSettings>({
    default_workflow: 'automatic', auto_save: true, startup_behavior: 'restore_last',
    youtube_media_mode: 'local',
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
  const [librarySearchHits, setLibrarySearchHits] = useState<SegmentSearchHit[]>([]);
  const [librarySearchTotal, setLibrarySearchTotal] = useState(0);
  const [librarySearchPage, setLibrarySearchPage] = useState(1);
  const [librarySearchLoading, setLibrarySearchLoading] = useState(false);
  const [librarySearchSelection, setLibrarySearchSelection] = useState(-1);
  const [librarySearchFacets, setLibrarySearchFacets] = useState<api.SegmentSearchFacets>({});
  const [librarySearchFilters, setLibrarySearchFilters] = useState<api.SegmentSearchFilters>({});
  const [libraryPage,setLibraryPage] = useState(1);
  const [libraryTotal,setLibraryTotal] = useState(0);
  const [libraryPages,setLibraryPages] = useState(1);
  const [libraryLoading,setLibraryLoading] = useState(false);
  const [libraryError,setLibraryError] = useState('');
  const [libraryStatus,setLibraryStatus] = useState('');
  const [libraryRefresh,setLibraryRefresh] = useState(0);
  const [compactLibrary,setCompactLibrary] = useState(() => localStorage.getItem('subtitle_factory_library_compact') === 'true');
  const [librarySort, setLibrarySort] = useState('updated_desc');
  const [showProjectWorkspace, setShowProjectWorkspace] = useState(false);
  const [projectWorkspace, setProjectWorkspace] = useState<'subtitles' | 'style' | 'export'>('subtitles');
  const [subtitleFocusRequest, setSubtitleFocusRequest] = useState(0);
  const [pendingImport, setPendingImport] = useState<ImportSource | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [quickImport, setQuickImport] = useState(() => localStorage.getItem('subtitle_factory_quick_import') || '');
  const [toolsOpen, setToolsOpen] = useState(false);
  const [toolsTab, setToolsTab] = useState<'process' | 'quality' | 'smart' | 'content'>('process');
  const [segmentInspector, setSegmentInspector] = useState<number | null>(null);
  const [segmentsLoading, setSegmentsLoading] = useState(false);
  const [segmentsError, setSegmentsError] = useState('');
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
  const subtitleFocus = false;
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
  const settingsButtonRef = useRef<HTMLButtonElement>(null);
  const contextMenuReturnFocus = useRef<HTMLElement | null>(null);
  const downloadedRenderTask = useRef('');
  const restoredStartupProject = useRef(false);
  const taskStartLock = useRef(false);
  const importActionLock = useRef(false);
  const exportActionLock = useRef(false);
  const ownsWindowFullscreen = useRef(false);
  const editorRevision = useRef(0);
  const editorQueue = useRef<Promise<unknown>>(Promise.resolve());
  const editorWriteFailed = useRef(false);
  const draftWriteQueue = useRef<Promise<unknown>>(Promise.resolve());
  const draftWriteGeneration = useRef<Record<string, number>>({});
  const draftMutationLock = useRef(false);
  const draftMutationPromise = useRef<Promise<void>>(Promise.resolve());
  const draftItemsRef = useRef<Record<number, SegmentUpdate>>({});
  const draftBaseRevisionRef = useRef<number | null>(null);
  const activeProjectIdRef = useRef<string | null>(null);
  const projectSelectionIntent = useRef(0);
  const styleSaveTimer = useRef<number | null>(null);
  const pendingSearchJump = useRef<SegmentSearchHit | null>(null);
  const toastTimer = useRef<number | null>(null);

  const showToast = useCallback((message: string, duration = 2800) => {
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    setToast(message);
    toastTimer.current = window.setTimeout(() => {
      setToast('');
      toastTimer.current = null;
    }, duration);
  }, []);

  const applyAppSettings = useCallback((settings: AppSettings) => {
    setAppSettings(settings);
    const persisted = settings.transcription_runtime_by_model || {};
    setTranscriptionRuntimes(current => {
      const next = { ...current, ...persisted };
      localStorage.setItem('subtitle_factory_transcription_runtimes', JSON.stringify(next));
      return next;
    });
  }, []);

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

      const canPlay = Boolean(
        activeProject?.video_path,
      );

      if (event.key.toLowerCase() === 't' && !isEditing && canPlay) {
        event.preventDefault();
        setPresentationMode(mode => mode === 'theater' ? 'normal' : 'theater');
        return;
      }

      if (event.key.toLowerCase() === 'f' && !isEditing && canPlay) {
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
  }, [activeProject?.media_mode, activeProject?.video_path, activeProject?.youtube_video_id, presentationMode]);

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
    if (!youtubeEnabled) {
      setPlaylistBatches([]);
      return;
    }
    if (backendStatus !== 'connected') return;
    const result = await api.getPlaylistBatches();
    setPlaylistBatches(result.batches);
  }, [backendStatus, youtubeEnabled]);
  const hasActivePlaylistBatch = playlistBatches.some(({ batch }) =>
    batch.status === 'running' || batch.status === 'pending');

  useEffect(() => { setLibraryPage(1); },[librarySearch,librarySort,libraryStatus,libraryView]);
  useEffect(() => {
    if (backendStatus !== 'connected' || showProjectWorkspace) return;
    let cancelled=false;
    setLibraryLoading(true); setLibraryError('');
    const timer=window.setTimeout(() => {
      void api.listProjects({deleted:libraryView === 'trash',search:librarySearch.trim(),sort:librarySort,page_size:40,page:libraryPage,readiness:libraryStatus})
        .then(result => { if (cancelled) return; (libraryView === 'trash' ? setTrashProjects : setProjects)(result.projects); setLibraryTotal(result.total ?? result.projects.length); setLibraryPages(result.pages || 1); if (libraryPage > (result.pages || 1)) setLibraryPage(result.pages || 1); })
        .catch(error => { if (!cancelled) setLibraryError(error.message); })
        .finally(() => { if (!cancelled) setLibraryLoading(false); });
    },200);
    return () => { cancelled=true; window.clearTimeout(timer); };
  },[backendStatus,librarySearch,librarySort,libraryStatus,libraryView,libraryPage,showProjectWorkspace,libraryRefresh]);


  useEffect(() => {
    setLibrarySearchPage(1);
  }, [librarySearch, librarySearchFilters]);

  useEffect(() => {
    const query = librarySearch.trim();
    setLibrarySearchSelection(-1);
    if (backendStatus !== 'connected' || libraryView !== 'projects' || query.length < 2) {
      setLibrarySearchHits([]);
      setLibrarySearchTotal(0);
      setLibrarySearchLoading(false);
      if (!query) {
        setLibrarySearchFacets(current => Object.keys(current).length ? {} : current);
        setLibrarySearchFilters(current => Object.keys(current).length ? {} : current);
      }
      return;
    }
    let cancelled = false;
    setLibrarySearchLoading(true);
    const timer = window.setTimeout(() => {
      void api.searchSegments(query, { ...librarySearchFilters, page: librarySearchPage, page_size: 50 })
        .then(result => {
          if (cancelled) return;
          setLibrarySearchHits(result.hits);
          setLibrarySearchTotal(result.total);
          setLibrarySearchFacets(result.facets || {});
        })
        .catch(() => {
          if (!cancelled) {
            setLibrarySearchHits([]);
            setLibrarySearchTotal(0);
          }
        })
        .finally(() => { if (!cancelled) setLibrarySearchLoading(false); });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [backendStatus, librarySearch, librarySearchFilters, librarySearchPage, libraryView]);

  useEffect(() => {
    if (!youtubeEnabled || backendStatus !== 'connected' || libraryView !== 'projects') return;
    void refreshPlaylistBatches().catch(() => undefined);
    // Keep active queues responsive without waking the backend every two
    // seconds while the project library is idle.
    const interval = hasActivePlaylistBatch ? 2000 : 30000;
    const timer = window.setInterval(() => void refreshPlaylistBatches().catch(() => undefined), interval);
    return () => window.clearInterval(timer);
  }, [backendStatus, hasActivePlaylistBatch, libraryView, refreshPlaylistBatches, youtubeEnabled]);

  const refreshModels = useCallback(() => {
    if (backendStatus !== 'connected') return;
    const sequence=++modelRequest.current;
    setModelError('');
    api.getTranscriptionModels(activeProject?.id, config.language).then(result => {if(sequence === modelRequest.current)setModelStatus(result);}).catch(error => {if(sequence === modelRequest.current)setModelError(error.message);});
  }, [activeProject?.id, backendStatus, config.language]);

  const refreshAIProviders = useCallback(async () => {
    try {
      const latest = await api.getAIProviders();
      setAIProviderState(latest);
    } catch {
      // Local media and transcription remain available when Keychain access is
      // temporarily unavailable. SettingsCenter provides the visible retry UI.
    }
  }, []);

  useEffect(() => {
    if (!showProjectWorkspace && !playlistDialogUrl) return;
    void refreshAIProviders();
  }, [playlistDialogUrl, refreshAIProviders, showProjectWorkspace]);

  const refreshHealth = useCallback(() => {
    api.checkHealth().then(setHealth).catch(() => undefined);
  }, []);

  const refreshLibraries = useCallback(async () => {
    setLibraryRefresh(value => value + 1);
    const result = await api.getPlaylistBatches().catch(() => ({ batches: [] as PlaylistBatchDetail[] }));
    setPlaylistBatches(result.batches);
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
        const displayedStatus = status === 'running' && task.status === 'paused' ? 'paused' : status;
        setStepStatus(stepId, displayedStatus, status === 'success' ? 100 : status === 'running' ? task.progress : 0);
      }
      return;
    }
    if (task.type === 'prepare_audio') {
      const status = task.status === 'success' ? 'success'
        : task.status === 'failed' ? 'failed'
          : task.status === 'cancelled' ? 'cancelled'
            : task.status === 'paused' ? 'paused' : 'running';
      setStepStatus('download', status, task.progress, task.error || undefined, task.suggestion || undefined);
      setStepStatus('extract_audio', status, task.progress, task.error || undefined, task.suggestion || undefined);
      return;
    }
    if (task.type === 'materialize_video' || task.type === 'switch_media_mode') {
      const status = task.status === 'success' ? 'success'
        : task.status === 'failed' ? 'failed'
          : task.status === 'cancelled' ? 'cancelled'
            : task.status === 'paused' ? 'paused' : 'running';
      setStepStatus('download', status, task.progress, task.error || undefined, task.suggestion || undefined);
      return;
    }
    // Map backend task type to step
    for (const [stepId, taskType] of Object.entries(STEP_TASK_MAP)) {
      if (taskType === task.type || (task.type === 'render' && stepId === 'export')) {
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
    setProcessSteps(deriveProcessSteps(proj));
  }, []);

  // ── Poll task status ──
  const pollingTaskId = currentTask?.id;
  useEffect(() => {
    if (!pollInterval || !pollingTaskId) return;
    let cancelled = false;
    let requestedSequence = 0;
    let appliedSequence = 0;
    const id = window.setInterval(async () => {
      const sequence = ++requestedSequence;
      try {
        const status = await api.getTaskStatus(pollingTaskId);
        if (
          cancelled
          || sequence < appliedSequence
          || (status.project_id && activeProjectIdRef.current !== status.project_id)
        ) return;
        appliedSequence = sequence;
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

        if (['transcribe','workflow'].includes(status.type) && (status.status === 'running' || status.status === 'paused') && activeProject) {
          const projectId = activeProject.id;
          api.getSegments(projectId).then(result => {
            if (activeProjectIdRef.current === projectId) setSegments(result.segments);
          }).catch(() => {});
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
            addLog('info', status.type, `输出文件已生成${d.output_size ? ` (${(d.output_size/1024/1024).toFixed(1)}MB)` : ''}`);
            if (status.type === 'render' && downloadedRenderTask.current !== status.id) {
              downloadedRenderTask.current = status.id;
              const format = String(d.format || 'mp4');
              void api.downloadExport(
                activeProject?.id || status.project_id || '',
                format,
                String(d.output_path),
                projectExportFilename(activeProject?.title, format, '带字幕'),
              ).then(saved => {
                addLog('info', status.type, saved ? '成片已保存到所选位置' : '已取消保存成片');
              }).catch(error => {
                addLog('error', status.type, `成片保存失败：${error.message}`);
              });
            }
          }
        }

        if (!taskIsActive(status)) {
          setPollInterval(null);
          if (activeProject) {
            const projectId = activeProject.id;
            api.getSegments(projectId)
              .then(result => {
                if (activeProjectIdRef.current === projectId) setSegments(result.segments);
              })
              .catch(() => {});
            api.getProject(projectId).then(project => {
              if (activeProjectIdRef.current !== projectId) return;
              setActiveProject(project);
              editorRevision.current = Number(project.edit_revision || 0);
            }).catch(() => {});
            setLibraryRefresh(value => value + 1);
          }
        }
      } catch (e: any) {
        if (cancelled) return;
        addLog('error', '系统', `状态查询失败: ${e.message}`);
        setPollInterval(null);
      }
    }, pollInterval);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [setCurrentTask, pollInterval, pollingTaskId, activeProject, addLog, ingestTaskLogs, syncProcessFromTask]);

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
        const startup = await loadAppBootstrap(false);
        if (!stopped) {
          setLibraryRefresh(value => value + 1);
          applyAppSettings(startup.app.settings);
          setConfig(current => ({
            ...current,
            model: String(startup.app.settings.default_model || current.model),
            language: String(startup.app.settings.source_language || current.language),
            target_language: String(startup.app.settings.translation_target_language || current.target_language),
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
  }, [applyAppSettings]);

  // ── Refresh segments ──
  const refreshSegments = useCallback(async (projectId: string) => {
    if (activeProjectIdRef.current === projectId) { setSegmentsLoading(true); setSegmentsError(''); }
    try {
      const r = await api.getSegments(projectId);
      if (activeProjectIdRef.current === projectId) setSegments(r.segments);
    } catch (error) {
      if (activeProjectIdRef.current === projectId) setSegmentsError(error instanceof Error ? error.message : '字幕加载失败');
    } finally {
      if (activeProjectIdRef.current === projectId) setSegmentsLoading(false);
    }
  }, []);

  const refreshActiveProject = useCallback(async (projectId: string) => {
    try {
      const project = await api.getProject(projectId);
      if (activeProjectIdRef.current !== projectId) return;
      setActiveProject(project);
      editorRevision.current = Number(project.edit_revision || 0);
      await refreshSegments(projectId);
    } catch { /* the initiating panel already reports its own operation error */ }
  }, [refreshSegments]);

  // ── Select project ──
  const selectProject = useCallback(async (p: Project, requestedIntent?: number) => {
    const selectionIntent = requestedIntent ?? ++projectSelectionIntent.current;
    if (selectionIntent !== projectSelectionIntent.current) return;
    // Finish writes for the previous project before changing the single active
    // editor revision. This prevents a late response from project A from being
    // interpreted with project B's revision or replacing project B's rows.
    await Promise.all([
      editorQueue.current,
      draftWriteQueue.current,
      draftMutationPromise.current,
    ]);
    if (selectionIntent !== projectSelectionIntent.current) return;
    activeProjectIdRef.current = p.id;
    setActiveProject(p);
    editorRevision.current = Number(p.edit_revision || 0);
    draftItemsRef.current = {};
    draftBaseRevisionRef.current = null;
    setDraftItems({});
    setDraftIsStale(false);
    setEditorSaveState('idle');
    setShowProjectWorkspace(true);
    setProjectWorkspace('subtitles');
    setToolsOpen(false); setSegmentInspector(null); setSegments([]);
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
        if (activeProjectIdRef.current !== p.id) return;
        setSubtitleStyle(result.settings
          ? { ...loadSubtitleStyle(), ...result.settings } as SubtitleStyleSettings
          : loadSubtitleStyle());
      }).catch(() => {
        if (activeProjectIdRef.current === p.id) setSubtitleStyle(loadSubtitleStyle());
      }),
    ]);
    try {
      const latestTask = await api.getLatestProjectTask(p.id);
      if (activeProjectIdRef.current !== p.id) return;
      if (latestTask) {
        setCurrentTask(latestTask);
        syncProcessFromTask(latestTask);
        ingestTaskLogs(latestTask);
        if (taskIsActive(latestTask)) setPollInterval(1000);
      }
    } catch { /* projects created by older builds may not have task history */ }
    if (activeProjectIdRef.current === p.id) addLog('info', '项目', `打开项目: ${p.title}`);
  }, [setCurrentTask, refreshSegments, addLog, ingestTaskLogs, refreshProcessSteps, syncProcessFromTask]);

  const selectProjectById = useCallback(async (projectId: string) => {
    const selectionIntent = ++projectSelectionIntent.current;
    try {
      const project = await api.getProject(projectId);
      if (selectionIntent !== projectSelectionIntent.current) return;
      await selectProject(project, selectionIntent);
    } catch (error) {
      if (selectionIntent === projectSelectionIntent.current) {
        setToast(error instanceof Error ? error.message : String(error));
      }
    }
  }, [selectProject]);

  useEffect(() => {
    if (!activeProject?.id) return;
    const projectId = activeProject.id;
    let cancelled = false;
    void api.getSegmentDraft(projectId).then(async ({ draft }) => {
      if (cancelled || activeProjectIdRef.current !== projectId) return;
      const localDraft = readRecoveredSegmentDraft(projectId);
      if (draft?.invalid && !localDraft) {
        draftItemsRef.current = { 0: {} };
        draftBaseRevisionRef.current = draft.base_revision;
        setDraftItems({ 0: {} });
        setDraftIsStale(true);
        setEditorSaveState('error');
        setToast('字幕草稿文件已损坏；正式字幕没有改变，请放弃该草稿后重新编辑');
        return;
      }
      const backendUpdatedAt = draft?.updated_at
        ? Date.parse(draft.updated_at.replace(' ', 'T')) || 0
        : 0;
      const useLocal = !!localDraft && (
        !!draft?.invalid
        || !draft?.items.length
        || localDraft.updatedAt > backendUpdatedAt
      );
      if (localDraft && !useLocal) clearRecoveredSegmentDraft(projectId);
      const baseRevision = useLocal ? localDraft.baseRevision : draft?.base_revision;
      const next = useLocal
        ? localDraft.items
        : Object.fromEntries((draft?.items || []).map(item => {
            const { index, ...data } = item;
            return [index, data];
          }));
      if (!Object.keys(next).length || baseRevision === undefined) return;
      draftItemsRef.current = next;
      draftBaseRevisionRef.current = baseRevision;
      setDraftItems(next);
      if (useLocal) {
        const generation = (draftWriteGeneration.current[projectId] || 0) + 1;
        draftWriteGeneration.current[projectId] = generation;
        const persist = () => api.saveSegmentDraft(projectId, baseRevision,
          Object.entries(next).map(([index, data]) => ({ index: Number(index), ...data })));
        const queued = draftWriteQueue.current.then(persist, persist);
        draftWriteQueue.current = queued.then(() => undefined, () => undefined);
        try {
          await queued;
          if (draftWriteGeneration.current[projectId] === generation) {
            clearRecoveredSegmentDraft(projectId);
          }
        } catch {
          // Keep the local recovery copy until the sidecar accepts it.
        }
      }
      if (cancelled || activeProjectIdRef.current !== projectId) return;
      if (baseRevision !== editorRevision.current) {
        setDraftIsStale(true);
        setEditorSaveState('error');
        setToast(useLocal
          ? '检测到异常退出前的旧字幕草稿；已保留但未自动套用，请确认后恢复或放弃'
          : '检测到旧版字幕草稿；已保留但未自动套用，请确认后恢复或放弃');
        return;
      }
      setDraftIsStale(false);
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
    const projectId = activeProjectIdRef.current;
    taskStartLock.current = true;
    setTaskStarting(true);
    setStepStatus(stepId, 'running', 0);
    try {
      const { task_id } = await fn();
      if (activeProjectIdRef.current !== projectId) return;
      lastTaskMessage.current = '';
      lastTaskBatch.current = '';
      lastTaskStep.current = '';
      lastTaskProgressBucket.current = -1;
      backendLogTaskId.current = task_id;
      lastBackendLogCount.current = 0;
      addLog('info', name, `${name} 任务已创建`);
      const status = await api.getTaskStatus(task_id);
      if (activeProjectIdRef.current !== projectId) return;
      setCurrentTask(status);
      syncProcessFromTask(status);
      ingestTaskLogs(status);
      setPollInterval(interval);
    } catch (e: any) {
      if (activeProjectIdRef.current !== projectId) return;
      addLog('error', name, `${name} 失败: ${e.message}`);
      setStepStatus(stepId, 'failed', 0, e.message);
      const suggestionMap: Record<string, string> = {
        '下载视频': '请查看任务错误码；App 会区分权限、网络、格式、运行时和存储问题',
        '提取音频': '请查看 FFmpeg、源媒体和输出目录的具体诊断',
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
  }, [setCurrentTask, addLog, ingestTaskLogs, setStepStatus, syncProcessFromTask]);

  const compatibleModel = useCallback((): string | null => {
    const requestedModel = resolveConfiguredModel(config.model, modelStatus?.recommended_model);
    if (config.language === 'auto') return requestedModel;
    const model = modelStatus?.models.find(item => item.id === requestedModel);
    const supported = model?.languages || ['*'];
    if (supported.includes('*') || supported.includes(config.language)) return requestedModel;
    const switchModel = window.confirm(
      `${model?.name || '所选模型'}不支持${languageLabel(config.language)}。\n\n是否切换到 Whisper Small 后继续？`,
    );
    if (!switchModel) return null;
    setConfig(current => ({ ...current, model: 'small' }));
    showToast('已切换到 Whisper Small', 2600);
    return 'small';
  }, [config.language, config.model, modelStatus, showToast]);

  // ── Create project ──
  const runtimeForModel = useCallback((model:string) => resolveRuntimeSelection(
    model,
    appSettings.transcription_runtime_by_model,
    transcriptionRuntimes,
    modelStatus?.models.find(item=>item.id===model)?.selected_runtime,
  ),
  [appSettings.transcription_runtime_by_model,modelStatus,transcriptionRuntimes]);
  const chooseRuntime = useCallback((model:string,runtime:string)=>{
    setTranscriptionRuntimes(current=>{const next={...current,[model]:runtime};localStorage.setItem('subtitle_factory_transcription_runtimes',JSON.stringify(next));return next;});
    setAppSettings(current=>({
      ...current,
      transcription_runtime_by_model:{...(current.transcription_runtime_by_model||{}),[model]:runtime},
    }));
    void api.saveAppSettings({transcription_runtime_by_model:{[model]:runtime}})
      .then(result=>applyAppSettings(result.settings))
      .catch(error=>showToast(`运行设备保存失败：${error instanceof Error?error.message:String(error)}`,4200));
  },[applyAppSettings,showToast]);
  const requireRuntime = useCallback((model:string)=>{
    const runtime=runtimeForModel(model); const option=modelStatus?.models.find(item=>item.id===model)?.runtimes?.find(item=>item.id===runtime);
    if(!runtime||!option?.available){setSelectedStep('transcribe');setToolsTab('process');setToolsOpen(true);showToast(!runtime?'请选择转写运行设备：CPU、Apple GPU 或 Core ML':'所选运行设备当前不可用，请重新选择',4200);return '';}
    return runtime;
  },[modelStatus,runtimeForModel,showToast]);

  const flowOptions = useMemo(() => ({enable_clean:config.enable_clean,enable_translate:config.enable_translate,target_language:config.target_language,clean_target_length:config.clean_target_length,text_processing_consent:config.enable_clean || config.enable_translate}),[config.enable_clean,config.enable_translate,config.target_language,config.clean_target_length]);
  useEffect(() => {localStorage.setItem('subtitle_factory_flow_clean',String(config.enable_clean));localStorage.setItem('subtitle_factory_flow_translate',String(config.enable_translate));},[config.enable_clean,config.enable_translate]);
  const importSignature = JSON.stringify([config.model, config.language, runtimeForModel(config.model === 'auto' ? modelStatus?.recommended_model || '' : config.model),flowOptions]);
  const preparedImports = useRef(new Map<File|string,{projectId:string;uploaded:boolean;started:boolean}>());
  const executeImport = useCallback(async (source: ImportSource, generate: boolean, remember = false) => {
    if (importActionLock.current) return;
    const model = generate ? compatibleModel() : config.model;
    if (!model) throw new Error('当前语言没有可用模型，请选择模型');
    const runtime = generate ? requireRuntime(model) : '';
    if (generate && !runtime) throw new Error('请选择可用的运行设备');
    importActionLock.current = true; setImportBusy(true);
    const items = source.kind === 'files' ? source.files : [source.url];
    try {
      for (const item of items) {
        const title = typeof item === 'string' ? `YouTube - ${item}` : item.name;
        let prepared = preparedImports.current.get(item);
        if (!prepared) {
        const created = await api.createProject({source_type:source.kind === 'files' ? 'local' : 'youtube', source_url:source.kind === 'link' ? source.url : undefined, title, language:config.language, target_language:config.target_language, media_mode:'local'});
        prepared = {projectId:created.project_id,uploaded:false,started:false};
        preparedImports.current.set(item,prepared);
        }
        if (prepared.started) continue;
        let taskId: string | undefined;
        if (typeof item !== 'string' && !prepared.uploaded) {
          setUploadProgress(0);
          await api.importLocalVideo(prepared.projectId,item,{autostart:false,onProgress:setUploadProgress});
          setUploadProgress(null);
          prepared.uploaded = true;
        }
        const project = await api.getProject(prepared.projectId);
        setProjects(current => [project,...current.filter(item => item.id !== project.id)]);
        await selectProject(project);
        if (generate) {
          taskId = (await api.startWorkflow(project.id,{model,language:config.language,runtime,...flowOptions, ...(source.kind === 'link' ? {source_url:source.url} : {})})).task_id;
        } else if (source.kind === 'link') {
          taskId = (await api.startDownload(project.id,source.url)).task_id;
        }
        prepared.started = true;
        if (taskId) {
          const status = await api.getTaskStatus(taskId);
          if (activeProjectIdRef.current === project.id) { setCurrentTask(status); syncProcessFromTask(status); ingestTaskLogs(status); setPollInterval(1000); }
        }
      }
      if (remember && generate) { localStorage.setItem('subtitle_factory_quick_import',importSignature); setQuickImport(importSignature); }
      await api.saveAppSettings({default_model:config.model,source_language:config.language});
      setPendingImport(null);
      preparedImports.current.clear();
    } finally { importActionLock.current=false; setImportBusy(false); setUploadProgress(null); }
  },[setCurrentTask, compatibleModel,config.language,config.model,config.target_language,flowOptions,importSignature,ingestTaskLogs,requireRuntime,selectProject,syncProcessFromTask]);
  const prepareImport = useCallback((source:ImportSource) => {
    if (source.kind === 'files') {
      source = {...source,files:source.files.filter(file => /\.(mp4|mkv|mov|webm|avi)$/i.test(file.name))};
      if (!source.files.length) { showToast('请选择支持的视频文件'); return; }
    }
    if (quickImport === importSignature) void executeImport(source,true).catch(error => { setPendingImport(source); showToast(error.message); });
    else setPendingImport(source);
  },[executeImport,importSignature,quickImport,showToast]);
  const importFiles = useCallback((files:File[]) => prepareImport({kind:'files',files}),[prepareImport]);

  const handleImportLocal = useCallback(() => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = '.mp4,.mkv,.mov,.webm,.avi,video/*';
    input.hidden = true;
    input.onchange = () => { const files = Array.from(input.files || []); input.remove(); importFiles(files); };
    input.oncancel = () => input.remove();
    document.body.appendChild(input);
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
      model, language: config.language, runtime, ...flowOptions,
    }));
  }, [activeProject, compatibleModel, config.language, flowOptions, requireRuntime, startTask]);

  const recoverTranscription = useCallback(async (preserveModel = false) => {
    if (!activeProject || !currentTask?.recoverable) return;
    try {
      const status = await api.getTranscriptionModels(activeProject.id, config.language);
      const failedModel = String(
        currentTask.details?.model_id
        || currentTask.details?.model_resolution?.model_id
        || currentTask.details?.resume_payload?.model
        || currentTask.details?.resolved_model
        || config.model,
      );
      if (preserveModel) {
        const selected = status.models.find(item => item.id === failedModel);
        if (!selected?.ready) {
          setSelectedStep('transcribe');
          setToolsTab('process'); setToolsOpen(true);
          setToast('原转写模型当前未就绪，请检查模型与运行设备后重试');
          return;
        }
        const runtime = String(currentTask.details?.runtime || runtimeForModel(failedModel) || '');
        if (!runtime) {
          setSelectedStep('transcribe');
          setToolsTab('process'); setToolsOpen(true);
          setToast('请重新选择转写运行设备后再重试');
          return;
        }
        if (!window.confirm(`上次转写在应用退出时被中断。原有已发布字幕未被覆盖。\n\n是否使用 ${selected.name} 从头重新转写？`)) return;
        await startTask('重新开始转写', 'transcribe', () => api.retryTranscription(activeProject.id, {
          model: failedModel, language: String(currentTask.details?.language || config.language), runtime,
        }));
        return;
      }
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
  }, [activeProject, config.language, config.model, currentTask, requireRuntime, runtimeForModel, startTask]);

  const doClean = useCallback(() => {
    if (!activeProject) return;
    startTask('AI 整理', 'clean', () => api.startClean(activeProject.id, config.clean_target_length));
  }, [activeProject, config.clean_target_length, startTask]);

  const retryFailedCleanBatch = useCallback(async (batchIndex: number) => {
    if (!currentTask || taskStarting || ['running', 'pending', 'paused'].includes(currentTask.status)) return;
    const originalTaskId = currentTask.id;
    const projectId = activeProjectIdRef.current;
    setTaskStarting(true);
    try {
      const result = await api.retryFailedCleanBatch(originalTaskId, batchIndex);
      const status = await api.getTaskStatus(result.task_id);
      if (
        activeProjectIdRef.current !== projectId
        || (status.project_id && status.project_id !== projectId)
      ) return;
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
  }, [setCurrentTask, currentTask, ingestTaskLogs, syncProcessFromTask, taskStarting]);

  const undoClean = useCallback(async () => {
    if (!activeProject || (currentTask && ['running', 'pending', 'paused'].includes(currentTask.status))) return;
    try {
      const result = await api.undoClean(activeProject.id);
      await refreshSegments(activeProject.id);
      setLibraryRefresh(value => value + 1);
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

  const updateTargetLanguage = useCallback(async (targetLanguage: string) => {
    setConfig(current => ({ ...current, target_language: targetLanguage }));
    const projectId = activeProjectIdRef.current;
    if (!projectId) return;
    try {
      const updated = await api.updateProjectTargetLanguage(projectId, targetLanguage);
      if (activeProjectIdRef.current === projectId) setActiveProject(updated);
    } catch (error) {
      if (activeProjectIdRef.current === projectId) {
        setToast(error instanceof Error ? error.message : String(error));
      }
    }
  }, []);

  // ── Export ──
  const doExport = useCallback(async (fmt: ExportFormat) => {
    if (!activeProject || exportActionLock.current) return;
    const projectId = activeProject.id;
    exportActionLock.current = true;
    setTaskStarting(true);
    setStepStatus('export', 'running', 10);
    try {
      if (fmt === 'mp4' || fmt === 'mkv') {
        const r = await api.exportSubtitles(projectId, {
          format: fmt, bilingual: config.bilingual,
          primary_language: subtitleStyle.mode === 'bilingual_translated_first' ? 'translated' : 'original', style: subtitleStyle,
        });
        if (r.task_id) {
          const s = await api.getTaskStatus(r.task_id);
          if (activeProjectIdRef.current !== projectId) return;
          backendLogTaskId.current = r.task_id;
          lastBackendLogCount.current = 0;
          setCurrentTask(s);
          ingestTaskLogs(s);
          setPollInterval(2000);
          setStepStatus('render', 'running', 10);
          addLog('info', '压制视频', `${fmt.toUpperCase()} 视频导出任务已创建`);
        }
      } else {
        const result = await api.exportSubtitles(projectId, {
          format: fmt, bilingual: config.bilingual,
          primary_language: subtitleStyle.mode === 'bilingual_translated_first' ? 'translated' : 'original', style: subtitleStyle,
        });
        if (activeProjectIdRef.current !== projectId) return;
        setStepStatus('export', 'success', 100);
        const saved = await api.downloadExport(
          projectId,
          fmt,
          result.path,
          projectExportFilename(activeProject.title, fmt),
        );
        if (activeProjectIdRef.current !== projectId) return;
        addLog('info', '导出', saved ? `${fmt.toUpperCase()} 已保存` : `已取消保存 ${fmt.toUpperCase()}`);
        showToast(saved ? `${fmt.toUpperCase()} 字幕已导出` : '已取消保存');
      }
    } catch (e: any) {
      if (activeProjectIdRef.current !== projectId) return;
      addLog('error', '导出', `${fmt} 导出失败: ${e.message}`);
      showToast(`导出失败：${e.message}`,5000);
      setStepStatus('export', 'failed', 0, e.message, '检查文件权限和 ffmpeg');
    } finally {
      exportActionLock.current = false;
      setTaskStarting(false);
    }
  }, [setCurrentTask, activeProject, config.bilingual, subtitleStyle, addLog, ingestTaskLogs, setStepStatus, showToast]);

  const acceptEditorResult = useCallback((projectId: string, result: Awaited<ReturnType<typeof api.applySegmentOperation>>) => {
    setProjects(current => current.map(project => project.id === projectId
      ? { ...project, edit_revision: result.revision, segments_count: result.segments.length }
      : project));
    if (activeProjectIdRef.current !== projectId) return;
    editorRevision.current = result.revision;
    setSegments(result.segments);
    setActiveProject(current => current?.id === projectId
      ? { ...current, edit_revision: result.revision, segments_count: result.segments.length }
      : current);
    if (Object.keys(draftItemsRef.current).length > 0) {
      setDraftIsStale(true);
      setEditorSaveState('error');
    } else {
      setEditorSaveState('saved');
    }
  }, []);

  const runEditorOperation = useCallback((data: Omit<SegmentOperationRequest, 'expected_revision'>) => {
    if (!activeProject) return Promise.reject(new Error('请先选择项目'));
    const projectId = activeProject.id;
    const execute = async () => {
      editorWriteFailed.current = false;
      setEditorSaveState('saving');
      try {
        const result = await api.applySegmentOperation(projectId, {
          ...data, expected_revision: editorRevision.current,
        });
        acceptEditorResult(projectId, result);
        return result;
      } catch (error: any) {
        editorWriteFailed.current = true;
        if (activeProjectIdRef.current === projectId) setEditorSaveState('error');
        addLog('error', '编辑', `字幕操作失败: ${error.message}`);
        if (activeProjectIdRef.current === projectId) setToast(error.message || '字幕操作失败');
        if (error.code === 'EDIT_REVISION_CONFLICT') {
          const [project, latest] = await Promise.all([
            api.getProject(projectId), api.getSegments(projectId),
          ]);
          if (activeProjectIdRef.current !== projectId) throw error;
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
    if (draftMutationLock.current) {
      setToast('正在保存或放弃草稿，请稍候');
      return;
    }
    if (draftIsStale) {
      setToast('旧草稿与当前字幕版本不同；请先预览并确认恢复，或放弃旧草稿');
      return;
    }
    const projectId = activeProject.id;
    const manualDraft = appSettings.auto_save === false || Object.keys(draftItemsRef.current).length > 0;
    if (manualDraft) {
      const next = { ...draftItemsRef.current, [idx]: { ...(draftItemsRef.current[idx] || {}), ...data } };
      const baseRevision = draftBaseRevisionRef.current ?? editorRevision.current;
      const generation = (draftWriteGeneration.current[projectId] || 0) + 1;
      draftWriteGeneration.current[projectId] = generation;
      draftBaseRevisionRef.current = baseRevision;
      draftItemsRef.current = next;
      if (!writeRecoveredSegmentDraft(projectId, baseRevision, next)) {
        addLog('warning', '编辑', '浏览器恢复副本写入失败；仍会继续写入本机数据库');
      }
      setDraftItems(next);
      setSegments(current => current.map(segment => segment.index === idx ? { ...segment, ...data } : segment));
      setEditorSaveState('saving');
      const persist = async () => {
        await api.saveSegmentDraft(projectId, baseRevision,
          Object.entries(next).map(([index, value]) => ({ index: Number(index), ...value })));
      };
      const queued = draftWriteQueue.current.then(persist, persist);
      draftWriteQueue.current = queued.then(() => undefined, () => undefined);
      try {
        await queued;
        if (draftWriteGeneration.current[projectId] === generation) {
          clearRecoveredSegmentDraft(projectId);
          if (activeProjectIdRef.current === projectId) setEditorSaveState('saved');
        }
      } catch (error: any) {
        if (activeProjectIdRef.current === projectId) setEditorSaveState('error');
        addLog('error', '编辑', `草稿保存失败: ${error.message}`);
      }
      return;
    }
    setSegments(current => current.map(segment => segment.index === idx ? { ...segment, ...data } : segment));
    await runEditorOperation({
      operation: 'update_many', items: [{ index: idx, ...data }],
      include_locked: data.locked !== undefined,
    }).catch(() => undefined);
  }, [activeProject, appSettings.auto_save, addLog, draftIsStale, runEditorOperation]);

  const commitDraft = useCallback(async () => {
    if (!activeProject || !Object.keys(draftItemsRef.current).length || draftMutationLock.current) return;
    const projectId = activeProject.id;
    draftMutationLock.current = true;
    let releaseMutation: () => void = () => {};
    draftMutationPromise.current = new Promise(resolve => { releaseMutation = resolve; });
    setEditorSaveState('saving');
    try {
      if (draftItemsRef.current[0]) {
        throw new Error('损坏的草稿不能保存，请放弃后重新编辑');
      }
      if (draftIsStale && !window.confirm('当前正式字幕已在草稿保存后发生变化。\n\n确认把这份旧草稿按行号应用到当前字幕吗？此操作可撤销。')) {
        setEditorSaveState('error');
        return;
      }
      await Promise.all([editorQueue.current, draftWriteQueue.current]);
      if (activeProjectIdRef.current !== projectId) return;
      const recovered = readRecoveredSegmentDraft(projectId);
      if (recovered) {
        await api.saveSegmentDraft(projectId, recovered.baseRevision,
          Object.entries(recovered.items).map(([index, data]) => ({ index: Number(index), ...data })));
        clearRecoveredSegmentDraft(projectId);
      }
      if (activeProjectIdRef.current !== projectId) return;
      const result = draftIsStale
        ? await api.rebaseSegmentDraft(projectId)
        : await api.commitSegmentDraft(projectId);
      clearRecoveredSegmentDraft(projectId);
      if (activeProjectIdRef.current !== projectId) return;
      draftItemsRef.current = {};
      draftBaseRevisionRef.current = null;
      setDraftItems({});
      setDraftIsStale(false);
      acceptEditorResult(projectId, result);
      setToast('字幕草稿已保存');
    } catch (error: any) {
      if (activeProjectIdRef.current === projectId) {
        setEditorSaveState('error');
        if (error?.code === 'EDIT_REVISION_CONFLICT') {
          setDraftIsStale(true);
          await refreshActiveProject(projectId);
          setToast('正式字幕已发生变化；草稿仍安全保留，请预览后确认恢复或放弃');
        } else {
          setToast(error.message);
        }
      }
    } finally {
      draftMutationLock.current = false;
      releaseMutation();
    }
  }, [acceptEditorResult, activeProject, draftIsStale, refreshActiveProject]);

  const discardDraft = useCallback(async () => {
    if (!activeProject || draftMutationLock.current) return;
    const projectId = activeProject.id;
    draftMutationLock.current = true;
    let releaseMutation: () => void = () => {};
    draftMutationPromise.current = new Promise(resolve => { releaseMutation = resolve; });
    try {
      await Promise.all([editorQueue.current, draftWriteQueue.current]);
      if (activeProjectIdRef.current !== projectId) return;
      await api.discardSegmentDraft(projectId);
      clearRecoveredSegmentDraft(projectId);
      if (activeProjectIdRef.current !== projectId) return;
      draftItemsRef.current = {};
      draftBaseRevisionRef.current = null;
      setDraftItems({});
      setDraftIsStale(false);
      await refreshSegments(projectId);
      setEditorSaveState('idle');
      setToast('字幕草稿已放弃');
    } catch (error: any) {
      if (activeProjectIdRef.current === projectId) {
        setEditorSaveState('error');
        setToast(error.message);
      }
    } finally {
      draftMutationLock.current = false;
      releaseMutation();
    }
  }, [activeProject, refreshSegments]);

  const previewDraft = useCallback(() => {
    if (!activeProject || !Object.keys(draftItemsRef.current).length) return;
    if (draftItemsRef.current[0]) {
      setToast('损坏的草稿无法预览；正式字幕未改变，可以安全放弃该草稿');
      return;
    }
    setSegments(current => current.map(segment => ({
      ...segment,
      ...(draftItemsRef.current[segment.index] || {}),
    })));
    setProjectWorkspace('subtitles');
    setShowProjectWorkspace(true);
    setToast(draftIsStale
      ? '正在预览旧草稿；数据库尚未改变，确认恢复后才会写入'
      : '正在预览本机草稿');
  }, [activeProject, draftIsStale]);

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
    if (draftMutationLock.current) {
      setToast('正在保存或放弃草稿，请稍候');
      return;
    }
    const projectId = activeProject.id;
    const execute = async () => {
      if (activeProjectIdRef.current !== projectId) return;
      const result = await api.undoEditorOperation(projectId, editorRevision.current);
      acceptEditorResult(projectId, result);
    };
    const queued = editorQueue.current.then(execute, execute);
    editorQueue.current = queued.then(() => undefined, () => undefined);
    try {
      await queued;
    } catch (error: any) {
      if (activeProjectIdRef.current === projectId) setToast(error.message);
    }
  }, [acceptEditorResult, activeProject]);

  const redoEditor = useCallback(async () => {
    if (!activeProject) return;
    if (draftMutationLock.current) {
      setToast('正在保存或放弃草稿，请稍候');
      return;
    }
    const projectId = activeProject.id;
    const execute = async () => {
      if (activeProjectIdRef.current !== projectId) return;
      const result = await api.redoEditorOperation(projectId, editorRevision.current);
      acceptEditorResult(projectId, result);
    };
    const queued = editorQueue.current.then(execute, execute);
    editorQueue.current = queued.then(() => undefined, () => undefined);
    try {
      await queued;
    } catch (error: any) {
      if (activeProjectIdRef.current === projectId) setToast(error.message);
    }
  }, [acceptEditorResult, activeProject]);

  useEffect(() => {
    const handleEditorShortcut = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.repeat) return;
      const key = event.key.toLowerCase();
      if (key === 's') {
        event.preventDefault();
        if (showAISettings || !showProjectWorkspace || !activeProjectIdRef.current) return;
        const target = event.target;
        if (target instanceof HTMLElement && (
          target.isContentEditable || target.tagName === 'INPUT' || target.tagName === 'TEXTAREA'
        )) target.blur();
        window.setTimeout(() => {
          if (Object.keys(draftItemsRef.current).length > 0) {
            void commitDraft();
            return;
          }
          void editorQueue.current.then(() => {
            setToast(editorWriteFailed.current ? '字幕尚未保存，请检查错误后重试' : '当前字幕已保存');
            window.setTimeout(() => setToast(''), 1800);
          });
        }, 0);
        return;
      }
      if (key !== 'z') return;
      if (showAISettings || !showProjectWorkspace) return;
      const target = event.target;
      if (target instanceof HTMLElement && (
        target.isContentEditable || target.tagName === 'INPUT' || target.tagName === 'TEXTAREA'
      )) return;
      event.preventDefault();
      if (event.shiftKey) void redoEditor();
      else void undoEditor();
    };
    window.addEventListener('keydown', handleEditorShortcut);
    return () => window.removeEventListener('keydown', handleEditorShortcut);
  }, [commitDraft, redoEditor, showAISettings, showProjectWorkspace, undoEditor]);

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
        showToast(`已导入 ${result.affected_count} 条字幕，可使用撤销恢复`);
      } catch (error: any) { showToast(error.message, 4200); }
    };
    input.click();
  }, [acceptEditorResult, activeProject, showToast]);

  const exportProjectPackage = useCallback(async (includeMedia: boolean) => {
    if (!activeProject) return;
    try {
      const result = await api.createProjectPackage(activeProject.id, includeMedia);
      const saved = await api.downloadProjectPackage(
        result.package_id,
        `${activeProject.title}-${includeMedia ? '完整' : '精简'}.sfproject`,
        result.path,
      );
      setToast(saved ? (includeMedia ? '完整项目包已导出' : '精简项目包已导出') : '已取消保存项目包');
    } catch (error: any) { setToast(error.message); }
  }, [activeProject]);

  const importProjectPackage = useCallback(() => {
    const input = document.createElement('input'); input.type = 'file'; input.accept = '.sfproject';
    input.onchange = async () => {
      const file = input.files?.[0]; if (!file) return;
      try {
        const result = await api.importProjectPackage(file);
        setLibraryRefresh(value => value + 1);
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
    setActiveSegmentIdx(segments.findLastIndex(segment => segment.start <= time && segment.end >= time));
    videoPlayerRef.current?.seekTo(time);
  }, [segments]);

  const openLibrarySearchHit = useCallback(async (hit: SegmentSearchHit) => {
    const selectionIntent = ++projectSelectionIntent.current;
    try {
      pendingSearchJump.current = hit;
      const project = projects.find(item => item.id === hit.project_id)
        || await api.getProject(hit.project_id);
      if (selectionIntent !== projectSelectionIntent.current) return;
      await selectProject(project, selectionIntent);
    } catch (error) {
      pendingSearchJump.current = null;
      setToast(error instanceof Error ? error.message : String(error));
    }
  }, [projects, selectProject]);

  useEffect(() => {
    const hit = pendingSearchJump.current;
    if (!hit || activeProject?.id !== hit.project_id) return;
    if (!segments.some(segment => segment.id === hit.segment_id || segment.index === hit.segment_index)) return;
    const frame = window.requestAnimationFrame(() => {
      setProjectWorkspace('subtitles');
      setShowProjectWorkspace(true);
      setActiveSegmentIdx(Math.max(0, segments.findIndex(segment => segment.id === hit.segment_id)));
      handleSeek(hit.start);
      setSubtitleFocusRequest(request => request + 1);
      pendingSearchJump.current = null;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeProject?.id, handleSeek, segments]);

  const previewClipRange = useCallback((start: number, end: number) => {
    setProjectWorkspace('subtitles');
    setShowProjectWorkspace(true);
    window.requestAnimationFrame(() => videoPlayerRef.current?.previewRange(start, end));
  }, []);

  const toggleTaskPause = useCallback(async () => {
    if (!currentTask) return;
    const taskId = currentTask.id;
    const projectId = activeProjectIdRef.current;
    try {
      const next = currentTask.status === 'paused'
        ? await api.resumeTask(taskId)
        : await api.pauseTask(taskId);
      if (activeProjectIdRef.current !== projectId || next.id !== taskId) return;
      setCurrentTask(next);
      syncProcessFromTask(next);
      addLog('info', next.type, next.status === 'paused' ? '任务已暂停' : '任务已继续');
    } catch (error: any) {
      addLog('error', currentTask.type, error.message);
    }
  }, [setCurrentTask, currentTask, syncProcessFromTask, addLog]);

  const cancelCurrentTask = useCallback(async () => {
    if (!currentTask || !['pending', 'running', 'paused'].includes(currentTask.status)) return;
    const taskId = currentTask.id;
    const projectId = activeProject?.id || null;
    try {
      const next = await api.cancelTask(taskId);
      if (activeProjectIdRef.current !== projectId || next.id !== taskId) return;
      setCurrentTask(next);
      ingestTaskLogs(next);
      setPollInterval(taskIsActive(next) ? 1000 : null);
      syncProcessFromTask(next);
      addLog('warning', next.type, taskProgressLabel(next));
      setToast(taskProgressLabel(next));
      window.setTimeout(() => setToast(''), 2600);
      if (projectId) {
        api.getProject(projectId).then(project => {
          if (activeProjectIdRef.current === projectId) setActiveProject(project);
        }).catch(() => {});
        setLibraryRefresh(value => value + 1);
        api.getSegments(projectId).then(result => {
          if (activeProjectIdRef.current === projectId) setSegments(result.segments);
        }).catch(() => {});
      }
    } catch (error: any) {
      addLog('error', currentTask.type, `终止失败：${error.message}`);
      setToast('无法终止当前任务');
      window.setTimeout(() => setToast(''), 3000);
    }
  }, [setCurrentTask, currentTask, activeProject, syncProcessFromTask, addLog, ingestTaskLogs]);

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
          if (activeProjectIdRef.current !== projectId) return;
          setToast(`样式保存失败：${error.message}`);
          window.setTimeout(() => setToast(''), 3000);
        });
    }, 350);
  }, [activeProject]);

  const projectGroups = useMemo(() => {
    const groups = new Map<string, { key: string; label: string; projects: Project[]; rank: number }>();
    for (const project of projects.slice(0,40)) {
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
    projectSelectionIntent.current += 1;
    activeProjectIdRef.current = null;
    setActiveProject(null);
    setSegments([]);
    draftItemsRef.current = {};
    draftBaseRevisionRef.current = null;
    setDraftItems({});
    setDraftIsStale(false);
    setCurrentTask(null);
    setPollInterval(null);
    setProcessSteps(emptyProcess());
    setSelectedStep(null);
    setInspectorMode(null);
    setShowProjectWorkspace(false);
    setProjectWorkspace('subtitles');
  }, [setCurrentTask]);

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
    if (activeProject?.id === project.id) {
      await Promise.all([
        editorQueue.current,
        draftWriteQueue.current,
        draftMutationPromise.current,
      ]);
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
      await animateProjectRemoval(trashProjects.slice(0,40).map(project => project.id));
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

  const changeProjectMediaMode = useCallback(async (mediaMode: 'local') => {
    if (
      !activeProject
      || activeProject.source_type !== 'youtube'
      || taskStarting
      || Boolean(currentTask && ['running', 'pending', 'paused'].includes(currentTask.status))
    ) return;
    const projectId = activeProject.id;
    setTaskStarting(true);
    try {
      const result = await api.updateProjectMediaMode(projectId, mediaMode);
      if (activeProjectIdRef.current !== projectId) return;
      setActiveProject(result.project);
      setProjects(current => current.map(project => project.id === result.project.id ? result.project : project));
      setToast(result.message);
      window.setTimeout(() => setToast(''), 3200);
      if (result.task_id) {
        const status = await api.getTaskStatus(result.task_id);
        if (activeProjectIdRef.current !== projectId) return;
        setCurrentTask(status);
        syncProcessFromTask(status);
        ingestTaskLogs(status);
        setPollInterval(1000);
      }
    } catch (error: any) {
      if (activeProjectIdRef.current !== projectId) return;
      setToast(`媒体模式切换失败：${error.message}`);
      window.setTimeout(() => setToast(''), 4200);
    } finally {
      setTaskStarting(false);
    }
  }, [setCurrentTask, activeProject, currentTask, ingestTaskLogs, syncProcessFromTask, taskStarting]);

  const beginMaterialization = useCallback(async (
    reason: 'manual' | 'player_fallback' | 'offline',
    label: string,
  ) => {
    if (!activeProject || activeProject.source_type !== 'youtube' || taskStarting) return;
    const projectId = activeProject.id;
    setTaskStarting(true);
    setStepStatus('download', 'running', 0);
    try {
      const result = await api.materializeProjectVideo(projectId, reason);
      if (activeProjectIdRef.current !== projectId) return;
      if (result.task_id) {
        const status = await api.getTaskStatus(result.task_id);
        if (activeProjectIdRef.current !== projectId) return;
        setCurrentTask(status);
        syncProcessFromTask(status);
        ingestTaskLogs(status);
        setPollInterval(1000);
        return;
      }
      const project = result.project || await api.getProject(projectId);
      if (activeProjectIdRef.current !== projectId) return;
      setActiveProject(project);
      setProjects(current => current.map(item => item.id === project.id ? project : item));
      setStepStatus('download', 'success', 100);
      setToast(result.message || `${label}已完成`);
      window.setTimeout(() => setToast(''), 3200);
    } catch (error: any) {
      if (activeProjectIdRef.current !== projectId) return;
      setStepStatus('download', 'failed', 0, error.message);
      setToast(`${label}失败：${error.message}`);
      window.setTimeout(() => setToast(''), 4200);
    } finally {
      setTaskStarting(false);
    }
  }, [setCurrentTask, activeProject, ingestTaskLogs, setStepStatus, syncProcessFromTask, taskStarting]);

  const downloadLocalCopy = useCallback(() => {
    void beginMaterialization('manual', '下载本地副本');
  }, [beginMaterialization]);

  const retryCurrentFailure = useCallback(() => {
    if (!currentTask?.recoverable) return;
    const action = recoveryAction(currentTask);
    if (currentTask.type === 'materialize_video') {
      void beginMaterialization('manual', '重新下载本地副本');
      return;
    }
    if (currentTask.type === 'switch_media_mode') {
      void changeProjectMediaMode('local');
      return;
    }
    if (action === 'workflow' && activeProject) {
      const payload = currentTask.details?.resume_payload;
      if (payload) startTask('从失败步骤重试','transcribe',() => api.startWorkflow(activeProject.id,{model:payload.model,language:payload.language,runtime:payload.runtime,...payload.options,resume_task_id:currentTask.id}));
      else doGenerateSubtitles();
      return;
    }
    if (action === 'download') {
      retryDownload();
      return;
    }
    if (action === 'transcription') {
      void recoverTranscription(currentTask.error_code === 'APP_INTERRUPTED');
      return;
    }
    if (action === 'extract_audio') {
      doExtractAudio();
      return;
    }
    if (action === 'clean') {
      doClean();
      return;
    }
    if (action === 'translate') {
      doTranslate();
      return;
    }
    if (action === 'render') {
      const format = currentTask.details?.format === 'mkv' ? 'mkv' : 'mp4';
      void doExport(format);
      return;
    }
    if (action === 'smart_tools') {
      setToolsTab('smart'); setToolsOpen(true);
      setToast('已打开智能工具；请检查范围与模型后重新开始');
      return;
    }
    if (action === 'content') {
      setToolsTab('content'); setToolsOpen(true);
      setToast('已打开内容工作区；已完成结果仍在，请手动重试中断的部分');
      return;
    }
    if (action === 'settings') {
      setShowAISettings(true);
      return;
    }
    setToolsTab('process'); setToolsOpen(true);
    setToast('已打开处理流程，请检查参数后重新开始');
  }, [activeProject, startTask,
    beginMaterialization, changeProjectMediaMode, currentTask, doClean, doExport,
    doExtractAudio, doGenerateSubtitles, doTranslate, recoverTranscription, retryDownload,
  ]);

  const recoveryActionLabel = taskRecoveryActionLabel(currentTask);

  const openWorkflowStep = useCallback((stepId: string) => {
    setSelectedStep(stepId);
    setToolsTab('process'); setToolsOpen(true);
    setShowProjectWorkspace(true);
    setInspectorMode(null);
  }, []);

  // ── Step indicators ──
  const hasAudio = activeProject?.audio_path;
  const hasSegments = segments.length > 0;
  const isProcessing = taskStarting || taskIsActive(currentTask);
  const editorBusy = isProcessing && (!currentTask || ['clean','translate','speaker_diarization','ocr'].includes(currentTask.type) || (currentTask.type === 'workflow' && ['clean','translate'].some(stage => currentTask.details?.stages?.[stage] === 'running')));
  const hasLocalVideo = Boolean(activeProject?.video_available || activeProject?.video_path);
  const activeProjectIsYoutube = youtubeEnabled && activeProject?.source_type === 'youtube';
  const canPlayMedia = hasLocalVideo;
  const activeSegmentIndex = activeSegmentIdx >= 0 ? segments[activeSegmentIdx]?.index ?? -1 : -1;
  const subtitleEntryFocusIndex = findSubtitleFocusIndex(segments, currentTime);
  const cleanAIProvider = aiProviderState?.providers.find(
    item => item.provider_id === aiProviderState.assignments.clean_provider_id,
  );
  const translateAIProvider = aiProviderState?.providers.find(
    item => item.provider_id === aiProviderState.assignments.translate_provider_id,
  );
  const cleanAIReady = Boolean(cleanAIProvider?.enabled && cleanAIProvider.has_api_key);
  const translateAIReady = Boolean(translateAIProvider?.enabled && translateAIProvider.has_api_key);

  const compactSteps = useMemo(() => {
    const find = (id: string) => processSteps.find(step => step.id === id);
    const combine = (...ids: string[]): ProcessStep => {
      const values = ids.map(find).filter(Boolean) as ProcessStep[];
      const failed = values.find(step => step.status === 'failed');
      const running = values.find(step => step.status === 'running' || step.status === 'paused');
      const allDone = values.length > 0 && (ids.includes('export') ? values.some(step => step.status === 'success') : values.every(step => step.status === 'success'));
      const interrupted = values.find(step => ['cancelled','partial'].includes(step.status));
      return failed || running || interrupted || { ...(values.at(-1) || emptyProcess()[0]), status: allDone ? 'success' : 'waiting', progress: allDone ? 100 : 0 };
    };
    return [
      { id: 'download', label: activeProjectIsYoutube ? '下载' : '媒体', icon: '⇩', state: combine('download', 'extract_audio') },
      { id: 'transcribe', label: '转写', icon: '⌁', state: combine('transcribe') },
      { id: 'clean', label: '整理', icon: '✦', state: combine('clean') },
      { id: 'translate', label: '翻译', icon: '文', state: combine('translate') },
      { id: 'export', label: '导出', icon: '↗', state: combine('export', 'render') },
    ];
  }, [activeProjectIsYoutube, processSteps]);
  const inspectorModelId=config.model==='auto'?(modelStatus?.recommended_model||'small'):config.model;

  const playlistWorkflow = {
    model: inspectorModelId,
    runtime: runtimeForModel(inspectorModelId),
    language: config.language,
    target_language: config.target_language,
    clean_target_length: config.clean_target_length,
  };

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
  const renderMediaInspector = () => <section className="inspector-section media-mode-inspector">
    <h3>播放与音频来源</h3>
    {activeProjectIsYoutube && <label>项目链接<input value={activeProject?.source_url || youtubeUrl} placeholder="YouTube URL" onChange={event => setYoutubeUrl(event.target.value)}/></label>}
    <div className="runtime-mini">
      <span className={health?.runtime?.ffmpeg?.ok ? 'ok' : 'error'}>FFmpeg {health?.runtime?.ffmpeg?.ok ? '可用' : '需检查'}</span>
      {activeProjectIsYoutube && <span className={health?.runtime?.yt_dlp?.ok ? 'ok' : 'error'}>yt-dlp {health?.runtime?.yt_dlp?.ok ? '可用' : '需检查'}</span>}
      {activeProjectIsYoutube && <span className={health?.runtime?.deno?.ok && health?.runtime?.ejs?.ok ? 'ok' : 'error'}>YouTube 挑战组件 {health?.runtime?.deno?.ok && health?.runtime?.ejs?.ok ? '可用' : '需检查'}</span>}
    </div>
    {activeProject?.source_type === 'local'
        ? <p>用户导入的视频只保存在此项目，可离线播放并选择音轨或截取范围。</p>
        : <p>完整视频保存在本机，可离线播放并选择音轨或截取范围。</p>}
    {activeProject?.video_path && <MediaSelectionPanel
      projectId={activeProject.id}
      onChanged={changed => {
        const projectId = activeProject.id;
        void api.getProject(projectId).then(project => {
          if (activeProjectIdRef.current !== projectId) return;
          setActiveProject(project);
          refreshProcessSteps(project);
          showToast(changed ? '音轨或范围已更新；下次生成字幕将自动准备音频，现有字幕已保留' : '设置未改变，可继续复用现有音频');
        }).catch(error => showToast(error.message));
      }}
    />}
    <div className="media-mode-actions">
      {activeProjectIsYoutube && <button className="button primary" disabled={!activeProject?.source_url || isProcessing} onClick={retryDownload}>
        重新下载视频
      </button>}
      {activeProjectIsYoutube && !hasLocalVideo && <button className="button secondary" disabled={isProcessing} onClick={downloadLocalCopy}>下载本地副本</button>}
      {activeProject?.video_path && <button className="button secondary" disabled={isProcessing} onClick={doExtractAudio}>重新提取音频</button>}
    </div>
  </section>;
  const renderTranscriptionSetup = () => <>{modelError && <p role="alert">模型列表暂时无法刷新：{modelError}<button onClick={refreshModels}>重新加载模型</button></p>}<TranscriptionSetup models={modelStatus?.models || []} model={config.model} language={config.language} recommended={modelStatus?.recommended_model} runtime={runtimeForModel(inspectorModelId)} onModel={model => setConfig(current => ({...current,model}))} onLanguage={language => setConfig(current => ({...current,language}))} onRuntime={chooseRuntime}/></>;
  const renderFlowOptions = () => <details className="flow-options"><summary>生成后流程：{config.enable_clean || config.enable_translate ? "含文本处理" : "仅转写"}</summary><label className="check-row"><input type="checkbox" checked={config.enable_clean} disabled={!cleanAIReady} onChange={event => setConfig(current => ({...current,enable_clean:event.target.checked}))}/>自动整理</label><label className="check-row"><input type="checkbox" checked={config.enable_translate} disabled={!translateAIReady} onChange={event => setConfig(current => ({...current,enable_translate:event.target.checked}))}/>自动翻译</label>{config.enable_translate && <LanguagePicker mode="target" value={config.target_language} onChange={target_language => setConfig(current => ({...current,target_language}))}/>}<p>勾选即同意在此流程中将字幕文本发送至设置中的服务商（整理：{cleanAIProvider?.name || '未配置'}；翻译：{translateAIProvider?.name || '未配置'}）。可能产生服务商费用，处理地区依服务商设置；取消勾选后，新任务不再自动执行相应步骤；正在运行的任务请在任务面板中终止。音视频不会随这两个步骤上传。</p>{(!cleanAIReady || !translateAIReady) && <button className="button secondary" onClick={() => setShowAISettings(true)}>配置文本服务</button>}</details>;
  const renderProcessSettings = () => <div className="process-settings-content">
    {activeProcessStep === 'download' && renderMediaInspector()}
    {activeProcessStep === 'transcribe' && <section className="inspector-section"><h3>生成字幕</h3><p>{hasSegments ? "重新转写会生成候选结果，当前人工字幕会保留。" : "转写完成后结果会出现在编辑页。"}</p>{renderTranscriptionSetup()}{renderFlowOptions()}<button className="button primary" disabled={isProcessing || !runtimeForModel(inspectorModelId)} onClick={hasAudio && !config.enable_clean && !config.enable_translate ? doTranscribe : doGenerateSubtitles}>开始转写</button><button className="button secondary" onClick={() => { setQuickImport(''); localStorage.removeItem('subtitle_factory_quick_import'); }}>下次导入时重新确认配置</button>{activeProject && <TranscriptionCandidates key={`${activeProject.id}-${currentTask?.status}-${currentTask?.details?.worker_stopped}`} projectId={activeProject.id} revision={editorRevision.current} segments={segments} hasDraft={Object.keys(draftItems).length > 0} onSeek={handleSeek} onAccept={result => acceptEditorResult(activeProject.id,result)}/>}</section>}
    {activeProcessStep === 'clean' && <section className="inspector-section"><h3>AI 忠实整理</h3><div className="ai-summary-row"><span className="ai-logo">✦</span><div><strong>{cleanAIProvider?.name || (aiProviderState ? '未配置 AI' : '正在读取 AI 服务')}</strong><small>{cleanAIProvider?.model || '请先打开设置中心'}</small></div></div><label>参考单句长度 <span>{config.clean_target_length} 字</span><input type="range" min={16} max={100} step={2} value={config.clean_target_length} onChange={event => setConfig({ ...config, clean_target_length: Number(event.target.value) })}/></label><p>只修正明显错词、标点和断句，不改变原意。</p><button className="button primary" disabled={!hasSegments || isProcessing || !cleanAIReady} onClick={doClean}>确认并开始整理</button><button className="button secondary" disabled={!hasSegments || isProcessing} onClick={undoClean}>撤销上次整理</button></section>}
    {activeProcessStep === 'clean' && renderFailedBatchRecovery()}
    {activeProcessStep === 'translate' && <section className="inspector-section"><h3>AI 翻译</h3><label>目标语言<LanguagePicker mode="target" allowCustom allowNone value={config.target_language} onChange={target_language => void updateTargetLanguage(target_language)}/></label><label className="check-row"><input type="checkbox" checked={config.bilingual} onChange={event => setConfig({ ...config, bilingual: event.target.checked })}/> 导出时包含原文与译文</label><p>翻译结果会单独保存，可继续逐句校对。</p><button className="button primary" disabled={!hasSegments || isProcessing || !translateAIReady || config.target_language === 'none'} onClick={doTranslate}>确认并开始翻译</button></section>}
    {activeProcessStep === 'export' && <section className="inspector-section"><h3>快速导出</h3><p>字幕文件立即生成；带字幕视频只在本机后台压制，不上传媒体。</p><button className="button primary" onClick={() => setProjectWorkspace('export')}>前往导出工作区</button></section>}
    {currentTask?.status === 'failed' && <section className="recovery-card"><strong>{currentTask.error_code || '任务失败'}</strong><span>{currentTask.error || currentTask.message}</span>{currentTask.suggestion && <small>{currentTask.suggestion}</small>}<small>尝试次数：{currentTask.attempt || 1}</small>{currentTask.recoverable && <button onClick={retryCurrentFailure}>{recoveryActionLabel}</button>}{currentTask.available_actions?.includes('open_settings') && <button onClick={() => setShowAISettings(true)}>打开下载与存储设置</button>}</section>}
  </div>;

  return (
    <div data-ui-build={PROFESSIONAL_UI_MARKER} data-ui-layout={LIBRARY_WORKSPACE_UI_MARKER} className={`app pro-app theme-${theme} density-${density} ${motionEnabled ? '' : 'motion-off'} presentation-${presentationMode} ${showProjectWorkspace && activeProject ? 'workspace-active' : 'library-home'}`}
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
          {youtubeEnabled && <button className={`topbar-button ${showLinkPopover ? 'active' : ''}`} disabled={backendStatus !== 'connected'} onClick={() => setShowLinkPopover(value => !value)}><span>⌁</span>链接</button>}
          <button className={`task-status-pill ${backendStatus}`} onClick={() => setShowTaskDrawer(value => !value)} aria-expanded={showTaskDrawer}>
            <i className={`backend-dot ${backendStatus}`}/><span>{isProcessing ? (currentTask ? taskProgressLabel(currentTask) : '正在处理') : backendStatus === 'connected' ? (activeTaskCount ? `${activeTaskCount} 项后台任务` : '引擎就绪') : backendStatus === 'connecting' ? '正在启动' : '引擎异常'}</span>
          </button>
          <button className="icon-action" aria-label={theme === 'dark' ? '切换浅色模式' : '切换深色模式'} onClick={() => setTheme(value => value === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☀︎' : '◐'}</button>
          <button ref={settingsButtonRef} className="icon-action" aria-label="打开设置" onClick={() => setShowAISettings(true)}><img className="topbar-control-icon" src={settingsIcon} alt=""/></button>
        </div>
        {youtubeEnabled && showLinkPopover && <div className="link-popover">
          <div><strong>从 YouTube 链接创建</strong><button aria-label="关闭" onClick={() => setShowLinkPopover(false)}>×</button></div>
          <input autoFocus type="url" value={youtubeUrl} placeholder="https://www.youtube.com/watch?v=…" onChange={event => setYoutubeUrl(event.target.value)}/>
          <p>{isPlaylistUrl(youtubeUrl) ? '将先读取播放列表，确认批量转写与 AI 流水线后创建归组。' : '播放定位参数会自动移除并下载完整视频。'}</p>
          <button className="button primary" disabled={!youtubeUrl || backendStatus !== 'connected'} onClick={() => {
            setShowLinkPopover(false);
            if (isPlaylistUrl(youtubeUrl)) setPlaylistDialogUrl(youtubeUrl); else prepareImport({kind:'link',url:youtubeUrl});
          }}>{isPlaylistUrl(youtubeUrl) ? '解析播放列表' : '下载并生成字幕'}</button>
        </div>}
      </header>
      {pendingImport && <ImportFlow source={pendingImport} setup={<>{renderTranscriptionSetup()}{renderFlowOptions()}</>} busy={importBusy} canStart={!!runtimeForModel(inspectorModelId)} onClose={() => setPendingImport(null)} onStart={(generate,remember) => executeImport(pendingImport,generate,remember)}/>}
      <GlobalTaskDrawer open={showTaskDrawer} onClose={() => setShowTaskDrawer(false)} onOpenProject={projectId => {
        const project = projects.find(item => item.id === projectId)
          || playlistBatches.flatMap(batch => batch.items).find(item => item.project_id === projectId)?.project;
        if (project) void selectProject(project); else void selectProjectById(projectId);
        setShowTaskDrawer(false);
      }} onOpenSettings={() => { setShowTaskDrawer(false); setShowAISettings(true); }}/>
      {youtubeEnabled && playlistDialogUrl && <Suspense fallback={<DeferredPanel kind="overlay" label="正在打开播放列表工具…"/>}>
        <PlaylistBatchDialog
          url={playlistDialogUrl} workflow={playlistWorkflow} appSettings={appSettings} health={health}
          aiReady={{ clean: cleanAIReady, translate: translateAIReady }} onClose={() => setPlaylistDialogUrl(null)}
          onCreated={message => { setPlaylistDialogUrl(null); showToast(message, 4200); void refreshPlaylistBatches(); }}
        />
      </Suspense>}
      {filesystemAutomationEnabled && showProductionCenter && (() => {
        const batchModel = compatibleModel() || config.model;
        return <Suspense fallback={<DeferredPanel kind="overlay" label="正在打开批量生产工具…"/>}>
          <ProductionCenter workflow={{ model: batchModel, language: config.language, target_language: config.target_language, runtime: runtimeForModel(batchModel) }} onClose={() => setShowProductionCenter(false)} onProjectsCreated={() => setLibraryRefresh(value => value + 1)} onShowTasks={() => setShowTaskDrawer(true)}/>
        </Suspense>;
      })()}
      {showFirstRunPreflight && backendStatus === 'connected' && modelStatus && (() => {
        const configuredModel = String(appSettings.default_model || 'auto');
        const modelId = configuredModel === 'auto'
          ? String(modelStatus.recommended_model || 'small')
          : configuredModel;
        const model = modelStatus.models.find(item => item.id === modelId) || modelStatus.models[0];
        const recommended = model?.runtimes?.find(item => item.available && item.id !== 'cpu') || model?.runtimes?.find(item => item.available);
        return <div className="preflight-overlay" role="dialog" aria-modal="true" aria-label="首次运行预检"><section className="preflight-card"><header><small>首次运行</small><h2>本机准备就绪</h2><p>基础转写在本机完成；AI、OCR 和说话人模型只在首次使用对应功能时准备。</p></header><div className="preflight-checks"><span className={health?.runtime?.ffmpeg?.ok ? 'ok' : 'warning'}><i>{health?.runtime?.ffmpeg?.ok ? '✓' : '!'}</i><strong>FFmpeg</strong><small>{health?.runtime?.ffmpeg?.ok ? '可用' : '需要在设置中检查'}</small></span><span className={health?.runtime?.disk?.ok ? 'ok' : 'warning'}><i>{health?.runtime?.disk?.ok ? '✓' : '!'}</i><strong>磁盘</strong><small>{health?.runtime?.disk?.message || '已检查可用空间'}</small></span><span className={model?.ready ? 'ok' : 'warning'}><i>{model?.ready ? '✓' : '↓'}</i><strong>{model?.name || '默认模型'}</strong><small>{model?.ready ? '已就绪' : '首次转写时按需下载'}</small></span></div><div className="preflight-device"><strong>推荐运行设备</strong><span>{recommended?.name || 'CPU'}<small>{recommended?.engine || '本地运行'}</small></span></div><footer><button className="button primary" disabled={!recommended} onClick={() => { if (model && recommended) chooseRuntime(model.id, recommended.id); localStorage.setItem('subtitle_factory_preflight_v1', 'done'); setShowFirstRunPreflight(false); }}>确认并开始使用</button></footer></section></div>;
      })()}

      {backendStatus === 'error' && <div className="engine-error-banner"><strong>本地引擎未能启动</strong><span>打开设置查看 FFmpeg、模型与存储诊断。</span><button onClick={refreshHealth}>重新检查</button></div>}
      {uploadProgress !== null && <div className="upload-progress-banner" role="status"><span>正在导入视频</span><progress value={uploadProgress} max={100}/><strong>{uploadProgress}%</strong></div>}

      <div className={`studio-shell v05-shell ${showProjectWorkspace && activeProject ? `app-project project-view-${projectWorkspace}` : 'app-library'} ${inspectorMode ? 'inspector-open' : ''}`} style={{
        '--left-panel-width': `${leftPanelWidth}px`, '--right-panel-width': `${rightPanelWidth}px`,
      } as React.CSSProperties}>
        <aside className={`project-sidebar ${compactLibrary ? "library-list-view" : ""}`}>
          <header className="library-page-header"><div><small>字幕工厂</small><h1>你的项目</h1><p>{youtubeEnabled ? '选择一个项目继续工作，或从视频和链接开始新的字幕任务。' : '选择一个项目继续工作，或导入您有权处理的视频。'}</p></div><div>
            {filesystemAutomationEnabled && <button className="button secondary" disabled={backendStatus !== 'connected'} onClick={() => setShowProductionCenter(true)}>批量与监听</button>}
            <button className="button secondary" disabled={backendStatus !== 'connected'} onClick={importProjectPackage}>导入项目包</button>
            {youtubeEnabled && <button className="button secondary" disabled={backendStatus !== 'connected'} onClick={() => setShowLinkPopover(true)}>添加链接</button>}
            <button className="button primary" disabled={backendStatus !== 'connected'} onClick={handleImportLocal}>导入视频</button>
          </div></header>
          <section className="library-overview" aria-label="项目库概览">
            <div><span>匹配项目</span><strong>{libraryTotal}</strong><small>{trashProjects.length ? `${trashProjects.length} 个在回收站` : '全部保存在本机'}</small></div>
            <div><span>本页字幕</span><strong>{projects.reduce((total, project) => total + Number(project.segments_count || 0), 0)}</strong><small>可在项目库中全文搜索</small></div>
            {youtubeEnabled
              ? <div><span>批量任务</span><strong>{playlistBatches.filter(item => ['running', 'pending', 'paused', 'partial', 'failed'].includes(item.batch.status)).length}</strong><small>{playlistBatches.length ? `${playlistBatches.length} 个播放列表` : '暂无进行中的队列'}</small></div>
              : <div><span>隐私模式</span><strong>本地优先</strong><small>第三方媒体读取已关闭</small></div>}
            <div className={`library-runtime-card ${backendStatus}`}><span>本地引擎</span><strong>{backendStatus === 'connected' ? '就绪' : backendStatus === 'connecting' ? '启动中' : '需检查'}</strong><small>{backendStatus === 'connected' ? '媒体与转写工具可用' : backendStatus === 'connecting' ? '正在载入本机运行时' : '本地功能受限，AI 设置不应影响此状态'}</small></div>
          </section>
          <div className="library-switcher" role="tablist" aria-label="项目库视图">
            <button role="tab" aria-selected={libraryView === 'projects'} className={libraryView === 'projects' ? 'active' : ''} onClick={() => setLibraryView('projects')}>项目</button>
            <button role="tab" aria-selected={libraryView === 'trash'} className={libraryView === 'trash' ? 'active' : ''} onClick={() => setLibraryView('trash')}>回收站</button>
          </div>
          <LibraryControls page={libraryPage} pages={libraryPages} total={libraryTotal} loading={libraryLoading} error={libraryError} compact={compactLibrary} status={libraryStatus} onPage={setLibraryPage} onStatus={setLibraryStatus} onRetry={() => setLibraryRefresh(value => value+1)} onCompact={() => setCompactLibrary(value => { localStorage.setItem('subtitle_factory_library_compact',String(!value)); return !value; })}/>
          {libraryView === 'projects' && <div className="library-filters"><input type="search" value={librarySearch} onChange={event => setLibrarySearch(event.target.value)} placeholder="搜索项目或所有字幕" aria-label="搜索项目或所有字幕" onKeyDown={event => {
            if (event.key === 'Escape') {
              event.preventDefault();
              setLibrarySearch('');
              setLibrarySearchSelection(-1);
              return;
            }
            if (!librarySearchHits.length || !['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return;
            event.preventDefault();
            if (event.key === 'ArrowDown') setLibrarySearchSelection(value => Math.min(librarySearchHits.length - 1, value + 1));
            if (event.key === 'ArrowUp') setLibrarySearchSelection(value => Math.max(0, value <= 0 ? 0 : value - 1));
            if (event.key === 'Enter') {
              const hit = librarySearchHits[Math.max(0, librarySearchSelection)];
              if (hit) void openLibrarySearchHit(hit);
            }
          }}/><select aria-label="项目排序" value={librarySort} onChange={event => setLibrarySort(event.target.value)}><option value="updated_desc">最近更新</option><option value="created_desc">最近创建</option><option value="name_asc">名称 A–Z</option><option value="name_desc">名称 Z–A</option></select></div>}
          <div className="project-list">
            {libraryView === 'projects' && librarySearch.trim() && <section className="library-unified-results">
              {librarySearch.trim().length === 1
                ? <div className="search-input-hint">再输入一个字即可搜索全部字幕，避免扫描整个项目库。</div>
                : <>
                  <details className="search-filter-panel">
                    <summary>筛选字幕结果 <span>{Object.values(librarySearchFilters).filter(Boolean).length || ''}</span></summary>
                    <div>
                      <label>项目<select value={String(librarySearchFilters.project_id || '')} onChange={event => setLibrarySearchFilters(current => ({ ...current, project_id: event.target.value || undefined }))}><option value="">全部项目</option>{(librarySearchFacets.projects || []).map(item => <option value={item.id} key={item.id}>{item.title}</option>)}</select></label>
                      <label>分组<select value={String(librarySearchFilters.group_name || '')} onChange={event => setLibrarySearchFilters(current => ({ ...current, group_name: event.target.value || undefined }))}><option value="">全部分组</option>{(librarySearchFacets.groups || []).map(item => <option value={item} key={item}>{item}</option>)}</select></label>
                      <label>说话人<select value={String(librarySearchFilters.speaker_id || '')} onChange={event => setLibrarySearchFilters(current => ({ ...current, speaker_id: event.target.value || undefined }))}><option value="">全部说话人</option>{(librarySearchFacets.speakers || []).map(item => <option value={item.id} key={item.id}>{item.name} · {item.project_title}</option>)}</select></label>
                      <label>源语言<select value={String(librarySearchFilters.source_language || '')} onChange={event => setLibrarySearchFilters(current => ({ ...current, source_language: event.target.value || undefined }))}><option value="">全部</option>{Array.from(new Set((librarySearchFacets.projects || []).map(item => item.source_language).filter(Boolean))).map(item => <option value={item} key={item}>{languageLabel(item)}</option>)}</select></label>
                      <label>目标语言<select value={String(librarySearchFilters.target_language || '')} onChange={event => setLibrarySearchFilters(current => ({ ...current, target_language: event.target.value || undefined }))}><option value="">全部</option>{Array.from(new Set((librarySearchFacets.projects || []).map(item => item.target_language).filter(Boolean))).map(item => <option value={item} key={item}>{languageLabel(item)}</option>)}</select></label>
                      <label>创建日期从<input type="date" value={String(librarySearchFilters.created_from || '')} onChange={event => setLibrarySearchFilters(current => ({ ...current, created_from: event.target.value || undefined }))}/></label>
                      <label>到<input type="date" value={String(librarySearchFilters.created_to || '')} onChange={event => setLibrarySearchFilters(current => ({ ...current, created_to: event.target.value || undefined }))}/></label>
                      <button className="button secondary" onClick={() => setLibrarySearchFilters({})}>清除筛选</button>
                    </div>
                  </details>
                  <header className="search-result-heading"><strong>字幕命中</strong><span>{librarySearchLoading ? '搜索中…' : `${librarySearchTotal} 条`}</span></header>
                  <div className="subtitle-search-hits">{librarySearchHits.map((hit, index) => <button key={hit.segment_id} className={librarySearchSelection === index ? 'selected' : ''} onMouseEnter={() => setLibrarySearchSelection(index)} onClick={() => void openLibrarySearchHit(hit)}>
                    <span className="search-hit-time">{libraryTimecode(hit.start)}</span>
                    <span><strong>{hit.project_title}</strong><small>{hit.playlist_title ? `${hit.playlist_title} · ` : ''}{hit.group_name ? `${hit.group_name} · ` : ''}{hit.speaker_name || '未标说话人'} · 命中 {hit.match_fields.map(field => field === 'clean_text' ? '整理后原文' : field === 'translated_text' ? '译文' : field === 'raw_text' ? '原始转写' : field === 'speaker_name' ? '说话人' : '项目标题').join('、')}</small><p>{highlightedSnippet(hit.snippet, librarySearch)}</p></span>
                    <em>打开 ↗</em>
                  </button>)}</div>
                  {!librarySearchLoading && !librarySearchHits.length && <div className="search-empty">没有匹配的字幕。</div>}
                  {librarySearchTotal > 50 && <div className="search-pagination"><button disabled={librarySearchPage <= 1} onClick={() => setLibrarySearchPage(page => page - 1)}>上一页</button><span>{librarySearchPage} / {Math.ceil(librarySearchTotal / 50)}</span><button disabled={librarySearchPage >= Math.ceil(librarySearchTotal / 50)} onClick={() => setLibrarySearchPage(page => page + 1)}>下一页</button></div>}
                  <header className="search-result-heading project-hits-heading"><strong>项目命中</strong><span>{projects.length}</span></header>
                </>}
            </section>}
            {youtubeEnabled && libraryView === 'projects' && <PlaylistBatchGroups
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
                      <span className="project-card-copy"><strong>{project.title}</strong><small>{languageLabel(project.language)} · {project.segments_count} 条 · {project.created_at.slice(0, 10)}</small><span className="media-mode-badge">{projectReadiness(project)}</span>{project.latest_task_status && <em className={`project-task-hint ${project.latest_task_status}`}>最近任务：{taskLabel(project.latest_task_status)}{project.latest_task_status === 'failed' ? ` · ${project.latest_task_message || ''}` : ''}</em>}</span>
                    </button>
                    <button type="button" className="project-more" aria-label={`更多项目操作：${project.title}`} aria-haspopup="menu" aria-expanded={contextMenu?.project.id === project.id} onClick={event => openProjectMenu(event, project)}>•••</button>
                    {editingGroup && <div className="project-group-editor"><AppSelect value={groupDraft} onChange={setGroupDraft} label="项目分组" placeholder="搜索或输入分组名称" searchable allowCustom options={knownProjectGroups.map(name=>({value:name,label:name}))}/><button onClick={() => void saveProjectGroup(project)}>保存</button><button aria-label="取消" onClick={() => setGroupEditorProjectId(null)}>×</button></div>}
                  </div>;
                })}</div>}
              </section>;
            })}
            {libraryView === 'projects' && backendStatus === 'connecting' && !projects.length && !playlistBatches.length && <div className="library-skeleton" aria-label="正在载入项目"><i/><i/><i/></div>}
            {libraryView === 'projects' && backendStatus !== 'connecting' && !projects.length && !playlistBatches.length && <div className="project-empty"><span>▱</span><strong>还没有项目</strong><small>{youtubeEnabled ? '导入视频、拖放文件或粘贴链接开始。' : '导入或拖放本地视频开始。'}</small></div>}
            {libraryView === 'trash' && trashProjects.slice(0,40).map(project => <button className={`project-card trash-card ${removingProjectIds.has(project.id) ? 'removing' : ''}`} key={project.id} onContextMenu={event => openProjectMenu(event, project, true)} onClick={event => openProjectMenu(event, project, true)}>
              <span className="project-thumb"><span className="project-thumb-fallback">♲</span></span><span className="project-card-copy"><strong>{project.title}</strong><small>{project.deleted_at?.slice(0, 10) || '已删除'} · 媒体仍保留</small></span><span className="project-more">•••</span>
            </button>)}
            {libraryView === 'trash' && !trashProjects.length && <div className="project-empty"><span>♲</span><strong>回收站为空</strong><small>移入回收站的项目会保留媒体与字幕。</small></div>}
          </div>
          <div className="sidebar-footer">
            {libraryView === 'trash' ? <button className="sidebar-action danger" disabled={!trashProjects.length} onClick={clearTrash}>清空回收站</button> : <><button className="sidebar-action" onClick={handleImportLocal}>＋ 导入视频</button>{filesystemAutomationEnabled && <button className="sidebar-action" onClick={() => setShowProductionCenter(true)}>▦ 批量与监听</button>}<button className="sidebar-action" onClick={importProjectPackage}>⇧ 导入项目包</button>{youtubeEnabled && <button className="sidebar-action" onClick={() => setShowLinkPopover(true)}>⌁ 添加链接</button>}</>}
          </div>
        </aside>

        <div className="panel-resizer panel-resizer-left" role="separator" aria-label="调整项目库宽度" tabIndex={0} onPointerDown={event => beginResize('left', event)} onKeyDown={event => { if (event.key === 'ArrowLeft') setLeftPanelWidth(value => Math.max(210, value - 16)); if (event.key === 'ArrowRight') setLeftPanelWidth(value => Math.min(430, value + 16)); }}/>

        <main className={`editor-workspace ${projectWorkspace === 'subtitles' ? 'workspace-editor' : ''} ${subtitleFocus ? 'subtitle-focus' : ''}`}>
          <nav className="project-workspace-nav" aria-label="项目工作区">
            {([['subtitles', '编辑', `${segments.length} 条字幕`], ['style', '样式', '外观与位置'], ['export', '导出', '文件与视频']] as const).map(([id, label, detail]) => <button key={id} className={projectWorkspace === id ? 'active' : ''} aria-current={projectWorkspace === id ? 'page' : undefined} onClick={() => { setProjectWorkspace(id); if (id === 'subtitles') setSubtitleFocusRequest(request => request + 1); setInspectorMode(null); }}><span>{label}</span><small>{detail}</small></button>)}
            <button className="workspace-tools-button" onClick={() => { setToolsTab('process'); setToolsOpen(true); setInspectorMode(null); }}><span>任务与工具</span><small>{currentTask ? currentTask.message || '运行中' : '质检、OCR、说话人'}</small></button>
          </nav>
          <header className="workspace-page-heading">
            <div><small>{activeProject?.title}</small><h1>{projectWorkspace === 'subtitles' ? '字幕编辑' : projectWorkspace === 'style' ? '字幕样式' : '导出交付'}</h1></div>
            {isProcessing && <div className="page-task-status"><i/><span>{currentTask ? taskProgressLabel(currentTask) : '正在处理'}</span></div>}
          </header>
          <div className="workbench-split">
          {projectWorkspace === 'subtitles' && <EditorWorkbench
            toolbar={<><button className="button primary" onClick={() => openWorkflowStep('transcribe')}>{hasSegments ? '重新转写' : '生成字幕'}</button><button className="button secondary" onClick={importSubtitleFile}>导入字幕</button><button className="button secondary" onClick={() => openWorkflowStep('translate')}>翻译</button><span className="editor-save-status" role="status">{segmentsLoading ? '正在加载字幕…' : `${segments.length} 条字幕`}</span></>}
            player={activeProject && canPlayMedia && activeProject.video_url ? <Suspense fallback={<DeferredPanel kind="player" label="正在加载本地播放器…"/>}><SubtitlePlayer initialTime={currentTime} ref={videoPlayerRef} projectId={activeProject.id} videoUrl={api.getBackendMediaUrl(activeProject.video_url) || ''} segments={segments} style={subtitleStyle} activeIdx={activeSegmentIdx} onTimeUpdate={handleTimeUpdate} onDurationChange={setVideoDuration} onStyleChange={handleStyleChange} presentationMode={presentationMode} onPresentationModeChange={setPresentationMode}/></Suspense>
              : <div className="viewer-welcome"><span>▶</span><h2>开始创作字幕</h2><p>{youtubeEnabled ? '导入视频或粘贴 YouTube 链接' : '导入您有权处理的本地视频'}</p><div><button className="button primary" onClick={handleImportLocal}>导入视频</button>{youtubeEnabled && <button className="button secondary" onClick={() => setShowLinkPopover(true)}>添加链接</button>}</div></div>}
            editor={segmentsLoading ? <div className="editor-load-state" role="status">正在载入字幕…</div> : segmentsError ? <div className="editor-load-state" role="alert">{segmentsError}<button onClick={() => activeProject && void refreshSegments(activeProject.id)}>重新加载</button></div> : <SubtitleTable segments={segments} currentTime={currentTime} activeIdx={activeSegmentIndex} entryFocusIdx={subtitleEntryFocusIndex} entryFocusRequest={subtitleFocusRequest} onSeek={handleSeek} onInspect={setSegmentInspector} onUpdate={handleUpdateSegment} onReplaceAll={replaceSegments} onSplit={splitSegment} onMerge={mergeSegments} onUndo={undoEditor} onRedo={redoEditor} saveState={editorSaveState} draftCount={Object.keys(draftItems).length} draftIsStale={draftIsStale} onPreviewDraft={previewDraft} onCommitDraft={commitDraft} onDiscardDraft={discardDraft} onAutoScrollChange={setAutoScrollTable} autoScroll={autoScrollTable} disabled={editorBusy || segmentsLoading}/>}
            timeline={activeProject && <SubtitleTimeline projectId={activeProject.id} segments={segments} currentTime={currentTime} duration={videoDuration} onSeek={handleSeek} onUpdateTime={(index, update) => { if (!editorBusy) void handleUpdateSegment(index, update); }}/ >}
            inspector={segmentInspector !== null && segments.find(segment => segment.index === segmentInspector) && (() => { const segment = segments.find(item => item.index === segmentInspector)!; return <><button className="button secondary" onClick={() => setSegmentInspector(null)}>关闭属性</button><h3>字幕 {segment.index}</h3><label>开始时间（秒）<input key={`start-${segment.id}-${segment.start}`} type="number" min={0} step={0.01} defaultValue={segment.start} disabled={editorBusy} onBlur={event => { const start = Number(event.target.value); if (Number.isFinite(start) && start >= 0 && start < segment.end && start !== segment.start) void handleUpdateSegment(segment.index, {start}); }}/></label><label>结束时间（秒）<input key={`end-${segment.id}-${segment.end}`} type="number" min={segment.start} step={0.01} defaultValue={segment.end} disabled={editorBusy} onBlur={event => { const end = Number(event.target.value); if (Number.isFinite(end) && end > segment.start && end !== segment.end) void handleUpdateSegment(segment.index, {end}); }}/></label><p>说话人：{segment.speaker || "未分配"}</p><button className="button secondary" onClick={() => { setToolsTab("smart"); setToolsOpen(true); }}>管理说话人</button><button className="button secondary" onClick={() => setProjectWorkspace('style')}>调整字幕样式</button></>; })()}
          />}
          {projectWorkspace === 'style' && <section className="task-page style-task-page">
            <div className="style-canvas"><StyleTemplateBar style={subtitleStyle} onApply={handleStyleChange}/><header><h2>实时外观预览</h2><p>在接近成片的画面比例中调整字幕，不受其他工具干扰。</p></header><div className="style-canvas-stage">{activeProject && canPlayMedia && activeProject.video_url ? <Suspense fallback={<DeferredPanel kind="player" label="正在加载样式预览…"/>}><SubtitlePlayer initialTime={currentTime} ref={videoPlayerRef} projectId={activeProject.id} videoUrl={api.getBackendMediaUrl(activeProject.video_url) || ''} segments={segments} style={subtitleStyle} activeIdx={activeSegmentIdx} onTimeUpdate={handleTimeUpdate} onDurationChange={setVideoDuration} onStyleChange={handleStyleChange} presentationMode={presentationMode} onPresentationModeChange={setPresentationMode}/></Suspense> : <div className="style-preview-card"><span>为每一句话找到恰好的位置。</span><small>Give every line its perfect place.</small></div>}</div></div>
            <aside className="style-controls-page"><header><small>字幕检查器</small><h2>字体与排版</h2></header><SubtitleStylePanel style={subtitleStyle} onChange={handleStyleChange}/></aside>
          </section>}
          {projectWorkspace === 'export' && <ExportWorkspace bilingual={config.bilingual} onBilingual={bilingual => {setConfig(current => ({...current,bilingual}));localStorage.setItem('subtitle_factory_export_bilingual',String(bilingual));}} hasSegments={hasSegments} hasVideo={hasLocalVideo} busy={taskStarting || (isProcessing && currentTask?.type === 'render') || Object.keys(draftItems).length > 0} onExport={format => void doExport(format)} onPackage={media => void exportProjectPackage(media)} task={<>
{currentTask && ['render','export'].includes(currentTask.type) && <div className={`export-task-card ${currentTask.status}`}><div><strong>{currentTask.message || '导出任务'}</strong><small>{currentTask.status === 'success' ? '文件已准备完成' : '可离开此页面，任务会继续运行'}</small></div><progress max={100} value={currentTask.progress || 0}/><span>{Math.round(currentTask.progress || 0)}%</span></div>}</>}/>}
          </div>
          {toolsOpen && <WorkspacePanel title="任务与工具" onClose={() => setToolsOpen(false)}><nav className="tool-panel-tabs" aria-label="工具分类">{([['process','任务'],['quality','质检与术语'],['smart','OCR 与说话人'],['content','内容']] as const).map(([id,label]) => <button key={id} aria-pressed={toolsTab === id} onClick={() => setToolsTab(id)}>{label}</button>)}<button onClick={() => setShowTaskDrawer(true)}>全部后台任务</button></nav>          {toolsTab === 'quality' && activeProject && <section className="task-page quality-task-page"><div className="quality-page-grid"><QualityPanel projectId={activeProject.id} segments={segments} revision={editorRevision.current} onEditorResult={result => acceptEditorResult(activeProject.id, result)} onSeek={time => { handleSeek(time); setProjectWorkspace('subtitles'); }}/><GlossaryPanel projectId={activeProject.id}/></div></section>}
          {toolsTab === 'smart' && activeProject && <section className="task-page smart-task-page"><SmartToolsPanel projectId={activeProject.id} revision={editorRevision.current} duration={videoDuration} onEditorResult={result => acceptEditorResult(activeProject.id, result)} onProjectChanged={() => void refreshActiveProject(activeProject.id)}/></section>}
          {toolsTab === 'process' && <TaskWorkspace steps={compactSteps} selected={activeProcessStep} onSelect={setSelectedStep} task={currentTask} settings={renderProcessSettings()} onPause={toggleTaskPause} onCancel={cancelCurrentTask} diagnostics={<details className="process-diagnostics"><summary>任务日志与诊断 <span>{processLogs.length}</span></summary><div><ProcessTimeline steps={processSteps} currentStepId={activeProcessStep} totalProgress={totalProgress} onStepClick={setSelectedStep}/><ProcessLogViewer logs={processLogs} collapsed={false} onToggle={() => undefined} onClear={() => setProcessLogs([])}/></div></details>}/>}
          {toolsTab === 'content' && activeProject && <Suspense fallback={<DeferredPanel label="正在打开内容工作区…"/>}>
            <ContentCenter
              project={activeProject}
              projectRevision={editorRevision.current}
              hasSegments={hasSegments}
              onPreview={previewClipRange}
              onMessage={message => showToast(message, 4200)}
            />
          </Suspense>}
</WorkspacePanel>}
        </main>


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
      {showAISettings && <Suspense fallback={<DeferredPanel kind="settings" theme={theme} label="正在打开设置中心…"/>}>
        <SettingsCenter open onClose={() => setShowAISettings(false)} returnFocusRef={settingsButtonRef} config={config} onConfigChange={setConfig} appSettings={appSettings} onAppSettingsChange={applyAppSettings} onAIProvidersChange={setAIProviderState} theme={theme} onThemeChange={setTheme} motionEnabled={motionEnabled} onMotionEnabledChange={setMotionEnabled} density={density} onDensityChange={setDensity} health={health} onRefreshHealth={refreshHealth} modelStatus={modelStatus} onRefreshModels={refreshModels} onOpenLogs={() => { setToolsTab('process'); setToolsOpen(true); setShowProjectWorkspace(!!activeProject); setInspectorMode(null); }}/>
      </Suspense>}
    </div>
  );
}

export default App;
