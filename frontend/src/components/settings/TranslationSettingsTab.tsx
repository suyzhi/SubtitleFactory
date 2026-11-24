import { useState } from 'react';
import { languageLabel } from '../../languages';
import type { AppSettings } from '../../types';
import AppSelect from '../AppSelect';
import LanguagePicker from '../LanguagePicker';
import { SettingsSection } from './SettingsPrimitives';

interface Props {
  draft: AppSettings;
  resolvedTargetLanguage: string;
  onUpdateDraft: (partial: Partial<AppSettings>) => void;
}

export function TranslationSettingsTab({ draft, resolvedTargetLanguage, onUpdateDraft }: Props) {
  const [favoriteLanguage, setFavoriteLanguage] = useState('fr');
  const favoriteLanguages = draft.favorite_languages || ['zh', 'en', 'ja', 'ko'];

  const addFavoriteLanguage = () => {
    const language = favoriteLanguage.trim();
    if (!language || language === 'auto' || language === 'none') return;
    if (!favoriteLanguages.includes(language)) {
      onUpdateDraft({ favorite_languages: [...favoriteLanguages, language] });
    }
  };

  return (
    <>
      <SettingsSection
        title="默认语言"
        description="目标语言支持直接输入自定义语言或语言代码。"
      >
        <label className="settings-field horizontal">
          <span>
            <strong>目标语言</strong>
            <small>用于新建项目和快速翻译</small>
          </span>
          <LanguagePicker
            mode="target"
            allowCustom
            value={resolvedTargetLanguage}
            onChange={translation_target_language =>
              onUpdateDraft({ translation_target_language })
            }
          />
        </label>
        <label className="settings-field horizontal">
          <span>
            <strong>双语顺序</strong>
            <small>可在单个导出中临时覆盖</small>
          </span>
          <AppSelect
            value={String(draft.bilingual_order || 'original_first')}
            onChange={bilingual_order => onUpdateDraft({ bilingual_order })}
            label="双语顺序"
            options={[
              { value: 'original_first', label: '原文在上' },
              { value: 'translated_first', label: '译文在上' },
            ]}
          />
        </label>
      </SettingsSection>

      <SettingsSection
        title="常用语言"
        description="保存后用于快速选择；可搜索内置语言，也可输入自定义语言代码。"
      >
        <div className="favorite-language-add">
          <LanguagePicker
            mode="target"
            allowCustom
            value={favoriteLanguage}
            onChange={setFavoriteLanguage}
          />
          <button className="button secondary" onClick={addFavoriteLanguage}>
            添加
          </button>
        </div>
        <div className="language-tags">
          {favoriteLanguages.map(language => (
            <span key={language}>
              {languageLabel(language)}
              <button
                aria-label={`移除 ${languageLabel(language)}`}
                onClick={() =>
                  onUpdateDraft({
                    favorite_languages: favoriteLanguages.filter(item => item !== language),
                  })
                }
              >
                ×
              </button>
            </span>
          ))}
        </div>
      </SettingsSection>
    </>
  );
}
