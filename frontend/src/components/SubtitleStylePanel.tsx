// 字幕工厂 - 字幕样式设置面板组件

import type { SubtitleStyleSettings, SubtitleDisplayMode } from '../types';
import { DEFAULT_SUBTITLE_STYLE, saveSubtitleStyle, SUBTITLE_FONT_OPTIONS } from '../subtitleStyle';
import AppSelect from './AppSelect';

const TEXT_COLORS = [
  { label: '白色', value: '#ffffff' },
  { label: '黄色', value: '#f5e642' },
  { label: '黑色', value: '#000000' },
  { label: '红色', value: '#ff6b6b' },
  { label: '蓝色', value: '#74b9ff' },
];

const MODE_OPTIONS: { label: string; value: SubtitleDisplayMode }[] = [
  { label: '关闭字幕', value: 'off' },
  { label: '原文字幕', value: 'original' },
  { label: '译文字幕', value: 'translated' },
  { label: '双语 - 原文在上', value: 'bilingual_original_first' },
  { label: '双语 - 译文在上', value: 'bilingual_translated_first' },
];

interface Props {
  style: SubtitleStyleSettings;
  onChange: (style: SubtitleStyleSettings) => void;
  collapsed?: boolean;
  onToggle?: () => void;
}

export default function SubtitleStylePanel({ style, onChange, collapsed, onToggle }: Props) {
  const update = (partial: Partial<SubtitleStyleSettings>) => {
    const next = { ...style, ...partial };
    onChange(next);
    saveSubtitleStyle(next);
  };

  const reset = () => {
    onChange({ ...DEFAULT_SUBTITLE_STYLE });
    saveSubtitleStyle(DEFAULT_SUBTITLE_STYLE);
  };

  return (
    <section className="panel subtitle-style-panel">
      <div className="panel-header clickable" onClick={onToggle}>
        <h3>🎨 字幕样式</h3>
        <span className="collapse-icon">{collapsed ? '▶' : '▼'}</span>
      </div>

      {!collapsed && (
        <div className="subtitle-style-body">
          {/* 显示模式 */}
          <label className="style-label">显示模式
            <AppSelect value={style.mode} onChange={mode=>update({mode:mode as SubtitleDisplayMode})} label="显示模式" options={MODE_OPTIONS}/>
          </label>

          <label className="style-label">字幕字体
            <AppSelect value={style.fontFamily} onChange={fontFamily=>update({fontFamily})} label="字幕字体" searchable options={SUBTITLE_FONT_OPTIONS}/>
          </label>

          {/* 垂直位置 */}
          <label className="style-label">
            垂直位置 <span className="style-value">{style.verticalPosition}%</span>
            <input type="range" min={5} max={95} value={style.verticalPosition}
              onChange={e => update({ verticalPosition: Number(e.target.value) })} />
          </label>

          {/* 统一字号 */}
          <label className="style-label">
            统一字号 <span className="style-value">{style.originalFontSize}px</span>
            <input type="range" min={12} max={48} value={style.originalFontSize}
              onChange={e => {
                const size = Number(e.target.value);
                update({ fontSize: size, originalFontSize: size, translatedFontSize: size });
              }} />
          </label>

          {/* 最大宽度 */}
          <label className="style-label">
            最大宽度 <span className="style-value">{style.maxWidth}%</span>
            <input type="range" min={40} max={98} value={style.maxWidth}
              onChange={e => update({ maxWidth: Number(e.target.value) })} />
          </label>

          {/* 双语行距 */}
          <label className="style-label">
            行距 <span className="style-value">{style.lineGap}px</span>
            <input type="range" min={0} max={20} value={style.lineGap}
              onChange={e => update({ lineGap: Number(e.target.value) })} />
          </label>

          {/* 文字颜色 */}
          <label className="style-label">原文颜色
            <div className="color-options">
              {TEXT_COLORS.map(c => (
                <button key={c.value}
                  className={`color-swatch ${style.originalTextColor === c.value ? 'active' : ''}`}
                  style={{ background: c.value, border: c.value === '#ffffff' ? '1px solid #ccc' : undefined }}
                  onClick={() => update({ originalTextColor: c.value, textColor: c.value })}
                  title={c.label}
                />
              ))}
            </div>
          </label>
          <label className="style-label">译文颜色
            <div className="color-options">
              {TEXT_COLORS.map(c => (
                <button key={c.value}
                  className={`color-swatch ${style.translatedTextColor === c.value ? 'active' : ''}`}
                  style={{ background: c.value, border: c.value === '#ffffff' ? '1px solid #ccc' : undefined }}
                  onClick={() => update({ translatedTextColor: c.value })}
                  title={c.label}
                />
              ))}
            </div>
          </label>

          {/* 背景模式 */}
          <label className="style-label">背景
            <AppSelect value={style.backgroundMode} onChange={backgroundMode=>update({backgroundMode:backgroundMode as SubtitleStyleSettings['backgroundMode']})} label="字幕背景" options={[{value:'none',label:'无背景'},{value:'black',label:'半透明黑底'},{value:'white',label:'半透明白底'}]}/>
          </label>

          {/* 阴影 */}
          <label className="style-checkbox">
            <input type="checkbox" checked={style.shadow}
              onChange={e => update({ shadow: e.target.checked })} />
            文字阴影/描边
          </label>

          {/* 重置 */}
          <button className="btn btn-ghost reset-btn" onClick={reset}>↺ 重置默认</button>
        </div>
      )}
    </section>
  );
}
