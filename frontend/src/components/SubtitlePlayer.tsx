import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
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
import { createYoutubePlayerSession } from '../api/backend';

interface Props {
  videoUrl?: string;
  youtubeVideoId?: string;
  onWebPlayerError?: (code: number) => void;
  segments: SubtitleSegment[];
  style: SubtitleStyleSettings;
  activeIdx: number;
  presentationMode: PlayerPresentationMode;
  onTimeUpdate: (time: number) => void;
  onDurationChange?: (duration: number) => void;
  onStyleChange: (style: SubtitleStyleSettings) => void;
  onPresentationModeChange: (mode: PlayerPresentationMode) => void;
}

export type PlayerPresentationMode = 'normal' | 'theater' | 'fullscreen';

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
  videoUrl, youtubeVideoId, onWebPlayerError, segments, style, activeIdx,
  presentationMode, onTimeUpdate, onDurationChange, onStyleChange,
  onPresentationModeChange,
}, ref) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const hideTimer = useRef<number | null>(null);
  const webReadyTimer = useRef<number | null>(null);
  const bridgeOriginRef = useRef('');
  const channelRef = useRef(globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2));
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [time, setTime] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [rate, setRate] = useState(1);
  const [availableRates, setAvailableRates] = useState([0.5, 0.75, 1, 1.25, 1.5, 2]);
  const [bridgeUrl, setBridgeUrl] = useState('');
  const [loopCurrent, setLoopCurrent] = useState(false);
  const [showSubtitleMenu, setShowSubtitleMenu] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  const fullscreen = presentationMode === 'fullscreen';
  const theaterMode = presentationMode === 'theater';
  const isWeb = Boolean(youtubeVideoId);
  const active = activeIdx >= 0 && activeIdx < segments.length ? segments[activeIdx] : null;

  const sendWebCommand = useCallback((
    command: 'play' | 'pause' | 'seek' | 'volume' | 'mute' | 'unmute' | 'rate',
    value?: number,
    allowSeekAhead = true,
  ) => {
    const origin = bridgeOriginRef.current;
    if (!origin) return;
    iframeRef.current?.contentWindow?.postMessage({
      source: 'subtitle-factory-host',
      channel: channelRef.current,
      command,
      value,
      allowSeekAhead,
    }, origin);
  }, []);

  const seekTo = useCallback((next: number) => {
    if (isWeb) {
      const target = Math.max(0, Math.min(next, duration || next));
      sendWebCommand('seek', target);
      setTime(target);
      onTimeUpdate(target);
      return;
    }
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = Math.max(0, Math.min(next, video.duration || next));
    setTime(video.currentTime);
    onTimeUpdate(video.currentTime);
  }, [duration, isWeb, onTimeUpdate, sendWebCommand]);

  useImperativeHandle(ref, () => ({ seekTo }), [seekTo]);

  useEffect(() => {
    if (isWeb) {
      setPlaying(false);
      setTime(0);
      setDuration(0);
      return;
    }
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    video.load();
    setPlaying(false);
    setTime(0);
    setDuration(0);
  }, [isWeb, videoUrl, youtubeVideoId]);

  useEffect(() => {
    if (!youtubeVideoId) {
      setBridgeUrl('');
      bridgeOriginRef.current = '';
      return;
    }
    let cancelled = false;
    createYoutubePlayerSession(youtubeVideoId, channelRef.current)
      .then(url => {
        if (cancelled) return;
        bridgeOriginRef.current = new URL(url).origin;
        setBridgeUrl(url);
      })
      .catch(() => {
        if (!cancelled) onWebPlayerError?.(401);
      });
    return () => { cancelled = true; };
  }, [onWebPlayerError, youtubeVideoId]);

  useEffect(() => {
    if (!isWeb || !bridgeUrl) return;
    let ready = false;
    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== bridgeOriginRef.current
          || event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data;
      if (!data || data.source !== 'subtitle-factory-youtube'
          || data.channel !== channelRef.current) return;
      if (data.type === 'ready') {
        ready = true;
        if (webReadyTimer.current) window.clearTimeout(webReadyTimer.current);
        const nextDuration = Number(data.duration || 0);
        setDuration(nextDuration);
        onDurationChange?.(nextDuration);
        setVolume(Math.max(0, Math.min(1, Number(data.volume ?? 100) / 100)));
        setMuted(Boolean(data.muted));
        const rates = Array.isArray(data.rates)
          ? data.rates.map(Number).filter((item: number) => Number.isFinite(item) && item > 0)
          : [];
        if (rates.length) setAvailableRates(rates);
      }
      if (data.type === 'time') {
        const nextTime = Number(data.time || 0);
        const nextDuration = Number(data.duration || 0);
        if (loopCurrent && active && nextTime >= active.end) {
          seekTo(active.start);
          return;
        }
        setTime(nextTime);
        onTimeUpdate(nextTime);
        if (nextDuration > 0) {
          setDuration(nextDuration);
          onDurationChange?.(nextDuration);
        }
        if (Number.isFinite(Number(data.state))) setPlaying(Number(data.state) === 1);
      }
      if (data.type === 'state') setPlaying(Number(data.state) === 1);
      if (data.type === 'rate') setRate(Number(data.rate || 1));
      if (data.type === 'autoplayBlocked') {
        setPlaying(false);
        setControlsVisible(true);
      }
      if (data.type === 'error') onWebPlayerError?.(Number(data.code || 5));
    };
    window.addEventListener('message', handleMessage);
    webReadyTimer.current = window.setTimeout(() => {
      if (!ready) onWebPlayerError?.(153);
    }, 12000);
    return () => {
      window.removeEventListener('message', handleMessage);
      if (webReadyTimer.current) window.clearTimeout(webReadyTimer.current);
    };
  }, [active, bridgeUrl, isWeb, loopCurrent, onDurationChange, onTimeUpdate, onWebPlayerError, seekTo]);

  useEffect(() => {
    if ('__TAURI_INTERNALS__' in window) return;
    const wrapper = wrapperRef.current;
    if (!wrapper) return;

    if (fullscreen && document.fullscreenElement !== wrapper) {
      void wrapper.requestFullscreen().catch(() => undefined);
    } else if (!fullscreen && document.fullscreenElement === wrapper) {
      void document.exitFullscreen().catch(() => undefined);
    }
  }, [fullscreen]);

  useEffect(() => {
    if ('__TAURI_INTERNALS__' in window) return;
    const update = () => {
      if (!document.fullscreenElement && presentationMode === 'fullscreen') {
        onPresentationModeChange('normal');
      }
    };
    document.addEventListener('fullscreenchange', update);
    return () => document.removeEventListener('fullscreenchange', update);
  }, [onPresentationModeChange, presentationMode]);

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
    if (isWeb) {
      sendWebCommand(playing ? 'pause' : 'play');
      return;
    }
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play().catch(() => {
        setPlaying(false);
        setControlsVisible(true);
      });
    } else video.pause();
  }, [isWeb, playing, sendWebCommand]);

  const toggleFullscreen = useCallback(() => {
    onPresentationModeChange(fullscreen ? 'normal' : 'fullscreen');
  }, [fullscreen, onPresentationModeChange]);

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
    <div className={`pro-player ${controlsVisible ? 'controls-visible' : 'controls-hidden'} ${fullscreen ? 'player-fullscreen' : ''}`} ref={wrapperRef}
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
        if (event.key === ',') seekTo(time - 1 / 25);
        if (event.key === '.') seekTo(time + 1 / 25);
        if (event.key.toLowerCase() === 'f') { event.preventDefault(); event.stopPropagation(); toggleFullscreen(); }
        if (event.key === 'Escape' && fullscreen) {
          event.preventDefault();
          event.stopPropagation();
          onPresentationModeChange('normal');
        }
      }}>
      {!isWeb && videoUrl && <video ref={videoRef} className="pro-player-video" preload="metadata" playsInline
        onClick={togglePlay}
        onDoubleClick={toggleFullscreen}
        onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
        onTimeUpdate={() => {
          const current = videoRef.current?.currentTime || 0;
          if (loopCurrent && active && current >= active.end) { seekTo(active.start); return; }
          setTime(current); onTimeUpdate(current);
        }}
        onLoadedMetadata={() => {
          const next = videoRef.current?.duration || 0;
          setDuration(next); onDurationChange?.(next);
        }}
        onEnded={() => setPlaying(false)}>
        <source src={videoUrl} />
      </video>}

      {isWeb && bridgeUrl && <iframe
        ref={iframeRef}
        className="pro-player-video pro-player-web"
        src={bridgeUrl}
        title="YouTube 网页播放器"
        allow="autoplay; encrypted-media; picture-in-picture"
        referrerPolicy="strict-origin-when-cross-origin"
      />}

      {!videoUrl && !isWeb && <div className="player-empty">选择或导入视频开始工作</div>}
      {isWeb && !bridgeUrl && <div className="player-empty">正在连接网页播放器…</div>}

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
            <button className="player-icon-btn player-step-btn" aria-label="后退一帧" title="后退一帧 (, )" onClick={() => seekTo(time - 1 / 25)}>‹</button>
            <button className="player-icon-btn player-step-btn" aria-label="前进一帧" title="前进一帧 (. )" onClick={() => seekTo(time + 1 / 25)}>›</button>
            <button className={`player-icon-btn player-step-btn ${loopCurrent ? 'active' : ''}`} aria-label="循环当前字幕" aria-pressed={loopCurrent} onClick={() => setLoopCurrent(value => !value)}>↻</button>
            <button className="player-icon-btn" aria-label={muted ? '取消静音' : '静音'} onClick={() => {
              const next = !muted;
              setMuted(next);
              if (isWeb) sendWebCommand(next ? 'mute' : 'unmute');
              else if (videoRef.current) videoRef.current.muted = next;
            }}><ControlIcon src={muted || volume === 0 ? mutedIcon : volumeIcon} /></button>
            <input className="volume-slider" aria-label="音量" type="range" min={0} max={1} step={0.05} value={volume}
              onChange={event => {
                const next = Number(event.target.value);
                setVolume(next);
                if (isWeb) sendWebCommand('volume', next * 100);
                else if (videoRef.current) videoRef.current.volume = next;
              }} />
            <span className="player-time">{timecode(time)} / {timecode(duration)}</span>
            <span className="control-spacer" />
            <AppSelect className="rate-select" label="播放速度" popoverMinWidth={112} value={String(rate)} onChange={value=>{
              const next=Number(value);
              setRate(next);
              if (isWeb) sendWebCommand('rate', next);
              else if(videoRef.current) videoRef.current.playbackRate=next;
            }} options={availableRates.map(value=>({value:String(value),label:`${value}×`}))}/>
            <button className={`player-icon-btn ${style.mode !== 'off' ? 'active' : ''}`} aria-label="字幕设置"
              aria-haspopup="dialog" aria-expanded={showSubtitleMenu}
              onClick={() => setShowSubtitleMenu(value => !value)}><ControlIcon src={captionsIcon} /></button>
            <button className={`player-icon-btn ${theaterMode ? 'active' : ''}`} aria-label={theaterMode ? '退出剧院模式' : '剧院模式'}
              aria-keyshortcuts="T" title={theaterMode ? '退出剧院模式 (T / Esc)' : '剧院模式 (T)'}
              onClick={() => onPresentationModeChange(theaterMode ? 'normal' : 'theater')}><ControlIcon src={theaterIcon} /></button>
            <button className={`player-icon-btn ${fullscreen ? 'active' : ''}`} aria-label={fullscreen ? '退出全屏' : '全屏'}
              aria-keyshortcuts="F" title={fullscreen ? '退出全屏 (F / Esc)' : '视频全屏 (F)'}
              onClick={toggleFullscreen}><ControlIcon src={fullscreenIcon} /></button>
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
