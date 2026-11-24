import type { RefObject } from 'react';
import { createPortal } from 'react-dom';
import * as api from '../api/backend';
import type { AppSettings, HealthStatus, ProcessingConfig } from '../types';
import { useSlidingIndicator } from '../utils/motion';
import { AboutSettingsTab } from './settings/AboutSettingsTab';
import { AISettingsTab } from './settings/AISettingsTab';
import { AppearanceSettingsTab } from './settings/AppearanceSettingsTab';
import { GeneralSettingsTab } from './settings/GeneralSettingsTab';
import { SETTINGS_CATEGORIES, type ModelStatusResult } from './settings/settingsHelpers';
import { StorageSettingsTab } from './settings/StorageSettingsTab';
import { TranscriptionSettingsTab } from './settings/TranscriptionSettingsTab';
import { TranslationSettingsTab } from './settings/TranslationSettingsTab';
import { useSettingsCenter } from './settings/useSettingsCenter';

const APP_VERSION = __APP_VERSION__;

interface Props {
  open: boolean;
  onClose: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
  config: ProcessingConfig;
  onConfigChange: (config: ProcessingConfig) => void;
  appSettings: AppSettings;
  onAppSettingsChange: (settings: AppSettings) => void;
  onAIProvidersChange: (state: api.AIProvidersResponse) => void;
  theme: 'light' | 'dark';
  onThemeChange: (theme: 'light' | 'dark', origin?: HTMLElement) => void;
  motionEnabled: boolean;
  onMotionEnabledChange: (enabled: boolean) => void;
  health: HealthStatus | null;
  onRefreshHealth: () => void;
  modelStatus: ModelStatusResult | null;
  onRefreshModels: () => void;
  onOpenLogs: () => void;
}

