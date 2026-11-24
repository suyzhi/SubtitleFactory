import type { ReactNode } from 'react';
import type { AppSettings, PathValidationResult } from '../../types';
import { useSlidingIndicator } from '../../utils/motion';

export function SettingsSection({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="settings-section">
      <header>
        <div>
          <h3>{title}</h3>
          {description && <p>{description}</p>}
        </div>
        {action}
      </header>
      <div className="settings-section-body">{children}</div>
    </section>
  );
}

export function Toggle({
  label,
  detail,
  checked,
  onChange,
}: {
  label: string;
  detail?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="settings-field horizontal toggle-row">
      <span>
        <strong>{label}</strong>
        {detail && <small>{detail}</small>}
      </span>
      <input
        type="checkbox"
        checked={checked}
        onChange={event => onChange(event.target.checked)}
      />
    </label>
  );
}

export function Segmented({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (value: string, origin: HTMLElement) => void;
  options: string[][];
}) {
  const ref = useSlidingIndicator<HTMLDivElement>(value);
  return (
    <div ref={ref} className="segmented-control sliding-tabs">
      {options.map(([id, label]) => (
        <button
          className={value === id ? 'active' : ''}
          key={id}
          onClick={event => onChange(id, event.currentTarget)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function RuntimeRow({
  label,
  value,
}: {
  label: string;
  value: { ok: boolean; title: string; detail: string };
}) {
  return (
    <div className="runtime-row">
      <span className={`status-orb ${value.ok ? 'ok' : 'error'}`} />
      <div>
        <strong>{label}</strong>
        <small>{value.detail || value.title}</small>
      </div>
      <em>{value.ok ? '可用' : value.title}</em>
    </div>
  );
}

export function PathSettingField({
  label,
  field,
  kind,
  placeholder,
  directory = false,
  draft,
  check,
  busy,
  onChoosePath,
  onValidatePath,
  onClearPath,
}: {
  label: string;
  field: keyof AppSettings;
  kind: PathValidationResult['kind'];
  placeholder: string;
  directory?: boolean;
  draft: AppSettings;
  check?: PathValidationResult;
  busy: boolean;
  onChoosePath: (
    field: keyof AppSettings,
    kind: PathValidationResult['kind'],
    directory?: boolean,
  ) => void;
  onValidatePath: (kind: PathValidationResult['kind'], path: string) => void;
  onClearPath: (field: keyof AppSettings, kind: PathValidationResult['kind']) => void;
}) {
  const value = String(draft[field] || '');
  return (
    <div className="settings-field path-setting">
      <span>
        <strong>{label}</strong>
        <small>{placeholder}</small>
      </span>
      <div className="path-control">
        <input readOnly value={value} placeholder={placeholder} />
        <button className="button secondary" onClick={() => onChoosePath(field, kind, directory)}>
          选择…
        </button>
        <button
          className="button secondary"
          disabled={!value || busy}
          onClick={() => onValidatePath(kind, value)}
        >
          校验
        </button>
        {value && (
          <button className="button secondary" onClick={() => onClearPath(field, kind)}>
            清除
          </button>
        )}
      </div>
      {check && (
        <small className={`path-result ${check.ok ? 'success' : 'failure'}`}>
          {check.ok ? '✓' : '!'}{' '}
          {check.resolved_path || check.reason || (check.ok ? '路径可用' : '路径不可用')}
        </small>
      )}
    </div>
  );
}
