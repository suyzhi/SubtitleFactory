import * as api from '../../api/backend';
import type { AppSettings, HealthStatus, PathValidationResult } from '../../types';
import AppSelect from '../AppSelect';
import { backupKind, bytes, runtimeCopy } from './settingsHelpers';
import { PathSettingField, RuntimeRow, SettingsSection } from './SettingsPrimitives';

interface Props {
  draft: AppSettings;
  youtubeEnabled: boolean;
  externalPathsEnabled: boolean;
  health: HealthStatus | null;
  pathChecks: Record<string, PathValidationResult>;
  backupState: api.BackupsResponse;
  busy: boolean;
  onUpdateDraft: (partial: Partial<AppSettings>) => void;
  onRefreshHealth: () => void;
  onChoosePath: (
    field: keyof AppSettings,
    kind: PathValidationResult['kind'],
    directory?: boolean,
  ) => void;
  onValidatePath: (kind: PathValidationResult['kind'], path: string) => void;
  onClearPath: (field: keyof AppSettings, kind: PathValidationResult['kind']) => void;
  onBackupNow: () => void;
  onRestoreBackup: (name: string) => void;
}

export function StorageSettingsTab({
  draft,
  youtubeEnabled,
  externalPathsEnabled,
  health,
  pathChecks,
  backupState,
  busy,
  onUpdateDraft,
  onRefreshHealth,
  onChoosePath,
  onValidatePath,
  onClearPath,
  onBackupNow,
  onRestoreBackup,
}: Props) {
  const runtime = health?.runtime;
  return (
    <>
      {youtubeEnabled && (
        <SettingsSection
          title="下载偏好"
          description="下载时会移除播放定位参数，并获取完整视频。"
        >
          <label className="settings-field horizontal">
            <span>
              <strong>画质</strong>
              <small>高清画面与音频需要 FFmpeg 合并</small>
            </span>
            <AppSelect
              value={String(draft.download_quality || 'best')}
              onChange={download_quality => onUpdateDraft({ download_quality })}
              label="下载画质"
              options={[
                { value: 'best', label: '最佳可用' },
                { value: '1080p', label: '最高 1080p' },
                { value: '720p', label: '最高 720p' },
              ]}
            />
          </label>
          <label className="settings-field horizontal">
            <span>
              <strong>容器</strong>
            </span>
            <AppSelect
              value={draft.download_container || 'mp4'}
              onChange={download_container =>
                onUpdateDraft({
                  download_container: download_container as AppSettings['download_container'],
                })
              }
              label="下载容器"
              options={[
                { value: 'mp4', label: 'MP4' },
                { value: 'mkv', label: 'MKV' },
                { value: 'webm', label: 'WebM' },
              ]}
            />
          </label>
        </SettingsSection>
      )}

      <SettingsSection
        title="运行状态"
        description="处理前请确保本地媒体引擎与存储均可用。"
        action={
          <button className="button secondary" onClick={onRefreshHealth}>
            重新检查
          </button>
        }
      >
        <RuntimeRow label="FFmpeg" value={runtimeCopy(runtime?.ffmpeg as any)} />
        <RuntimeRow label="FFprobe" value={runtimeCopy(runtime?.ffprobe as any)} />
        {youtubeEnabled && <RuntimeRow label="yt-dlp" value={runtimeCopy(runtime?.yt_dlp as any)} />}
        {youtubeEnabled && <RuntimeRow label="Deno" value={runtimeCopy(runtime?.deno as any)} />}
        {youtubeEnabled && (
          <RuntimeRow label="EJS 挑战组件" value={runtimeCopy(runtime?.ejs as any)} />
        )}
        <RuntimeRow label="输出目录" value={runtimeCopy(runtime?.output_directory as any)} />
        <RuntimeRow
          label="磁盘空间"
          value={{
            ...runtimeCopy(runtime?.disk as any),
            detail: runtime?.disk?.free_bytes
              ? `${bytes(runtime.disk.free_bytes)} 可用`
              : runtimeCopy(runtime?.disk as any).detail,
          }}
        />
      </SettingsSection>

      {externalPathsEnabled && (
        <SettingsSection
          title="路径"
          description="下载器固定使用 App 内置的 yt-dlp Python API；旧版 yt_dlp_path 设置会继续保留但不再参与下载。"
        >
          <PathSettingField
            label="下载目录"
            field="download_directory"
            kind="download_directory"
            placeholder="App 默认数据目录"
            directory
            draft={draft}
            check={pathChecks.download_directory}
            busy={busy}
            onChoosePath={onChoosePath}
            onValidatePath={onValidatePath}
            onClearPath={onClearPath}
          />
          <PathSettingField
            label="FFmpeg 自定义路径"
            field="ffmpeg_path"
            kind="ffmpeg"
            placeholder="通常无需设置"
            draft={draft}
            check={pathChecks.ffmpeg}
            busy={busy}
            onChoosePath={onChoosePath}
            onValidatePath={onValidatePath}
            onClearPath={onClearPath}
          />
        </SettingsSection>
      )}

      <SettingsSection
        title="数据库备份"
        description="默认保留 7 份每日备份和 4 份每周备份；恢复会在安全重启时原子应用。"
        action={
          <button className="button secondary" disabled={busy} onClick={onBackupNow}>
            立即备份
          </button>
        }
      >
        {backupState.pending_restore && (
          <div className="settings-notice warning">
            已排队恢复 {backupState.pending_restore.source_name || '数据库备份'}，下次启动前应用。
          </div>
        )}
        {backupState.last_restore && !backupState.pending_restore && (
          <div className="settings-notice">
            上次恢复：{backupState.last_restore.source_name || '数据库备份'} ·{' '}
            {backupState.last_restore.applied_at || '已完成'}
          </div>
        )}
        <div className="backup-list">
          {!backupState.backups.length && <span className="settings-empty">尚无备份</span>}
          {backupState.backups.slice(0, 8).map(backup => (
            <div className="backup-row" key={backup.name}>
              <span>
                <strong>{backup.name}</strong>
                <small>
                  {backupKind(backup.kind)} · {backup.modified_at} · {bytes(backup.size)}
                  {backup.hash ? ' · 有校验记录' : ''}
                </small>
              </span>
              <button
                className="button secondary"
                disabled={busy}
                onClick={() => onRestoreBackup(backup.name)}
              >
                验证并恢复
              </button>
            </div>
          ))}
        </div>
        <button
          className="button secondary"
          disabled={!backupState.directory}
          onClick={() => void api.revealLocalPath(backupState.directory)}
        >
          在 Finder 中打开备份目录
        </button>
      </SettingsSection>
    </>
  );
}