export default function SettingsCenter(props: Props) {
  const {
    open,
    onClose,
    theme,
    onThemeChange,
    motionEnabled,
    onMotionEnabledChange,
    health,
    onRefreshHealth,
    modelStatus,
    onRefreshModels,
    onOpenLogs,
  } = props;
  const youtubeEnabled = api.youtubeFeaturesEnabled();
  const externalPathsEnabled = api.externalRuntimePathsEnabled();
  const distributionChannel = api.getDistributionChannel();

  const state = useSettingsCenter(props);
  const navRef = useSlidingIndicator<HTMLElement>(state.category);

  if (!open) return null;

  const showSaveFooter = !['ai', 'appearance', 'about'].includes(state.category);

  return createPortal(
    <div
      className={`modal-backdrop settings-backdrop theme-${theme} ${motionEnabled ? '' : 'motion-off'}`}
      role="presentation"
      onMouseDown={onClose}
    >
      <section
        ref={state.dialogRef}
        tabIndex={-1}
        className="settings-center"
        role="dialog"
        aria-modal="true"
        aria-label="设置中心"
        onMouseDown={event => event.stopPropagation()}
      >
        <aside className="settings-navigation">
          <div className="settings-title">
            <strong>设置</strong>
            <small>字幕工厂</small>
          </div>
          <nav ref={navRef} className="sliding-tabs" aria-label="设置分类">
            {SETTINGS_CATEGORIES.map(item => (
              <button
                key={item.id}
                className={state.category === item.id ? 'active' : ''}
                onClick={() => {
                  state.setCategory(item.id);
                  state.setMessage('');
                  state.setError('');
                }}
              >
                <i>{item.icon}</i>
                <span>{item.label}</span>
              </button>
            ))}
          </nav>
          <small className="settings-version">Version {health?.version || APP_VERSION}</small>
        </aside>

        <div className="settings-content">
          <header className="settings-content-header">
            <div>
              <h2>{SETTINGS_CATEGORIES.find(item => item.id === state.category)?.label}</h2>
              <p>更改会保存在这台 Mac 上。</p>
            </div>
            <button className="icon-button" aria-label="关闭设置" onClick={onClose}>
              ×
            </button>
          </header>

          <div className="settings-scroll" key={state.category}>
            {state.warnings.map(warning => (
              <div className="settings-notice warning" key={`${warning.field}:${warning.code}`}>
                ! {warning.message}
              </div>
            ))}

            {state.category === 'general' && (
              <GeneralSettingsTab draft={state.draft} onUpdateDraft={state.updateDraft} />
            )}

            {state.category === 'transcription' && (
              <TranscriptionSettingsTab
                draft={state.draft}
                resolvedModel={state.resolvedModel}
                resolvedSourceLanguage={state.resolvedSourceLanguage}
                externalPathsEnabled={externalPathsEnabled}
                modelStatus={modelStatus}
                providerCards={state.providerCards}
                cloudAuthorizations={state.cloudAuthorizations}
                scannedModels={state.scannedModels}
                pathChecks={state.pathChecks}
                busy={state.busy}
                validatingModel={state.validatingModel}
                preparingModel={state.preparingModel}
                modelTasks={state.modelTasks}
                onUpdateDraft={state.updateDraft}
                onRefreshModels={onRefreshModels}
                onScanModelFolder={() => void state.scanModelFolder()}
                onImportScannedModel={model => void state.importScannedModel(model)}
                onValidateModel={modelId => void state.validateModel(modelId)}
                onPrepareModel={(modelId, runtime, repair) =>
                  void state.prepareModel(modelId, runtime, repair)
                }
                onRemoveModel={model => void state.removeModel(model)}
                onUpdateCloudAuth={granted =>
                  void state.updateTranscriptionCloudAuthorization(granted)
                }
                onRequireDashscopeConfig={() => {
                  state.setCategory('ai');
                  state.setError('请先保存通义千问（百炼）API Key。');
                }}
                onChoosePath={(field, kind, directory) =>
                  void state.choosePath(field, kind, directory)
                }
                onValidatePath={(kind, path) => void state.validatePath(kind, path)}
                onClearPath={state.clearPath}
              />
            )}

            {state.category === 'ai' && (
              <AISettingsTab
                providerCards={state.providerCards}
                assignments={state.assignments}
                busy={state.busy}
                onAssignmentsChange={state.setAssignments}
                onSaveAssignments={() => void state.saveAssignments()}
                onUpdateProvider={state.updateProvider}
                onTestProvider={card => void state.testProvider(card)}
                onSaveProvider={card => void state.saveProvider(card)}
              />
            )}

            {state.category === 'translation' && (
              <TranslationSettingsTab
                draft={state.draft}
                resolvedTargetLanguage={state.resolvedTargetLanguage}
                onUpdateDraft={state.updateDraft}
              />
            )}

            {state.category === 'storage' && (
              <StorageSettingsTab
                draft={state.draft}
                youtubeEnabled={youtubeEnabled}
                externalPathsEnabled={externalPathsEnabled}
                health={health}
                pathChecks={state.pathChecks}
                backupState={state.backupState}
                busy={state.busy}
                onUpdateDraft={state.updateDraft}
                onRefreshHealth={onRefreshHealth}
                onChoosePath={(field, kind, directory) =>
                  void state.choosePath(field, kind, directory)
                }
                onValidatePath={(kind, path) => void state.validatePath(kind, path)}
                onClearPath={state.clearPath}
                onBackupNow={() => void state.backupNow()}
                onRestoreBackup={name => void state.restoreSelectedBackup(name)}
              />
            )}

            {state.category === 'appearance' && (
              <AppearanceSettingsTab
                theme={theme}
                onThemeChange={onThemeChange}
                motionEnabled={motionEnabled}
                onMotionEnabledChange={onMotionEnabledChange}
              />
            )}

            {state.category === 'about' && (
              <AboutSettingsTab
                appVersion={APP_VERSION}
                distributionChannel={distributionChannel}
                youtubeEnabled={youtubeEnabled}
                health={health}
                onCopyDiagnostics={() => void state.copyDiagnostics()}
                onExportDiagnostics={() => void state.exportDiagnostics()}
                onOpenLogs={() => {
                  onClose();
                  onOpenLogs();
                }}
                onOpenPrivacyPolicy={() => void state.openPrivacyPolicy()}
              />
            )}
          </div>

          {showSaveFooter && (
            <footer className="settings-footer">
              <div>
                {state.message && <span className="success-copy">{state.message}</span>}
                {state.error && <span className="error-copy">{state.error}</span>}
              </div>
              <button
                className="button primary"
                disabled={state.busy}
                onClick={() => void state.saveApp()}
              >
                {state.busy ? '正在保存…' : '保存更改'}
              </button>
            </footer>
          )}
          {!showSaveFooter && (state.message || state.error) && (
            <footer className="settings-footer">
              <div>
                {state.message && <span className="success-copy">{state.message}</span>}
                {state.error && <span className="error-copy">{state.error}</span>}
              </div>
            </footer>
          )}
        </div>
      </section>
    </div>,
    document.body,
  );
}
