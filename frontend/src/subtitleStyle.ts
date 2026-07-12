import type { SubtitleStyleSettings } from './types';

const STORAGE_KEY = 'subtitle_factory_subtitle_style';

export const SUBTITLE_FONT_OPTIONS = [
  { label: '系统黑体', value: 'Inter, "PingFang SC", "Helvetica Neue", sans-serif' },
  { label: '苹方', value: '"PingFang SC", "Hiragino Sans GB", sans-serif' },
  { label: '宋体', value: '"Songti SC", "STSong", serif' },
  { label: '楷体', value: '"Kaiti SC", "STKaiti", serif' },
  { label: 'Arial', value: 'Arial, "Helvetica Neue", sans-serif' },
  { label: 'Georgia', value: 'Georgia, "Times New Roman", serif' },
  { label: '等宽字体', value: '"SF Mono", Menlo, Consolas, monospace' },
] as const;

export const DEFAULT_SUBTITLE_STYLE: SubtitleStyleSettings = {
  mode: 'bilingual_original_first',
  verticalPosition: 82,
  fontSize: 22,
  originalFontSize: 22,
  translatedFontSize: 22,
  fontFamily: SUBTITLE_FONT_OPTIONS[0].value,
  originalTextColor: '#ffffff',
  translatedTextColor: '#ffffff',
  textColor: '#ffffff',
  backgroundMode: 'black',
  shadow: true,
  maxWidth: 85,
  lineGap: 4,
};

export function loadSubtitleStyle(): SubtitleStyleSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_SUBTITLE_STYLE };
    const parsed = JSON.parse(raw);
    const legacySize = Number(parsed.fontSize) || DEFAULT_SUBTITLE_STYLE.fontSize;
    const legacyColor = typeof parsed.textColor === 'string' ? parsed.textColor : DEFAULT_SUBTITLE_STYLE.textColor;
    return {
      ...DEFAULT_SUBTITLE_STYLE,
      ...parsed,
      originalFontSize: Number(parsed.originalFontSize) || legacySize,
      translatedFontSize: Number(parsed.translatedFontSize) || legacySize,
      fontFamily: typeof parsed.fontFamily === 'string' ? parsed.fontFamily : DEFAULT_SUBTITLE_STYLE.fontFamily,
      originalTextColor: typeof parsed.originalTextColor === 'string' ? parsed.originalTextColor : legacyColor,
      translatedTextColor: typeof parsed.translatedTextColor === 'string' ? parsed.translatedTextColor : legacyColor,
    };
  } catch {
    return { ...DEFAULT_SUBTITLE_STYLE };
  }
}

export function saveSubtitleStyle(style: SubtitleStyleSettings) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(style));
  } catch {
    // localStorage 不可用时仍允许本次会话内继续调整。
  }
}
