// 字幕工厂 - 主应用组件（集成字幕播放器 + 流程可视化）

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type {
  Project, SubtitleSegment, TaskStatus, ProcessingConfig,
  SourceLang, TargetLang, ModelSize, ExportFormat,
  ProcessStep, ProcessLogEntry, TaskStepStatus,
  SubtitleStyleSettings, SubtitleStats, AISettings, AIProviderPreset,
} from './types';
import * as api from './api/backend';
import './App.css';

import SubtitlePlayer, { type SubtitlePlayerHandle } from './components/SubtitlePlayer';
import { loadSubtitleStyle, saveSubtitleStyle } from './subtitleStyle';
import ProcessTimeline from './components/ProcessTimeline';
import ProcessLogViewer from './components/ProcessLogViewer';
import SubtitleStatsPanel from './components/SubtitleStatsPanel';
import SubtitleTimeline from './components/SubtitleTimeline';
import AISettingsDialog from './components/AISettingsDialog';
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
  const [showSettings, setShowSettings] = useState(false);
  const [showLogPanel, setShowLogPanel] = useState(true);
  const [autoScrollTable, setAutoScrollTable] = useState(true);
  const [showAISettings, setShowAISettings] = useState(false);
  const [backendStatus, setBackendStatus] = useState<'connecting' | 'connected' | 'error'>('connecting');
  const [aiSettings, setAISettings] = useState<AISettings | null>(null);
  const [aiPresets, setAIPresets] = useState<AIProviderPreset[]>([]);
  const [toast, setToast] = useState('');
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [modelStatus, setModelStatus] = useState<Awaited<ReturnType<typeof api.getTranscriptionModels>> | null>(null);
  const [theme, setTheme] = useState<'dark' | 'light'>(() => localStorage.getItem('subtitle_factory_theme') === 'light' ? 'light' : 'dark');
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
  const downloadedRenderTask = useRef('');

  useEffect(() => {
    localStorage.setItem('subtitle_factory_theme', theme);
  }, [theme]);

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

  useEffect(() => {
    if (backendStatus !== 'connected') return;
    api.getTranscriptionModels(activeProject?.id, config.language).then(setModelStatus).catch(() => setModelStatus(null));
  }, [activeProject?.id, backendStatus, config.language]);

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
        await api.checkHealth();
        if (stopped) return;
        setBackendStatus('connected');
        const data = await api.listProjects();
        if (!stopped) setProjects(data.projects);
        const ai = await api.getAISettings();
        if (!stopped) { setAISettings(ai.settings); setAIPresets(ai.presets); }
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

  // ── Start background task ──
  const startTask = useCallback(async (
    name: string, stepId: string,
    fn: () => Promise<{ task_id: string }>,
    interval: number = 1000
  ) => {
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
    }
  }, [addLog, ingestTaskLogs, setStepStatus, syncProcessFromTask]);

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
      if (newProj) selectProject(newProj);
      return r.project_id;
    } catch (e: any) {
      addLog('error', '创建项目', `创建失败: ${e.message}`);
      setStepStatus('create', 'failed', 0, e.message);
    }
  }, [youtubeUrl, config, addLog, selectProject, setStepStatus]);

  // ── Full pipeline ──
  const handleFullPipeline = useCallback(async () => {
    const pid = await handleCreateProject();
    if (!pid) return;
    if (youtubeUrl) {
      await startTask('自动生成字幕', 'download', () => api.startWorkflow(pid, {
        source_url: youtubeUrl, model: config.model, language: config.language,
      }));
    }
  }, [youtubeUrl, config.model, config.language, handleCreateProject, startTask]);

  // ── Import local videos; a project is created only after a real selection. ──
  const importFiles = useCallback(async (files: File[]) => {
    const supported = files.filter(file => /\.(mp4|mkv|mov|webm|avi)$/i.test(file.name));
    if (!supported.length) {
      setToast('请选择 MP4、MKV、MOV、WebM 或 AVI 视频');
      return;
    }
    for (const file of supported) {
      try {
        const created = await api.createProject({
          source_type: 'local', title: file.name,
          language: config.language, target_language: config.target_language,
        });
        setUploadProgress(0);
        addLog('info', '导入视频', `正在导入 ${file.name}`);
        const result = await api.importLocalVideo(created.project_id, file, {
          autostart: true, model: config.model, language: config.language,
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
  }, [addLog, config.language, config.model, config.target_language, ingestTaskLogs, selectProject, syncProcessFromTask]);

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
    setSubtitleStats(null);
    startTask('转写', 'transcribe', () => api.startTranscribe(activeProject.id, config.language, config.model));
  }, [activeProject, config.language, config.model, startTask]);

  const doGenerateSubtitles = useCallback(() => {
    if (!activeProject) return;
    setSubtitleStats(null);
    startTask('自动生成字幕', 'transcribe', () => api.startWorkflow(activeProject.id, {
      model: config.model, language: config.language,
    }));
  }, [activeProject, config.language, config.model, startTask]);

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
    if (!activeProject) return;
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

  const quickSwitchAI = useCallback(async (model: string) => {
    if (!aiSettings || model === aiSettings.model) return;
    try {
      const result = await api.saveAISettings({ ...aiSettings, model, api_key: '' });
      setAISettings(result.settings);
      setToast(`AI 模型已切换为 ${model}`);
    } catch (error: any) {
      setToast(`切换失败：${error.message}`);
    }
    window.setTimeout(() => setToast(''), 3000);
  }, [aiSettings]);

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

  // ── Step indicators ──
  const hasAudio = activeProject?.audio_path;
  const hasSegments = segments.length > 0;
  const isProcessing = !!(currentTask && (currentTask.status === 'running' || currentTask.status === 'pending' || currentTask.status === 'paused'));
  const activeSegmentIndex = activeSegmentIdx >= 0 ? segments[activeSegmentIdx]?.index ?? -1 : -1;
  const activeAIPreset = aiPresets.find(item => item.id === aiSettings?.provider);
  const quickModels = Array.from(new Set([...(activeAIPreset?.models || []), aiSettings?.model || ''].filter(Boolean)));

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
    <div className={`app pro-app theme-${theme} ${theaterMode ? 'theater-active' : ''}`}
      onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
      onDrop={event => { event.preventDefault(); void importFiles(Array.from(event.dataTransfer.files)); }}>
      <header className="studio-topbar">
        <div className="brand-block"><img className="brand-mark" src={appIcon} alt="字幕工厂"/><div><strong>字幕工厂</strong><small>TRANSCRIPTION STUDIO</small></div></div>
        <div className="active-project-title">
          <strong>{activeProject?.title || '未打开项目'}</strong>
          {activeProject && <span>{segments.length} 条字幕 · {activeProject.language === 'auto' ? '自动识别' : activeProject.language.toUpperCase()}</span>}
        </div>
        <div className="topbar-status">
          <span className={`backend-dot ${backendStatus}`} />
          <span className="backend-label">{backendStatus === 'connected' ? '本地引擎就绪' : backendStatus === 'connecting' ? '正在启动引擎' : '引擎连接失败'}</span>
          <button className={`ai-status-chip ${aiSettings?.last_test_status === 'success' ? 'verified' : ''}`} onClick={() => setShowAISettings(true)}>
            <span className="ai-spark">✦</span>
            <span><small>{activeAIPreset?.name || aiSettings?.provider || 'AI 未配置'}</small><strong>{aiSettings?.model || '选择模型'}</strong></span>
            {aiSettings?.last_test_status === 'success' && <i title={`最近测试 ${aiSettings.last_latency_ms}ms`}>●</i>}
          </button>
          {quickModels.length > 0 && <select className="quick-model-select" aria-label="快速切换 AI 模型" value={aiSettings?.model || ''}
            onChange={event => quickSwitchAI(event.target.value)}>
            {quickModels.map(model => <option key={model} value={model}>{model}</option>)}
          </select>}
          <button className="icon-action theme-toggle" aria-label={theme === 'dark' ? '切换亮色模式' : '切换深色模式'}
            onClick={() => setTheme(value => value === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☀' : '☾'}</button>
          <button className="icon-action" aria-label="AI 接入管理" onClick={() => setShowAISettings(true)}>
            <img className="topbar-control-icon" src={settingsIcon} alt=""/>
          </button>
        </div>
      </header>

      {backendStatus === 'error' && <div className="engine-error-banner">
        <strong>本地后端未能启动</strong><span>请关闭可能占用 8000 端口的程序后重新打开 App；详细原因已写入应用数据目录。</span>
      </div>}
      {uploadProgress !== null && <div className="upload-progress-banner" role="status">
        <span>正在导入视频</span><progress value={uploadProgress} max={100}/><strong>{uploadProgress}%</strong>
      </div>}

      <div className="studio-shell" style={{
        '--left-panel-width': `${leftPanelWidth}px`, '--right-panel-width': `${rightPanelWidth}px`,
      } as React.CSSProperties}>
        <aside className="project-sidebar">
          <div className="sidebar-header"><strong>项目库</strong><span>{projects.length}</span></div>
          <div className="source-create-card">
            <input type="url" placeholder="粘贴 YouTube 链接" value={youtubeUrl} onChange={event => setYoutubeUrl(event.target.value)} />
            <button className="primary-wide" disabled={!youtubeUrl || backendStatus !== 'connected'} onClick={handleFullPipeline}>下载并生成字幕</button>
            <button className="secondary-wide" disabled={backendStatus !== 'connected'} onClick={handleImportLocal}>＋ 导入视频并生成字幕</button>
            <small className="drop-hint">也可以拖入一个或多个视频</small>
          </div>

          <div className="project-list">
            <datalist id="project-group-options">
              {knownProjectGroups.map(name => <option key={name} value={name}/>) }
            </datalist>
            {projectGroups.map(group => {
              const collapsed = collapsedProjectGroups.has(group.key);
              return <section className="project-group" key={group.key}>
                <button className="project-group-header" aria-expanded={!collapsed} onClick={() => toggleProjectGroup(group.key)}>
                  <span><i>{collapsed ? '▸' : '▾'}</i>{group.label}</span><small>{group.projects.length}</small>
                </button>
                {!collapsed && <div className="project-group-items">
                  {group.projects.map(project => {
                    const thumbnailUrl = api.getProjectThumbnailUrl(project);
                    const editingGroup = groupEditorProjectId === project.id;
                    return <div className={`project-card-shell ${activeProject?.id === project.id ? 'active' : ''}`} key={project.id}>
                      <button className={`project-card ${project.source_type === 'youtube' ? 'has-group-action' : ''} ${activeProject?.id === project.id ? 'active' : ''}`}
                        onClick={() => selectProject(project)}>
                        <span className="project-thumb">
                          <span className="project-thumb-fallback">{project.source_type === 'youtube' ? '▶' : '▣'}</span>
                          {thumbnailUrl && <img key={thumbnailUrl} src={thumbnailUrl} alt="" loading="lazy"
                            onLoad={event => { event.currentTarget.style.display = ''; }}
                            onError={event => { event.currentTarget.style.display = 'none'; }} />}
                        </span>
                        <span className="project-card-copy"><strong>{project.title}</strong><small>{project.segments_count} 条字幕 · {project.created_at.slice(0, 10)}</small></span>
                      </button>
                      {project.source_type === 'youtube' && <button id={`project-group-action-${project.id}`} className={`project-group-action ${editingGroup ? 'active' : ''}`}
                        aria-label={`设置“${project.title}”的分组`} aria-expanded={editingGroup}
                        title="设置分组" onClick={() => openProjectGroupEditor(project)}>⌄</button>}
                      {editingGroup && <div className="project-group-editor">
                        <input autoFocus list="project-group-options" maxLength={40} value={groupDraft}
                          aria-label="分组名称" placeholder="输入分组名，留空为未分组"
                          onChange={event => setGroupDraft(event.target.value)}
                          onKeyDown={event => {
                            if (event.key === 'Enter') { event.preventDefault(); void saveProjectGroup(project); }
                            if (event.key === 'Escape') setGroupEditorProjectId(null);
                          }}/>
                        <button className="project-group-save" onClick={() => void saveProjectGroup(project)}>保存</button>
                        <button className="project-group-cancel" aria-label="取消设置分组" onClick={() => setGroupEditorProjectId(null)}>✕</button>
                      </div>}
                    </div>;
                  })}
                </div>}
              </section>;
            })}
            {projects.length === 0 && <div className="project-empty">还没有项目<br/><small>导入视频或粘贴 YouTube 链接</small></div>}
          </div>

          <button className="transcribe-settings-toggle" onClick={() => setShowSettings(value => !value)}>转写参数 <span>{showSettings ? '−' : '+'}</span></button>
          {showSettings && <div className="compact-settings">
            <label>转写模型<select value={config.model} onChange={event => setConfig({ ...config, model: event.target.value as ModelSize })}>
              <option value="auto">自动选择 · 推荐</option><option value="small">Whisper Small · 快速</option><option value="medium">Whisper Medium · 均衡</option><option value="large-v3">Whisper Large V3 · 精准</option><option value="parakeet-tdt-0.6b-v3-coreml">Parakeet V3 · Core ML</option>
            </select></label>
            <label>源语言<select value={config.language} onChange={event => setConfig({ ...config, language: event.target.value as SourceLang })}>
              <option value="auto">自动检测</option><option value="en">英语</option><option value="zh">中文</option><option value="ja">日语</option>
            </select></label>
            <label>翻译为<select value={config.target_language} onChange={event => setConfig({ ...config, target_language: event.target.value as TargetLang })}>
              <option value="zh">中文</option><option value="en">英语</option><option value="ja">日语</option><option value="none">不翻译</option>
            </select></label>
            {modelStatus && <div className="model-readiness">
              <strong>推荐：{modelStatus.models.find(item => item.id === modelStatus.recommended_model)?.name || modelStatus.recommended_model}</strong>
              {modelStatus.audio && <small className={modelStatus.audio.ok ? 'ready' : 'warning'}>{modelStatus.audio.ok ? `音频已就绪 · ${modelStatus.audio.duration}s` : modelStatus.audio.message}</small>}
              {modelStatus.models.filter(item => item.id.includes('parakeet')).map(item => <small key={item.id} className={item.ready ? 'ready' : 'warning'}>{item.name}：{item.ready ? '已就绪' : item.download_required ? '首次使用需准备模型' : '不可用'}</small>)}
            </div>}
          </div>}
        </aside>

        <div className="panel-resizer panel-resizer-left" role="separator" aria-label="调整项目库宽度" tabIndex={0}
          onPointerDown={event => beginResize('left', event)}
          onKeyDown={event => {
            if (event.key === 'ArrowLeft') setLeftPanelWidth(value => Math.max(210, value - 16));
            if (event.key === 'ArrowRight') setLeftPanelWidth(value => Math.min(430, value + 16));
          }} />

        <main className="editor-workspace">
          <section className="fixed-viewer" style={{ '--viewer-height': `${viewerHeight}px` } as React.CSSProperties}>
            {activeProject?.video_path ? <SubtitlePlayer
              ref={videoPlayerRef} videoUrl={api.getVideoUrl(activeProject.id)} segments={segments}
              style={subtitleStyle} activeIdx={activeSegmentIdx} onTimeUpdate={handleTimeUpdate}
              onDurationChange={setVideoDuration} onStyleChange={handleStyleChange}
              theaterMode={theaterMode} onTheaterModeChange={setTheaterMode}
            /> : <div className="viewer-welcome"><span>▶</span><h2>专业字幕预览区</h2><p>从左侧选择项目或导入视频</p></div>}
          </section>

          {activeProject && <SubtitleTimeline segments={segments} currentTime={currentTime} duration={videoDuration} onSeek={handleSeek} />}

          <div className="viewer-resizer" role="separator" aria-label="调整播放器高度" tabIndex={0}
            onPointerDown={event => beginResize('viewer', event)}
            onKeyDown={event => {
              if (event.key === 'ArrowUp') setViewerHeight(value => Math.max(260, value - 20));
              if (event.key === 'ArrowDown') setViewerHeight(value => Math.min(window.innerHeight - 250, value + 20));
            }}><span /></div>

          {currentTask && isProcessing && <section className={`active-task-strip ${currentTask.status === 'paused' ? 'paused' : ''}`}>
            <div><strong>{currentTask.status === 'paused' ? '任务已暂停' : currentTask.message || '正在处理'}</strong><span>{Math.round(currentTask.progress)}%</span></div>
            <div className="task-progress"><div style={{ width: `${currentTask.progress}%` }} /></div>
            <div className="task-controls">
              <button onClick={toggleTaskPause}>{currentTask.status === 'paused' ? '继续' : '暂停'}</button>
              <button className="task-stop-button" onClick={cancelCurrentTask}>终止</button>
            </div>
          </section>}

          <section className="transcript-pane">
            {activeProject ? <SubtitleTable
              segments={segments} currentTime={currentTime} activeIdx={activeSegmentIndex}
              onSeek={handleSeek} onUpdate={handleUpdateSegment} onAutoScrollChange={setAutoScrollTable}
              autoScroll={autoScrollTable} disabled={isProcessing}
            /> : <div className="transcript-empty">字幕将在这里按时间排列并可直接编辑</div>}
          </section>
        </main>

        <div className="panel-resizer panel-resizer-right" role="separator" aria-label="调整操作面板宽度" tabIndex={0}
          onPointerDown={event => beginResize('right', event)}
          onKeyDown={event => {
            if (event.key === 'ArrowLeft') setRightPanelWidth(value => Math.min(480, value + 16));
            if (event.key === 'ArrowRight') setRightPanelWidth(value => Math.max(280, value - 16));
          }} />

        <aside className="inspector-sidebar">
          <section className="inspector-section workflow-section">
            <div className="inspector-heading"><strong>处理流程</strong><span>{totalProgress}%</span></div>
            {activeProject?.video_path && !hasSegments && <button className="workflow-button primary main-workflow-action" disabled={isProcessing} onClick={doGenerateSubtitles}><b>▶</b><span><strong>{currentTask?.status === 'failed' ? '重新生成字幕' : '一键生成字幕'}</strong><small>自动提取音频并完成转写</small></span></button>}
            {currentTask?.status === 'failed' && currentTask.recoverable && <div className="recovery-card">
              <strong>{currentTask.error_code || '转写失败'}</strong>
              <span>{currentTask.error || currentTask.message}</span>
              <small>已有字幕不会被覆盖</small>
              <button onClick={recoverTranscription}>选择备用模型重试</button>
            </div>}
            <button className="workflow-button" disabled={!activeProject || isProcessing} onClick={doExtractAudio}><b>01</b><span><strong>提取音频</strong><small>内置媒体引擎 · 16kHz</small></span></button>
            <button className="workflow-button primary" disabled={!hasAudio || isProcessing} onClick={doTranscribe}><b>02</b><span><strong>开始转写</strong><small>{config.model.startsWith('parakeet-') ? 'Parakeet V3 · Core ML' : `Whisper ${config.model}`}</small></span></button>
            <button className="workflow-button ai" disabled={!hasSegments || isProcessing || !aiSettings?.has_api_key} onClick={doClean}><b>03</b><span><strong>AI 忠实整理</strong><small>保留原意，只修正明显错误与断句</small></span></button>
            <label className="clean-length-control">参考单句长度 <span>{config.clean_target_length} 字符</span>
              <input type="range" min={16} max={100} step={2} value={config.clean_target_length}
                onChange={event => setConfig({ ...config, clean_target_length: Number(event.target.value) })}/>
              <small>仅作排版偏好；完整长句不会被强行截断</small>
            </label>
            <button className="workflow-button ai" disabled={!hasSegments || isProcessing || !aiSettings?.has_api_key || config.target_language === 'none'} onClick={doTranslate}><b>04</b><span><strong>AI 翻译</strong><small>目标：{config.target_language.toUpperCase()}</small></span></button>
            <button className="undo-clean-button" disabled={!hasSegments || isProcessing} onClick={undoClean}>↶ 撤销上次 AI 整理</button>
          </section>

          <section className="inspector-section ai-summary-card">
            <div className="inspector-heading"><strong>当前 AI</strong><button onClick={() => setShowAISettings(true)}>管理</button></div>
            <div className="ai-summary-row"><span className="ai-logo">✦</span><div><strong>{activeAIPreset?.name || aiSettings?.provider || '未配置'}</strong><small>{aiSettings?.model || '请接入模型'}</small></div></div>
            <div className={`ai-connection-state ${aiSettings?.last_test_status || 'unknown'}`}>
              {aiSettings?.last_test_status === 'success' ? `连接已验证 · ${aiSettings.last_latency_ms} ms` : aiSettings?.has_api_key ? '已保存密钥 · 建议测试连接' : '尚未配置 API Key'}
            </div>
          </section>

          {hasSegments && <section className="inspector-section export-section">
            <div className="inspector-heading"><strong>导出</strong></div>
            <div className="export-grid">
              <button disabled={isProcessing} onClick={() => doExport('srt')}>SRT</button><button disabled={isProcessing} onClick={() => doExport('vtt')}>VTT</button>
              <button disabled={isProcessing} onClick={() => doExport('ass')}>ASS</button><button disabled={isProcessing} onClick={() => doExport('srt-bilingual')}>双语 SRT</button>
              <button disabled={isProcessing} className="video-export" onClick={() => doExport('mp4')}>导出 MP4</button>
              <button disabled={isProcessing} className="video-export" onClick={() => doExport('mkv')}>导出 MKV</button>
            </div>
            <label className="bilingual-export"><input type="checkbox" checked={config.bilingual} onChange={event => setConfig({ ...config, bilingual: event.target.checked })}/> 导出时包含双语</label>
          </section>}

          <details className="inspector-details">
            <summary>流程诊断</summary>
            <ProcessTimeline steps={processSteps} currentStepId={selectedStep} totalProgress={totalProgress} onStepClick={setSelectedStep}/>
            <SubtitleStatsPanel stats={subtitleStats}/>
          </details>
          <ProcessLogViewer logs={processLogs} collapsed={!showLogPanel} onToggle={() => setShowLogPanel(!showLogPanel)} onClear={() => setProcessLogs([])}/>
        </aside>
      </div>

      {toast && <div className="studio-toast" role="status" aria-live="polite">{toast}</div>}
      <AISettingsDialog open={showAISettings} onClose={() => setShowAISettings(false)} onSaved={setAISettings}/>
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
  const visibleSegments = useMemo(() => {
    const needle = searchText.trim().toLocaleLowerCase();
    if (!needle) return segments;
    return segments.filter(segment =>
      `${segment.clean_text || segment.raw_text}\n${segment.translated_text}`.toLocaleLowerCase().includes(needle)
    );
  }, [searchText, segments]);

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
      const el = tableRef.current.querySelector(`[data-idx="${activeIdx}"]`);
      if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [activeIdx, autoScroll]);

  // Detect user scrolling
  const handleScroll = useCallback(() => {
    userScrolling.current = true;
    clearTimeout((handleScroll as any)._timer);
    (handleScroll as any)._timer = setTimeout(() => { userScrolling.current = false; }, 2000);
    if (onAutoScrollChange) onAutoScrollChange(false);
  }, [onAutoScrollChange]);

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
            {visibleSegments.map(seg => {
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
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default App;
