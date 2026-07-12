export interface LanguageOption {
  code: string;
  name: string;
  nativeName: string;
}

export const LANGUAGES: LanguageOption[] = [
  { code: 'auto', name: '自动检测', nativeName: 'Auto' },
  { code: 'zh', name: '中文', nativeName: '中文' },
  { code: 'en', name: '英语', nativeName: 'English' },
  { code: 'ja', name: '日语', nativeName: '日本語' },
  { code: 'ko', name: '韩语', nativeName: '한국어' },
  { code: 'es', name: '西班牙语', nativeName: 'Español' },
  { code: 'fr', name: '法语', nativeName: 'Français' },
  { code: 'de', name: '德语', nativeName: 'Deutsch' },
  { code: 'pt', name: '葡萄牙语', nativeName: 'Português' },
  { code: 'it', name: '意大利语', nativeName: 'Italiano' },
  { code: 'ru', name: '俄语', nativeName: 'Русский' },
  { code: 'ar', name: '阿拉伯语', nativeName: 'العربية' },
  { code: 'hi', name: '印地语', nativeName: 'हिन्दी' },
  { code: 'id', name: '印尼语', nativeName: 'Bahasa Indonesia' },
  { code: 'vi', name: '越南语', nativeName: 'Tiếng Việt' },
  { code: 'th', name: '泰语', nativeName: 'ไทย' },
  { code: 'tr', name: '土耳其语', nativeName: 'Türkçe' },
  { code: 'pl', name: '波兰语', nativeName: 'Polski' },
  { code: 'nl', name: '荷兰语', nativeName: 'Nederlands' },
  { code: 'uk', name: '乌克兰语', nativeName: 'Українська' },
];

export const TARGET_LANGUAGES = LANGUAGES.filter(language => language.code !== 'auto');

export function languageLabel(code: string): string {
  if (code === 'none') return '不翻译';
  const language = LANGUAGES.find(item => item.code.toLocaleLowerCase() === code.toLocaleLowerCase());
  return language ? `${language.name} · ${language.nativeName}` : code;
}
