// 字幕工厂 - 主应用入口（项目库首页 + 按需项目工作区编排）

import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from 'react';
import * as api from './api/backend';
import { AppSelectThemeContext } from './components/appSelectContext';
import DeferredPanel from './components/DeferredPanel';
import FirstRunPreflight from './components/FirstRunPreflight';
import GlobalTaskDrawer from './components/GlobalTaskDrawer';
import ImportFlow from './components/ImportFlow';
import LanguagePicker from './components/LanguagePicker';
import LibrarySidebar from './components/LibrarySidebar';
import ProcessSettingsPanel from './components/ProcessSettingsPanel';
import ProjectContextOverlays from './components/ProjectContextOverlays';
import ProjectWorkspaceView from './components/ProjectWorkspaceView';
import StudioTopbar from './components/StudioTopbar';
import type { SubtitlePlayerHandle } from './components/SubtitlePlayer';
import TranscriptionSetup from './components/TranscriptionSetup';
import { useAppBootstrap } from './hooks/useAppBootstrap';
import { useAppTheme } from './hooks/useAppTheme';
import { useLibrary } from './hooks/useLibrary';
import { useProcessLogs } from './hooks/useProcessLogs';
import { useProjectImport } from './hooks/useProjectImport';
import { useProjectTasks } from './hooks/useProjectTasks';
import { useSubtitleEditor } from './hooks/useSubtitleEditor';
import { useToast } from './hooks/useToast';
import { useWorkspaceLayout } from './hooks/useWorkspaceLayout';
import { useWorkspaceShortcuts } from './hooks/useWorkspaceShortcuts';
import { emptyProcess } from './processSteps';
import { taskIsActive } from './projectState';
import { loadSubtitleStyle } from './subtitleStyle';
import { findSubtitleFocusIndex } from './subtitleTableVirtualization';
import type {
  ProcessingConfig,
  Project,
  SegmentSearchHit,
  SubtitleSegment,
  SubtitleStyleSettings,
} from './types';
import { isPlaylistUrl } from './utils/library';
import { useSlidingIndicator, useTransientFlag } from './utils/motion';
import './styles/tokens.css';
import './styles/base.css';
import './styles/app-shell.css';
import './styles/library.css';
import './styles/workspace.css';
import './styles/editor.css';
import './styles/player.css';
import './styles/style-export.css';
import './styles/tools.css';
import './styles/settings.css';
import './styles/overlays.css';
import './styles/motion.css';

const PROFESSIONAL_UI_MARKER = 'subtitle-factory-ui:professional-v2';
const LIBRARY_WORKSPACE_UI_MARKER = 'subtitle-factory-ui:library-workspace-v2';

const ProductionCenter = lazy(() => import('./components/ProductionCenter'));
const PlaylistBatchDialog = lazy(() => import('./components/PlaylistBatchDialog'));
// Settings are opened occasionally; loading them on demand keeps the startup bundle lean.
const SettingsCenter = lazy(() => import('./components/SettingsCenter'));

const DEFAULT_CONFIG: ProcessingConfig = {
  model: 'auto',
  language: 'auto',
  target_language: 'zh',
  enable_clean: false,
  enable_translate: false,
  bilingual: localStorage.getItem('subtitle_factory_export_bilingual') === 'true',
  clean_target_length: 42,
};

