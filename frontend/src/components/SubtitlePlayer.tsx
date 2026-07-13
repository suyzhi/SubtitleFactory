import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import type { SubtitleDisplayMode, SubtitleSegment, SubtitleStyleSettings } from '../types';
import playIcon from '../assets/player-icons/play.png';
import pauseIcon from '../assets/player-icons/pause.png';
import volumeIcon from '../assets/player-icons/volume.png';
import mutedIcon from '../assets/player-icons/muted.png';
import captionsIcon from '../assets/player-icons/captions.png';
import fullscreenIcon from '../assets/player-icons/fullscreen.png';
import theaterIcon from '../assets/player-icons/theater.png';
import { SUBTITLE_FONT_OPTIONS } from '../subtitleStyle';
import AppSelect from './AppSelect';

interface Props {
  videoUrl: string;
  segments: SubtitleSegment[];
  style: SubtitleStyleSettings;
  activeIdx: number;
  theaterMode: boolean;
  onTimeUpdate: (time: number) => void;
  onDurationChange?: (duration: number) => void;
  onStyleChange: (style: SubtitleStyleSettings) => void;
  onTheaterModeChange: (enabled: boolean) => void;
}

export interface SubtitlePlayerHandle {
  seekTo: (time: number) => void;
}

function timecode(value: number) {
  if (!Number.isFinite(value)) return '0:00';
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = Math.floor(value % 60);
  return hours
    ? `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
    : `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

const MODE_OPTIONS: { label: string; value: SubtitleDisplayMode }[] = [
  { label: '关闭', value: 'off' },
  { label: '仅原文', value: 'original' },
  { label: '仅译文', value: 'translated' },
  { label: '双语 · 原文在上', value: 'bilingual_original_first' },
  { label: '双语 · 译文在上', value: 'bilingual_translated_first' },
];

function ControlIcon({ src }: { src: string }) {
  return <img className="control-icon" src={src} alt="" draggable={false} />;
}

const SubtitlePlayer = forwardRef<SubtitlePlayerHandle, Props>(function SubtitlePlayer({
  videoUrl, segments, style, activeIdx, theaterMode, onTimeUpdate, onDurationChange,
  onStyleChange, onTheaterModeChange,
}, ref) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const hideTimer = useRef<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [time, setTime] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [rate, setRate] = useState(1);
  const [showSubtitleMenu, setShowSubtitleMenu] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [fallbackFullscreen, setFallbackFullscreen] = useState(false);

  const seekTo = useCallback((next: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = Math.max(0, Math.min(next, video.duration || next));
    setTime(video.currentTime);
    onTimeUpdate(video.currentTime);
  }, [onTimeUpdate]);

  useImperativeHandle(ref, () => ({ seekTo }), [seekTo]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    video.load();
    setPlaying(false);
    setTime(0);
    setDuration(0);
  }, [videoUrl]);

  useEffect(() => {
    const update = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', update);
    return () => document.removeEventListener('fullscreenchange', update);
  }, []);

  const revealControls = useCallback(() => {
    setControlsVisible(true);
    if (hideTimer.current) window.clearTimeout(hideTimer.current);
    if (playing && !showSubtitleMenu) {
      hideTimer.current = window.setTimeout(() => setControlsVisible(false), 2400);
    }
  }, [playing, showSubtitleMenu]);

  useEffect(() => {
    revealControls();
    return () => { if (hideTimer.current) window.clearTimeout(hideTimer.current); };
  }, [playing, showSubtitleMenu, revealControls]);

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play().catch(() => {
        setPlaying(false);
        setControlsVisible(true);
      });
    } else video.pause();
  }, []);

  const toggleFullscreen = useCallback(async () => {
    if (fallbackFullscreen) {
      setFallbackFullscreen(false);
      setFullscreen(false);
      return;
    }
    try {
      if ('__TAURI_INTERNALS__' in window) {
        const appWindow = getCurrentWindow();
        const next = !(await appWindow.isFullscreen());
        await appWindow.setFullscreen(next);
        setFullscreen(next);
        return;
      }
      if (document.fullscreenElement) await document.exitFullscreen();
      else {
        await wrapperRef.current?.requestFullscreen();
        if (!document.fullscreenElement) {
          setFallbackFullscreen(true);
          setFullscreen(true);
        }
      }
    } catch (error) {
      console.error('切换全屏失败', error);
      setFallbackFullscreen(true);
      setFullscreen(true);
    }
  }, [fallbackFullscreen]);

  const active = activeIdx >= 0 && activeIdx < segments.length ? segments[activeIdx] : null;
  const original = active?.clean_text || active?.raw_text || '';
  const translated = active?.translated_text || '';
  const lines: Array<{ kind: 'original' | 'translated'; text: string }> = !active || style.mode === 'off' ? []
    : style.mode === 'original' ? [{ kind: 'original', text: original }]
    : style.mode === 'translated' ? (translated ? [{ kind: 'translated', text: translated }] : [])
    : style.mode === 'bilingual_original_first'
      ? [{ kind: 'original', text: original }, { kind: 'translated', text: translated }].filter(item => Boolean(item.text)) as Array<{ kind: 'original' | 'translated'; text: string }>
      : [{ kind: 'translated', text: translated }, { kind: 'original', text: original }].filter(item => Boolean(item.text)) as Array<{ kind: 'original' | 'translated'; text: string }>;

  const background = style.backgroundMode === 'black' ? 'rgba(0,0,0,.68)'
    : style.backgroundMode === 'white' ? 'rgba(255,255,255,.82)' : 'transparent';
  const updateStyle = (partial: Partial<SubtitleStyleSettings>) => onStyleChange({ ...style, ...partial });

  return (
    <div className={`pro-player ${controlsVisible ? 'controls-visible' : 'controls-hidden'} ${fallbackFullscreen ? 'fallback-fullscreen' : ''}`} ref={wrapperRef}
      tabIndex={0} onMouseMove={revealControls} onMouseEnter={revealControls}
      onMouseLeave={() => playing && !showSubtitleMenu && setControlsVisible(false)}
      onKeyDown={event => {
        if (event.key === 'Escape' && showSubtitleMenu) {
          event.preventDefault();
          event.stopPropagation();
          setShowSubtitleMenu(false);
          return;
        }
        if (event.key === ' ') { event.preventDefault(); togglePlay(); }
        if (event.key === 'ArrowLeft') seekTo(time - 5);
        if (event.key === 'ArrowRight') seekTo(time + 5);
        if (event.key.toLowerCase() === 'f') void toggleFullscreen();
        if (event.key === 'Escape' && fallbackFullscreen) {
          event.preventDefault();
          event.stopPropagation();
          setFallbackFullscreen(false);
          setFullscreen(false);
        }
      }}>
      <video ref={videoRef} className="pro-player-video" preload="metadata" playsInline
        onClick={togglePlay}
        onDoubleClick={() => void toggleFullscreen()}
        onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
        onTimeUpdate={() => {
          const current = videoRef.current?.currentTime || 0;
          setTime(current); onTimeUpdate(current);
        }}
        onLoadedMetadata={() => {
          const next = videoRef.current?.duration || 0;
          setDuration(next); onDurationChange?.(next);
        }}
        onEnded={() => setPlaying(false)}>
        <source src={videoUrl} />
      </video>

      {!videoUrl && <div className="player-empty">选择或导入视频开始工作</div>}

      {lines.length > 0 && <div className="pro-subtitle-overlay"
        style={{ top: `${style.verticalPosition}%`, maxWidth: `${style.maxWidth}%` }}>
        {lines.map((line, index) => <div key={`${active?.id}-${line.kind}`} className={`pro-subtitle-line ${line.kind}`}
          style={{
            fontSize: `${line.kind === 'original' ? style.originalFontSize : style.translatedFontSize}px`,
            fontFamily: style.fontFamily,
            color: line.kind === 'original' ? style.originalTextColor : style.translatedTextColor,
            background, marginTop: index ? `${style.lineGap}px` : 0,
            // 白字配浅色背景时也保留描边，避免用户切换背景后字幕失去对比度。
            textShadow: style.shadow
              ? '-1px -1px 2px #000,1px -1px 2px #000,-1px 1px 2px #000,1px 1px 2px #000' : 'none',
          }}>{line.text}</div>)}
      </div>}

      {style.mode === 'translated' && active && !translated && <div className="player-caption-hint">此处暂无译文</div>}

      <div className="player-control-surface" onMouseMove={revealControls}>
        <div className="pro-player-controls">
          <input className="player-seek" aria-label="播放进度" type="range" min={0} max={duration || 1}
            step={0.01} value={Math.min(time, duration || 1)} onChange={event => seekTo(Number(event.target.value))}
            style={{ '--seek': `${duration ? time / duration * 100 : 0}%` } as React.CSSProperties} />
          <div className="player-control-row">
            <button className="player-icon-btn" aria-label={playing ? '暂停' : '播放'} onClick={togglePlay}>
              <ControlIcon src={playing ? pauseIcon : playIcon} />
            </button>
            <button className="player-icon-btn" aria-label={muted ? '取消静音' : '静音'} onClick={() => {
              const next = !muted; setMuted(next); if (videoRef.current) videoRef.current.muted = next;
            }}><ControlIcon src={muted || volume === 0 ? mutedIcon : volumeIcon} /></button>
            <input className="volume-slider" aria-label="音量" type="range" min={0} max={1} step={0.05} value={volume}
              onChange={event => { const next = Number(event.target.value); setVolume(next); if (videoRef.current) videoRef.current.volume = next; }} />
            <span className="player-time">{timecode(time)} / {timecode(duration)}</span>
            <span className="control-spacer" />
            <AppSelect className="rate-select" label="播放速度" value={String(rate)} onChange={value=>{const next=Number(value);setRate(next);if(videoRef.current)videoRef.current.playbackRate=next;}} options={[0.5,0.75,1,1.25,1.5,2].map(value=>({value:String(value),label:`${value}×`}))}/>
            <button className={`player-icon-btn ${style.mode !== 'off' ? 'active' : ''}`} aria-label="字幕设置"
              aria-haspopup="dialog" aria-expanded={showSubtitleMenu}
              onClick={() => setShowSubtitleMenu(value => !value)}><ControlIcon src={captionsIcon} /></button>
            <button className={`player-icon-btn ${theaterMode ? 'active' : ''}`} aria-label={theaterMode ? '退出剧院模式' : '剧院模式'}
              aria-keyshortcuts="T" title={theaterMode ? '退出剧院模式 (T / Esc)' : '剧院模式 (T)'}
              onClick={() => onTheaterModeChange(!theaterMode)}><ControlIcon src={theaterIcon} /></button>
            <button className={`player-icon-btn ${fullscreen ? 'active' : ''}`} aria-label={fullscreen ? '退出全屏' : '全屏'}
              onClick={() => void toggleFullscreen()}><ControlIcon src={fullscreenIcon} /></button>
          </div>
        </div>
      </div>

      {showSubtitleMenu && <div className="player-subtitle-menu" role="dialog" aria-label="字幕显示设置">
        <div className="player-menu-title"><strong>字幕显示</strong><button aria-label="关闭字幕设置" onClick={() => setShowSubtitleMenu(false)}>✕</button></div>
        <label>显示内容
          <AppSelect value={style.mode} onChange={mode=>updateStyle({mode:mode as SubtitleDisplayMode})} label="显示内容" options={MODE_OPTIONS}/>
        </label>
        <label>字幕字体
          <AppSelect value={style.fontFamily} onChange={fontFamily=>updateStyle({fontFamily})} label="字幕字体" searchable options={SUBTITLE_FONT_OPTIONS}/>
        </label>
        <div className="player-color-grid">
          <label className="player-color-field">原文颜色 <span>{style.originalTextColor.toUpperCase()}</span>
            <input type="color" value={style.originalTextColor}
              onChange={event => updateStyle({ originalTextColor: event.target.value, textColor: event.target.value })} />
          </label>
          <label className="player-color-field">译文颜色 <span>{style.translatedTextColor.toUpperCase()}</span>
            <input type="color" value={style.translatedTextColor}
              onChange={event => updateStyle({ translatedTextColor: event.target.value })} />
          </label>
        </div>
        <label>原文字号 <span>{style.originalFontSize}px</span>
          <input type="range" min={14} max={58} value={style.originalFontSize}
            onChange={event => updateStyle({ originalFontSize: Number(event.target.value) })} />
        </label>
        <label>译文字号 <span>{style.translatedFontSize}px</span>
          <input type="range" min={14} max={58} value={style.translatedFontSize}
            onChange={event => updateStyle({ translatedFontSize: Number(event.target.value) })} />
        </label>
        <label>字幕位置 <span>{style.verticalPosition}%</span>
          <input type="range" min={12} max={86} value={Math.min(86, style.verticalPosition)}
            onChange={event => updateStyle({ verticalPosition: Number(event.target.value) })} />
        </label>
        <label>背景
          <AppSelect value={style.backgroundMode} onChange={backgroundMode=>updateStyle({backgroundMode:backgroundMode as SubtitleStyleSettings['backgroundMode']})} label="字幕背景" options={[{value:'black',label:'半透明黑底'},{value:'none',label:'无背景'},{value:'white',label:'浅色背景'}]}/>
        </label>
      </div>}
    </div>
  );
});

export default SubtitlePlayer;
