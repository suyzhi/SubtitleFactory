import { useEffect, useId, useState } from 'react';
import { LANGUAGES, TARGET_LANGUAGES, languageLabel } from '../languages';

interface Props {
  value: string;
  onChange: (value: string) => void;
  mode?: 'source' | 'target';
  allowNone?: boolean;
  allowCustom?: boolean;
  disabled?: boolean;
  label?: string;
}

export default function LanguagePicker({
  value, onChange, mode = 'source', allowNone = false, allowCustom = false, disabled, label,
}: Props) {
  const id = useId().replace(/:/g, '');
  const options = mode === 'source' ? LANGUAGES : TARGET_LANGUAGES;
  const known = value === 'none' || options.some(option => option.code === value);
  const [query, setQuery] = useState(known ? languageLabel(value) : value);
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) setQuery(known ? languageLabel(value) : value);
  }, [focused, known, value]);

  return (
    <div className="language-picker">
      <input
        list={`languages-${id}`}
        value={query}
        disabled={disabled}
        aria-label={label || (mode === 'source' ? '源语言' : '目标语言')}
        placeholder={allowCustom ? '搜索或输入语言代码' : '搜索语言'}
        onFocus={event => { setFocused(true); event.currentTarget.select(); }}
        onChange={event => {
          const raw = event.target.value.trim();
          setQuery(event.target.value);
          const match = [...options, ...(allowNone ? [{ code: 'none', name: '不翻译', nativeName: 'None' }] : [])]
            .find(option => languageLabel(option.code) === raw || option.code.toLocaleLowerCase() === raw.toLocaleLowerCase());
          if (match) onChange(match.code);
          else if (allowCustom) onChange(raw);
        }}
        onBlur={() => {
          setFocused(false);
          const raw = query.trim();
          if (allowCustom && raw && !options.some(option => languageLabel(option.code) === raw)) onChange(raw);
        }}
      />
      <datalist id={`languages-${id}`}>
        {allowNone && <option value={languageLabel('none')}>none</option>}
        {options.map(option => <option key={option.code} value={languageLabel(option.code)}>{option.code}</option>)}
      </datalist>
      {allowCustom && !known && value && <small>自定义语言代码：{value}</small>}
    </div>
  );
}