function App() {
  const youtubeEnabled = api.youtubeFeaturesEnabled();
  const filesystemAutomationEnabled = api.filesystemAutomationEnabled();

  // ── Top-level shared state ──
  const [projects, setProjects] = useState<Project[]>([]);
  const [libraryRefresh, setLibraryRefresh] = useState(0);
  const [activeProject, setActiveProject] = useState<Project | null>(null);
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [config, setConfig] = useState<ProcessingConfig>(() => ({
    ...DEFAULT_CONFIG,
    enable_clean: localStorage.getItem('subtitle_factory_flow_clean') === 'true',
    enable_translate: localStorage.getItem('subtitle_factory_flow_translate') === 'true',
    clean_target_length:
      Number(localStorage.getItem('subtitle_factory_clean_target_length')) ||
      DEFAULT_CONFIG.clean_target_length,
  }));

  // ── UI chrome & workspace navigation state ──
  const [currentTime, setCurrentTime] = useState(0);
  const [videoDuration, setVideoDuration] = useState(0);
  const [activeSegmentIdx, setActiveSegmentIdx] = useState(-1);
  const [autoScrollTable, setAutoScrollTable] = useState(true);
  const [showSettingsCenter, setShowSettingsCenter] = useState(false);
  const [showTaskDrawer, setShowTaskDrawer] = useState(false);
  const [showProductionCenter, setShowProductionCenter] = useState(false);
  const [showFirstRunPreflight, setShowFirstRunPreflight] = useState(
    () => localStorage.getItem('subtitle_factory_preflight_v1') !== 'done',
  );
  const [showProjectWorkspace, setShowProjectWorkspace] = useState(false);
  const [projectWorkspace, setProjectWorkspace] = useState<'subtitles' | 'style' | 'export'>(
    'subtitles',
  );
  const [subtitleFocusRequest, setSubtitleFocusRequest] = useState(0);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [toolsTab, setToolsTab] = useState<'process' | 'quality' | 'smart' | 'content'>('process');
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [segmentInspector, setSegmentInspector] = useState<number | null>(null);
  const [inspectorMode, setInspectorMode] = useState<'style' | 'step' | null>(null);
  const [showLinkPopover, setShowLinkPopover] = useState(false);
  const [playlistDialogUrl, setPlaylistDialogUrl] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);

  // ── Refs ──
  const videoPlayerRef = useRef<SubtitlePlayerHandle>(null);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);
  const restoredStartupProject = useRef(false);
  const activeProjectIdRef = useRef<string | null>(null);
  const projectSelectionIntent = useRef(0);
  const pendingSearchJump = useRef<SegmentSearchHit | null>(null);
  const playheadRef = useRef<{ segments: SubtitleSegment[]; time: number }>({
    segments: [],
    time: 0,
  });

  // ── Independent utility hooks ──
  const { toast, setToast, showToast } = useToast();
  const {
    theme,
    changeTheme,
    motionEnabled,
    setMotionEnabled,
    presentationMode,
    setPresentationMode,
  } = useAppTheme();
  const {
    compactLibrary,
    toggleCompactLibrary,
    leftPanelWidth,
    setLeftPanelWidth,
    rightPanelWidth,
    beginResize,
  } = useWorkspaceLayout(motionEnabled);
  const { processLogs, setProcessLogs, addLog } = useProcessLogs();

  // ── Backend bootstrap, settings, AI providers & model runtimes ──
  const {
    backendStatus,
    health,
    refreshHealth,
    appSettings,
    applyAppSettings,
    aiProviderState,
    setAIProviderState,
    cleanAIProvider,
    translateAIProvider,
    cleanAIReady,
    translateAIReady,
    modelStatus,
    modelError,
    refreshModels,
    compatibleModel,
    runtimeForModel,
    chooseRuntime,
    requireRuntime,
  } = useAppBootstrap({
    activeProject,
    config,
    setConfig,
    showProjectWorkspace,
    playlistDialogUrl,
    setLibraryRefresh,
    setSelectedStep,
    setToolsTab,
    setToolsOpen,
    showToast,
  });

  useEffect(() => {
    localStorage.setItem('subtitle_factory_clean_target_length', String(config.clean_target_length));
  }, [config.clean_target_length]);

  useEffect(() => {
    localStorage.setItem('subtitle_factory_flow_clean', String(config.enable_clean));
    localStorage.setItem('subtitle_factory_flow_translate', String(config.enable_translate));
  }, [config.enable_clean, config.enable_translate]);

  const flowOptions = useMemo(
    () => ({
      enable_clean: config.enable_clean,
      enable_translate: config.enable_translate,
      target_language: config.target_language,
      clean_target_length: config.clean_target_length,
      text_processing_consent: config.enable_clean || config.enable_translate,
    }),
    [
      config.clean_target_length,
      config.enable_clean,
      config.enable_translate,
      config.target_language,
    ],
  );

  // ── Subtitle editor & draft recovery hook ──
  const editor = useSubtitleEditor({
    activeProject,
    activeProjectIdRef,
    appSettings,
    setProjects,
    setActiveProject,
    setProjectWorkspace,
    setShowProjectWorkspace,
    addLog,
    setToast,
    showToast,
  });

  useEffect(() => {
    playheadRef.current = { segments: editor.segments, time: currentTime };
  }, [editor.segments, currentTime]);

  const activeProjectIsYoutube = youtubeEnabled && activeProject?.source_type === 'youtube';

  // ── Background task & workflow orchestration hook ──
  const tasks = useProjectTasks({
    activeProject,
    activeProjectIdRef,
    activeProjectIsYoutube,
    config,
    subtitleStyle: editor.subtitleStyle,
    flowOptions,
    editorRevision: editor.editorRevision,
    setActiveProject,
    setProjects,
    setSegments: editor.setSegments,
    setLibraryRefresh,
    setToolsTab,
    setToolsOpen,
    setShowAISettings: setShowSettingsCenter,
    setSelectedStep,
    setProcessLogs,
    addLog,
    compatibleModel,
    runtimeForModel,
    requireRuntime,
    refreshSegments: editor.refreshSegments,
    setToast,
    showToast,
  });

  const {
    waitForPendingWrites,
    resetDraftState,
    setSegments,
    refreshSegments,
    setSubtitleStyle,
  } = editor;
  const {
    resetTaskTracking,
    refreshProcessSteps,
    attachTask,
    setProcessSteps,
  } = tasks;

  // ── Project selection & reset coordination ──
  const selectProject = useCallback(
    async (p: Project, requestedIntent?: number) => {
      const selectionIntent = requestedIntent ?? ++projectSelectionIntent.current;
      if (selectionIntent !== projectSelectionIntent.current) return;
      await waitForPendingWrites();
      if (selectionIntent !== projectSelectionIntent.current) return;
      const switchingProject = activeProjectIdRef.current !== p.id;
      activeProjectIdRef.current = p.id;
      setActiveProject(p);
      resetDraftState(Number(p.edit_revision || 0));
      setShowProjectWorkspace(true);
      setProjectWorkspace('subtitles');
      setToolsOpen(false);
      setSegmentInspector(null);
      setSegments([]);
      localStorage.setItem('subtitle_factory_last_project_id', p.id);
      resetTaskTracking();
      setActiveSegmentIdx(-1);
      if (switchingProject) {
        setCurrentTime(0);
        setVideoDuration(0);
      }
      refreshProcessSteps(p);
      await Promise.all([
        refreshSegments(p.id),
        api
          .getProjectStyle(p.id)
          .then(result => {
            if (activeProjectIdRef.current !== p.id) return;
            setSubtitleStyle(
              result.settings
                ? ({ ...loadSubtitleStyle(), ...result.settings } as SubtitleStyleSettings)
                : loadSubtitleStyle(),
            );
          })
          .catch(() => {
            if (activeProjectIdRef.current === p.id) setSubtitleStyle(loadSubtitleStyle());
          }),
      ]);
      try {
        const latestTask = await api.getLatestProjectTask(p.id);
        if (activeProjectIdRef.current !== p.id) return;
        if (latestTask) attachTask(latestTask, 1000);
      } catch {
        /* projects created by older builds may not have task history */
      }
      if (activeProjectIdRef.current === p.id) addLog('info', '项目', `打开项目: ${p.title}`);
    },
    [
      addLog,
      attachTask,
      refreshProcessSteps,
      refreshSegments,
      resetDraftState,
      resetTaskTracking,
      setSegments,
      setSubtitleStyle,
      waitForPendingWrites,
    ],
  );

  const selectProjectById = useCallback(
    async (projectId: string) => {
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
    },
    [selectProject, setToast],
  );

  const resetActiveProject = useCallback(() => {
    projectSelectionIntent.current += 1;
    activeProjectIdRef.current = null;
    setActiveProject(null);
    setSegments([]);
    resetDraftState(0);
    resetTaskTracking();
    setProcessSteps(emptyProcess());
    setInspectorMode(null);
    setShowProjectWorkspace(false);
    setProjectWorkspace('subtitles');
  }, [resetDraftState, resetTaskTracking, setProcessSteps, setSegments]);

  // ── Library, search, playlists & project actions hook ──
  const library = useLibrary({
    backendStatus,
    youtubeEnabled,
    motionEnabled,
    showProjectWorkspace,
    projects,
    setProjects,
    libraryRefresh,
    setLibraryRefresh,
    activeProject,
    currentTask: tasks.currentTask,
    setActiveProject,
    waitForPendingWrites: editor.waitForPendingWrites,
    resetActiveProject,
    setToast,
    showToast,
  });

  useEffect(() => {
    if (restoredStartupProject.current || backendStatus !== 'connected' || !projects.length) return;
    if (activeProject) {
      restoredStartupProject.current = true;
      return;
    }
    restoredStartupProject.current = true;
    if (appSettings.startup_behavior !== 'restore_last') return;
    const lastId = localStorage.getItem('subtitle_factory_last_project_id');
    const project = projects.find(item => item.id === lastId);
    if (project) void selectProject(project);
  }, [activeProject, appSettings.startup_behavior, backendStatus, projects, selectProject]);

  // ── Global keyboard & popover shortcuts ──
  useWorkspaceShortcuts({
    activeProject,
    activeProjectIdRef,
    presentationMode,
    setPresentationMode,
    setInspectorMode,
    setShowLinkPopover,
    showAISettings: showSettingsCenter,
    showProjectWorkspace,
    projectWorkspace,
    draftItemsRef: editor.draftItemsRef,
    editorQueue: editor.editorQueue,
    editorWriteFailed: editor.editorWriteFailed,
    playheadRef,
    videoPlayerRef,
    commitDraft: editor.commitDraft,
    splitSegment: editor.splitSegment,
    undoEditor: editor.undoEditor,
    redoEditor: editor.redoEditor,
    setToast,
    showToast,
  });

  // ── Import & project package flows ──
  const {
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
    exportProjectPackage,
    importProjectPackage,
  } = useProjectImport({
    activeProject,
    activeProjectIdRef,
    config,
    recommendedModel: modelStatus?.recommended_model,
    flowOptions,
    setProjects,
    setLibraryRefresh,
    compatibleModel,
    runtimeForModel,
    requireRuntime,
    selectProject,
    attachTask,
    setToast,
    showToast,
  });

  const updateTargetLanguage = useCallback(
    async (targetLanguage: string) => {
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
    },
    [setToast],
  );

  // ── Playback & search jump sync ──
  const handleTimeUpdate = useCallback(
    (time: number) => {
      setCurrentTime(time);
      setActiveSegmentIdx(editor.segments.findLastIndex(s => s.start <= time && s.end >= time));
    },
    [editor.segments],
  );

  const handleSeek = useCallback(
    (time: number) => {
      setCurrentTime(time);
      setActiveSegmentIdx(
        editor.segments.findLastIndex(segment => segment.start <= time && segment.end >= time),
      );
      videoPlayerRef.current?.seekTo(time);
    },
    [editor.segments],
  );

  const openLibrarySearchHit = useCallback(
    async (hit: SegmentSearchHit) => {
      const selectionIntent = ++projectSelectionIntent.current;
      try {
        pendingSearchJump.current = hit;
        const project =
          projects.find(item => item.id === hit.project_id) ||
          (await api.getProject(hit.project_id));
        if (selectionIntent !== projectSelectionIntent.current) return;
        await selectProject(project, selectionIntent);
      } catch (error) {
        pendingSearchJump.current = null;
        setToast(error instanceof Error ? error.message : String(error));
      }
    },
    [projects, selectProject, setToast],
  );

  useEffect(() => {
    const hit = pendingSearchJump.current;
    if (!hit || activeProject?.id !== hit.project_id) return;
    if (
      !editor.segments.some(
        segment => segment.id === hit.segment_id || segment.index === hit.segment_index,
      )
    ) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      setProjectWorkspace('subtitles');
      setShowProjectWorkspace(true);
      setActiveSegmentIdx(
        Math.max(
          0,
          editor.segments.findIndex(segment => segment.id === hit.segment_id),
        ),
      );
      handleSeek(hit.start);
      setSubtitleFocusRequest(request => request + 1);
      pendingSearchJump.current = null;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeProject?.id, editor.segments, handleSeek]);

  const previewClipRange = useCallback((start: number, end: number) => {
    setProjectWorkspace('subtitles');
    setShowProjectWorkspace(true);
    window.requestAnimationFrame(() => videoPlayerRef.current?.previewRange(start, end));
  }, []);

  const openWorkflowStep = useCallback((stepId: string) => {
    setSelectedStep(stepId);
    setToolsTab('process');
    setToolsOpen(true);
    setShowProjectWorkspace(true);
    setInspectorMode(null);
  }, []);

  // ── Derived view state & motion indicators ──
  const inProjectView = showProjectWorkspace && !!activeProject;
  const viewEntering = useTransientFlag(
    inProjectView
      ? `project:${activeProject?.id}`
      : `library:${library.libraryView}:${library.libraryPage}:${projects.length > 0}`,
  );
  const pageEntering = useTransientFlag(`${activeProject?.id}:${projectWorkspace}`, 700);
  const workspaceTabsRef = useSlidingIndicator<HTMLDivElement>(
    `${inProjectView}:${projectWorkspace}`,
  );
  const libraryTabsRef = useSlidingIndicator<HTMLDivElement>(library.libraryView);
  const layoutSwap = useTransientFlag(
    typeof document !== 'undefined' && 'startViewTransition' in document
      ? 'layout'
      : `layout:${compactLibrary}`,
    700,
  );
  const toolTabsRef = useSlidingIndicator<HTMLElement>(`${toolsOpen}:${toolsTab}`);

  const hasAudio = Boolean(activeProject?.audio_path);
  const hasSegments = editor.segments.length > 0;
  const isProcessing = tasks.taskStarting || taskIsActive(tasks.currentTask);
  const editorBusy =
    isProcessing &&
    (!tasks.currentTask ||
      ['clean', 'translate', 'speaker_diarization', 'ocr'].includes(tasks.currentTask.type) ||
      (tasks.currentTask.type === 'workflow' &&
        ['clean', 'translate'].some(
          stage => tasks.currentTask?.details?.stages?.[stage] === 'running',
        )));
  const hasLocalVideo = Boolean(activeProject?.video_available || activeProject?.video_path);
  const canPlayMedia = hasLocalVideo;
  const activeSegmentIndex =
    activeSegmentIdx >= 0 ? (editor.segments[activeSegmentIdx]?.index ?? -1) : -1;
  const subtitleEntryFocusIndex = findSubtitleFocusIndex(editor.segments, currentTime);

  const inspectorModelId =
    config.model === 'auto' ? modelStatus?.recommended_model || 'small' : config.model;
  const activeProcessStep =
    selectedStep || (!hasAudio ? 'download' : !hasSegments ? 'transcribe' : 'clean');

  const playlistWorkflow = {
    model: inspectorModelId,
    runtime: runtimeForModel(inspectorModelId),
    language: config.language,
    target_language: config.target_language,
    clean_target_length: config.clean_target_length,
  };

  const transcriptionSetupNode = (
    <>
      {modelError && (
        <p role="alert">
          模型列表暂时无法刷新：{modelError}
          <button onClick={refreshModels}>重新加载模型</button>
        </p>
      )}
      <TranscriptionSetup
        models={modelStatus?.models || []}
        model={config.model}
        language={config.language}
        recommended={modelStatus?.recommended_model}
        runtime={runtimeForModel(inspectorModelId)}
        onModel={model => setConfig(current => ({ ...current, model }))}
        onLanguage={language => setConfig(current => ({ ...current, language }))}
        onRuntime={chooseRuntime}
      />
    </>
  );

  const flowOptionsNode = (
    <details className="flow-options">
      <summary>
        生成后流程：{config.enable_clean || config.enable_translate ? '含文本处理' : '仅转写'}
      </summary>
      <label className="check-row">
        <input
          type="checkbox"
          checked={config.enable_clean}
          disabled={!cleanAIReady}
          onChange={event =>
            setConfig(current => ({ ...current, enable_clean: event.target.checked }))
          }
        />
        自动整理
      </label>
      <label className="check-row">
        <input
          type="checkbox"
          checked={config.enable_translate}
          disabled={!translateAIReady}
          onChange={event =>
            setConfig(current => ({ ...current, enable_translate: event.target.checked }))
          }
        />
        自动翻译
      </label>
      {config.enable_translate && (
        <LanguagePicker
          mode="target"
          value={config.target_language}
          onChange={target_language => setConfig(current => ({ ...current, target_language }))}
        />
      )}
      <p>
        勾选即同意在此流程中将字幕文本发送至设置中的服务商（整理：
        {cleanAIProvider?.name || '未配置'}；翻译：{translateAIProvider?.name || '未配置'}
        ）。可能产生服务商费用，处理地区依服务商设置；取消勾选后，新任务不再自动执行相应步骤；正在运行的任务请在任务面板中终止。音视频不会随这两个步骤上传。
      </p>
      {(!cleanAIReady || !translateAIReady) && (
        <button className="button secondary" onClick={() => setShowSettingsCenter(true)}>
          配置文本服务
        </button>
      )}
    </details>
  );

  return (
    <AppSelectThemeContext.Provider value={theme}>
      <div
        data-ui-build={PROFESSIONAL_UI_MARKER}
        data-ui-layout={LIBRARY_WORKSPACE_UI_MARKER}
        className={`app pro-app theme-${theme} ${motionEnabled ? '' : 'motion-off'} presentation-${presentationMode} ${showProjectWorkspace && activeProject ? 'workspace-active' : 'library-home'}`}
        onDragEnter={event => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragOver={event => {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'copy';
        }}
        onDragLeave={event => {
          if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragActive(false);
        }}
        onDrop={event => {
          event.preventDefault();
          setDragActive(false);
          importFiles(Array.from(event.dataTransfer.files));
        }}
      >
        <StudioTopbar
          showProjectWorkspace={showProjectWorkspace}
          activeProject={activeProject}
          segments={editor.segments}
          inProjectView={inProjectView}
          youtubeEnabled={youtubeEnabled}
          backendStatus={backendStatus}
          isProcessing={isProcessing}
          currentTask={tasks.currentTask}
          activeTaskCount={tasks.activeTaskCount}
          showTaskDrawer={showTaskDrawer}
          showLinkPopover={showLinkPopover}
          youtubeUrl={youtubeUrl}
          theme={theme}
          settingsButtonRef={settingsButtonRef}
          onBackToLibrary={() => setShowProjectWorkspace(false)}
          onImportLocal={handleImportLocal}
          onToggleLinkPopover={() => setShowLinkPopover(value => !value)}
          onCloseLinkPopover={() => setShowLinkPopover(false)}
          onYoutubeUrlChange={setYoutubeUrl}
          onSubmitLink={url => {
            setShowLinkPopover(false);
            if (isPlaylistUrl(url)) setPlaylistDialogUrl(url);
            else prepareImport({ kind: 'link', url });
          }}
          onToggleTaskDrawer={() => setShowTaskDrawer(value => !value)}
          onToggleTheme={origin => changeTheme(theme === 'dark' ? 'light' : 'dark', origin)}
          onOpenSettings={() => setShowSettingsCenter(true)}
        />

        {pendingImport && (
          <ImportFlow
            source={pendingImport}
            setup={
              <>
                {transcriptionSetupNode}
                {flowOptionsNode}
              </>
            }
            busy={importBusy}
            canStart={!!runtimeForModel(inspectorModelId)}
            onClose={() => setPendingImport(null)}
            onStart={(generate, remember) => executeImport(pendingImport, generate, remember)}
          />
        )}

        <GlobalTaskDrawer
          open={showTaskDrawer}
          onClose={() => setShowTaskDrawer(false)}
          onOpenProject={projectId => {
            const project =
              projects.find(item => item.id === projectId) ||
              library.playlistBatches
                .flatMap(batch => batch.items)
                .find(item => item.project_id === projectId)?.project;
            if (project) void selectProject(project);
            else void selectProjectById(projectId);
            setShowTaskDrawer(false);
          }}
          onOpenSettings={() => {
            setShowTaskDrawer(false);
            setShowSettingsCenter(true);
          }}
        />

        {youtubeEnabled && playlistDialogUrl && (
          <Suspense fallback={<DeferredPanel kind="overlay" label="正在打开播放列表工具…" />}>
            <PlaylistBatchDialog
              url={playlistDialogUrl}
              workflow={playlistWorkflow}
              appSettings={appSettings}
              health={health}
              aiReady={{ clean: cleanAIReady, translate: translateAIReady }}
              onClose={() => setPlaylistDialogUrl(null)}
              onCreated={message => {
                setPlaylistDialogUrl(null);
                showToast(message, 4200);
                void library.refreshPlaylistBatches();
              }}
            />
          </Suspense>
        )}

        {filesystemAutomationEnabled && showProductionCenter && (() => {
          const batchModel = compatibleModel() || config.model;
          return (
            <Suspense fallback={<DeferredPanel kind="overlay" label="正在打开批量生产工具…" />}>
              <ProductionCenter
                workflow={{
                  model: batchModel,
                  language: config.language,
                  target_language: config.target_language,
                  runtime: runtimeForModel(batchModel),
                }}
                onClose={() => setShowProductionCenter(false)}
                onProjectsCreated={() => setLibraryRefresh(value => value + 1)}
                onShowTasks={() => setShowTaskDrawer(true)}
              />
            </Suspense>
          );
        })()}

        {showFirstRunPreflight && backendStatus === 'connected' && modelStatus && (
          <FirstRunPreflight
            appSettings={appSettings}
            health={health}
            modelStatus={modelStatus}
            onConfirm={(modelId, runtimeId) => {
              if (modelId && runtimeId) chooseRuntime(modelId, runtimeId);
              localStorage.setItem('subtitle_factory_preflight_v1', 'done');
              setShowFirstRunPreflight(false);
            }}
          />
        )}

        {backendStatus === 'error' && (
          <div className="engine-error-banner">
            <strong>本地引擎未能启动</strong>
            <span>打开设置查看 FFmpeg、模型与存储诊断。</span>
            <button onClick={refreshHealth}>重新检查</button>
          </div>
        )}

        {uploadProgress !== null && (
          <div className="upload-progress-banner" role="status">
            <span>正在导入视频</span>
            <progress value={uploadProgress} max={100} />
            <strong>{uploadProgress}%</strong>
          </div>
        )}

        <div
          className={`studio-shell v05-shell ${showProjectWorkspace && activeProject ? `app-project project-view-${projectWorkspace}` : 'app-library'} ${inspectorMode ? 'inspector-open' : ''} ${viewEntering ? 'view-entering' : ''} ${pageEntering ? 'page-entering' : ''}`}
          style={
            {
              '--left-panel-width': `${leftPanelWidth}px`,
              '--right-panel-width': `${rightPanelWidth}px`,
            } as CSSProperties
          }
        >
          <LibrarySidebar
            youtubeEnabled={youtubeEnabled}
            filesystemAutomationEnabled={filesystemAutomationEnabled}
            backendStatus={backendStatus}
            motionEnabled={motionEnabled}
            compactLibrary={compactLibrary}
            layoutSwap={layoutSwap}
            libraryTabsRef={libraryTabsRef}
            libraryView={library.libraryView}
            libraryPage={library.libraryPage}
            libraryPages={library.libraryPages}
            libraryTotal={library.libraryTotal}
            libraryLoading={library.libraryLoading}
            libraryFirstLoad={library.libraryFirstLoad}
            libraryError={library.libraryError}
            libraryStatus={library.libraryStatus}
            librarySearch={library.librarySearch}
            librarySort={library.librarySort}
            librarySearchHits={library.librarySearchHits}
            librarySearchTotal={library.librarySearchTotal}
            librarySearchPage={library.librarySearchPage}
            librarySearchLoading={library.librarySearchLoading}
            librarySearchSelection={library.librarySearchSelection}
            librarySearchFacets={library.librarySearchFacets}
            librarySearchFilters={library.librarySearchFilters}
            projects={projects}
            trashProjects={library.trashProjects}
            activeProjectId={activeProject?.id || null}
            playlistBatches={library.playlistBatches}
            openPlaylistBatches={library.openPlaylistBatches}
            playlistWorkflow={playlistWorkflow}
            projectGroups={library.projectGroups}
            knownProjectGroups={library.knownProjectGroups}
            collapsedProjectGroups={library.collapsedProjectGroups}
            groupEditorProjectId={library.groupEditorProjectId}
            groupDraft={library.groupDraft}
            contextMenuProjectId={library.contextMenu?.project.id || null}
            removingProjectIds={library.removingProjectIds}
            onImportLocal={handleImportLocal}
            onImportProjectPackage={importProjectPackage}
            onOpenProductionCenter={() => setShowProductionCenter(true)}
            onOpenLinkPopover={() => setShowLinkPopover(true)}
            onLibraryViewChange={library.setLibraryView}
            onLibraryPageChange={library.setLibraryPage}
            onLibraryStatusChange={library.setLibraryStatus}
            onLibraryRetry={() => setLibraryRefresh(value => value + 1)}
            onToggleCompactLibrary={toggleCompactLibrary}
            onLibrarySearchChange={library.setLibrarySearch}
            onLibrarySortChange={library.setLibrarySort}
            onLibrarySearchSelectionChange={library.setLibrarySearchSelection}
            onLibrarySearchFiltersChange={library.setLibrarySearchFilters}
            onLibrarySearchPageChange={library.setLibrarySearchPage}
            onOpenSearchHit={hit => void openLibrarySearchHit(hit)}
            onTogglePlaylistBatch={library.togglePlaylistBatch}
            onRefreshPlaylistBatches={() => void library.refreshPlaylistBatches()}
            onSelectProject={project => void selectProject(project)}
            onToggleProjectGroup={library.toggleProjectGroup}
            onOpenProjectMenu={library.openProjectMenu}
            onGroupDraftChange={library.setGroupDraft}
            onSaveProjectGroup={project => void library.saveProjectGroup(project)}
            onCloseGroupEditor={() => library.setGroupEditorProjectId(null)}
            onClearTrash={() => void library.clearTrash()}
            onMessage={showToast}
          />

          <div
            className="panel-resizer panel-resizer-left"
            role="separator"
            aria-label="调整项目库宽度"
            tabIndex={0}
            onPointerDown={event => beginResize('left', event)}
            onKeyDown={event => {
              if (event.key === 'ArrowLeft') {
                setLeftPanelWidth(value => Math.max(210, value - 16));
              }
              if (event.key === 'ArrowRight') {
                setLeftPanelWidth(value => Math.min(430, value + 16));
              }
            }}
          />

          <ProjectWorkspaceView
            inProjectView={inProjectView}
            activeProject={activeProject}
            projectWorkspace={projectWorkspace}
            subtitleFocus={false}
            workspaceTabsRef={workspaceTabsRef}
            toolTabsRef={toolTabsRef}
            videoPlayerRef={videoPlayerRef}
            youtubeEnabled={youtubeEnabled}
            canPlayMedia={canPlayMedia}
            hasLocalVideo={hasLocalVideo}
            hasSegments={hasSegments}
            isProcessing={isProcessing}
            taskStarting={tasks.taskStarting}
            editorBusy={editorBusy}
            segments={editor.segments}
            segmentsLoading={editor.segmentsLoading}
            segmentsError={editor.segmentsError}
            currentTime={currentTime}
            videoDuration={videoDuration}
            activeSegmentIdx={activeSegmentIdx}
            activeSegmentIndex={activeSegmentIndex}
            subtitleEntryFocusIndex={subtitleEntryFocusIndex}
            subtitleFocusRequest={subtitleFocusRequest}
            autoScrollTable={autoScrollTable}
            editorSaveState={editor.editorSaveState}
            editorRevision={editor.editorRevision.current}
            draftCount={Object.keys(editor.draftItems).length}
            draftIsStale={editor.draftIsStale}
            segmentInspector={segmentInspector}
            subtitleStyle={editor.subtitleStyle}
            presentationMode={presentationMode}
            bilingual={config.bilingual}
            toolsOpen={toolsOpen}
            toolsTab={toolsTab}
            activeTaskCount={tasks.activeTaskCount}
            currentTask={tasks.currentTask}
            compactSteps={tasks.compactSteps}
            activeProcessStep={activeProcessStep}
            processSteps={tasks.processSteps}
            totalProgress={tasks.totalProgress}
            processLogs={processLogs}
            processSettingsNode={
              <ProcessSettingsPanel
                activeProcessStep={activeProcessStep}
                activeProject={activeProject}
                activeProjectIsYoutube={activeProjectIsYoutube}
                hasAudio={hasAudio}
                hasSegments={hasSegments}
                hasLocalVideo={hasLocalVideo}
                isProcessing={isProcessing}
                taskStarting={tasks.taskStarting}
                currentTask={tasks.currentTask}
                recoveryActionLabel={tasks.recoveryActionLabel}
                config={config}
                health={health}
                youtubeUrl={youtubeUrl}
                aiProviderState={aiProviderState}
                cleanAIProvider={cleanAIProvider}
                cleanAIReady={cleanAIReady}
                translateAIReady={translateAIReady}
                failedCleanBatches={tasks.failedCleanBatches}
                segments={editor.segments}
                editorRevision={editor.editorRevision.current}
                hasDraft={Object.keys(editor.draftItems).length > 0}
                inspectorRuntime={runtimeForModel(inspectorModelId)}
                quickImport={quickImport}
                transcriptionSetup={transcriptionSetupNode}
                flowOptionsPanel={flowOptionsNode}
                onYoutubeUrlChange={setYoutubeUrl}
                onConfigChange={setConfig}
                onMediaSelectionChanged={changed => {
                  if (!activeProject) return;
                  const projectId = activeProject.id;
                  void api
                    .getProject(projectId)
                    .then(project => {
                      if (activeProjectIdRef.current !== projectId) return;
                      setActiveProject(project);
                      tasks.refreshProcessSteps(project);
                      showToast(
                        changed
                          ? '音轨或范围已更新；下次生成字幕将自动准备音频，现有字幕已保留'
                          : '设置未改变，可继续复用现有音频',
                      );
                    })
                    .catch(error => showToast(error.message));
                }}
                onRetryDownload={tasks.retryDownload}
                onDownloadLocalCopy={tasks.downloadLocalCopy}
                onExtractAudio={tasks.doExtractAudio}
                onTranscribe={tasks.doTranscribe}
                onGenerateSubtitles={tasks.doGenerateSubtitles}
                onResetQuickImport={resetQuickImport}
                onClean={tasks.doClean}
                onUndoClean={() => void tasks.undoClean()}
                onRetryFailedCleanBatch={batchIndex => void tasks.retryFailedCleanBatch(batchIndex)}
                onUpdateTargetLanguage={targetLanguage => void updateTargetLanguage(targetLanguage)}
                onTranslate={tasks.doTranslate}
                onGoToExport={() => {
                  setProjectWorkspace('export');
                  setToolsOpen(false);
                }}
                onOpenSettings={() => setShowSettingsCenter(true)}
                onRetryCurrentFailure={tasks.retryCurrentFailure}
                onSeek={handleSeek}
                onAcceptCandidate={result =>
                  activeProject && editor.acceptEditorResult(activeProject.id, result)
                }
              />
            }
            onSelectWorkspace={workspace => {
              setProjectWorkspace(workspace);
              if (workspace === 'subtitles') setSubtitleFocusRequest(request => request + 1);
              setInspectorMode(null);
            }}
            onOpenTools={(tab = 'process') => {
              setToolsTab(tab);
              setToolsOpen(true);
              setInspectorMode(null);
            }}
            onCloseTools={() => setToolsOpen(false)}
            onToolsTabChange={setToolsTab}
            onOpenTaskDrawer={() => setShowTaskDrawer(true)}
            onOpenWorkflowStep={openWorkflowStep}
            onImportSubtitleFile={editor.importSubtitleFile}
            onImportLocal={handleImportLocal}
            onOpenLinkPopover={() => setShowLinkPopover(true)}
            onTimeUpdate={handleTimeUpdate}
            onDurationChange={setVideoDuration}
            onStyleChange={editor.handleStyleChange}
            onPresentationModeChange={setPresentationMode}
            onRefreshSegments={projectId => void editor.refreshSegments(projectId)}
            onSeek={handleSeek}
            onInspectSegment={setSegmentInspector}
            onUpdateSegment={editor.handleUpdateSegment}
            onReplaceAll={editor.replaceSegments}
            onSplit={editor.splitSegment}
            onMerge={editor.mergeSegments}
            onDelete={editor.deleteSegments}
            onUndo={editor.undoEditor}
            onRedo={editor.redoEditor}
            onPreviewDraft={editor.previewDraft}
            onCommitDraft={() => void editor.commitDraft()}
            onDiscardDraft={() => void editor.discardDraft()}
            onAutoScrollChange={setAutoScrollTable}
            onBilingualChange={bilingual => {
              setConfig(current => ({ ...current, bilingual }));
              localStorage.setItem('subtitle_factory_export_bilingual', String(bilingual));
            }}
            onExport={format => void tasks.doExport(format)}
            onPackage={includeMedia => void exportProjectPackage(includeMedia)}
            onAcceptEditorResult={editor.acceptEditorResult}
            onRefreshActiveProject={projectId => void editor.refreshActiveProject(projectId)}
            onSelectStep={setSelectedStep}
            onPauseTask={() => void tasks.toggleTaskPause()}
            onCancelTask={() => void tasks.cancelCurrentTask()}
            onClearLogs={() => setProcessLogs([])}
            onPreviewClipRange={previewClipRange}
            onMessage={showToast}
          />
        </div>

        <ProjectContextOverlays
          contextMenu={library.contextMenu}
          contextMenuRef={library.contextMenuRef}
          renameProjectState={library.renameProjectState}
          renameDraft={library.renameDraft}
          dragActive={dragActive}
          toast={toast}
          onCloseContextMenu={() => library.setContextMenu(null)}
          onSelectProject={project => void selectProject(project)}
          onStartRename={project => {
            library.setRenameDraft(project.title);
            library.setRenameProjectState(project);
          }}
          onOpenGroupEditor={library.openProjectGroupEditor}
          onMoveToTrash={project => void library.moveProjectToTrash(project)}
          onRestoreProject={project => void library.restoreProject(project)}
          onDeleteForever={project => void library.deleteProjectForever(project)}
          onRenameDraftChange={library.setRenameDraft}
          onCloseRename={() => library.setRenameProjectState(null)}
          onSaveRename={() => void library.saveRename()}
        />

        {showSettingsCenter && (
          <Suspense fallback={<DeferredPanel kind="overlay" label="正在打开设置…" />}>
            <SettingsCenter
              open
              onClose={() => setShowSettingsCenter(false)}
              returnFocusRef={settingsButtonRef}
              config={config}
              onConfigChange={setConfig}
              appSettings={appSettings}
              onAppSettingsChange={applyAppSettings}
              onAIProvidersChange={setAIProviderState}
              theme={theme}
              onThemeChange={changeTheme}
              motionEnabled={motionEnabled}
              onMotionEnabledChange={setMotionEnabled}
              health={health}
              onRefreshHealth={refreshHealth}
              modelStatus={modelStatus}
              onRefreshModels={refreshModels}
              onOpenLogs={() => {
                setToolsTab('process');
                setToolsOpen(true);
                setShowProjectWorkspace(!!activeProject);
                setInspectorMode(null);
              }}
            />
          </Suspense>
        )}
      </div>
    </AppSelectThemeContext.Provider>
  );
}

export default App;
