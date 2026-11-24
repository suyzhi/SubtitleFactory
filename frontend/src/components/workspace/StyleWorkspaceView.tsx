import { lazy, Suspense, type RefObject } from 'react';
import * as api from '../../api/backend';
import type { Project, SubtitleSegment, SubtitleStyleSettings } from '../../types';
import DeferredPanel from '../DeferredPanel';
import StyleTemplateBar from '../StyleTemplateBar';
import type { PlayerPresentationMode, SubtitlePlayerHandle } from '../SubtitlePlayer';
import SubtitleStylePanel from '../SubtitleStylePanel';

const SubtitlePlayer = lazy(() => import('../SubtitlePlayer'));

interface Props {
  activeProject: Project | null;
  canPlayMedia: boolean;
  currentTime: number;
  videoPlayerRef: RefObject<SubtitlePlayerHandle | null>;
  segments: SubtitleSegment[];
  subtitleStyle: SubtitleStyleSettings;
  activeSegmentIdx: number;
  presentationMode: PlayerPresentationMode;
  onTimeUpdate: (time: number) => void;
  onDurationChange: (duration: number) => void;
  onStyleChange: (style: SubtitleStyleSettings) => void;
  onPresentationModeChange: (mode: PlayerPresentationMode) => void;
}

export default function StyleWorkspaceView({
  activeProject,
  canPlayMedia,
  currentTime,
  videoPlayerRef,
  segments,
  subtitleStyle,
  activeSegmentIdx,
  presentationMode,
  onTimeUpdate,
  onDurationChange,
  onStyleChange,
  onPresentationModeChange,
}: Props) {
  return (
    <section className="task-page style-task-page">
      <div className="style-canvas">
        <StyleTemplateBar style={subtitleStyle} onApply={onStyleChange} />
        <header>
          <h2>实时外观预览</h2>
          <p>在接近成片的画面比例中调整字幕，不受其他工具干扰。</p>
        </header>
        <div className="style-canvas-stage">
          {activeProject && canPlayMedia && activeProject.video_url ? (
            <Suspense fallback={<DeferredPanel kind="player" label="正在加载样式预览…" />}>
              <SubtitlePlayer
                initialTime={currentTime}
                ref={videoPlayerRef}
                projectId={activeProject.id}
                videoUrl={api.getBackendMediaUrl(activeProject.video_url) || ''}
                segments={segments}
                style={subtitleStyle}
                activeIdx={activeSegmentIdx}
                onTimeUpdate={onTimeUpdate}
                onDurationChange={onDurationChange}
                onStyleChange={onStyleChange}
                presentationMode={presentationMode}
                onPresentationModeChange={onPresentationModeChange}
              />
            </Suspense>
          ) : (
            <div className="style-preview-card">
              <span>为每一句话找到恰好的位置。</span>
              <small>Give every line its perfect place.</small>
            </div>
          )}
        </div>
      </div>
      <aside className="style-controls-page">
        <header>
          <small>字幕检查器</small>
          <h2>字体与排版</h2>
        </header>
        <SubtitleStylePanel style={subtitleStyle} onChange={onStyleChange} />
      </aside>
    </section>
  );
}
