import * as api from './api/backend';
import type { AIProviderPreset, AISettings, AppSettings, AppSettingsResponse, Project } from './types';

export interface AppBootstrapSnapshot {
  projects: Project[];
  trashProjects: Project[];
  ai: { settings: AISettings; presets: AIProviderPreset[] } | null;
  app: AppSettingsResponse;
}

/**
 * Load optional startup data after the health endpoint has confirmed that the
 * local backend is reachable. AI credentials can be unavailable in an ad-hoc
 * signed macOS build without making local transcription features unavailable.
 */
export async function loadAppBootstrap(): Promise<AppBootstrapSnapshot> {
  // Keep the two library reads sequential during cold start. Some embedded
  // browser/WebView stacks cancel one of two simultaneous CORS-preflighted
  // requests while the bundled backend is still warming up, which made the
  // trash counter incorrectly stay at zero for the whole session.
  const loadLibraries = async () => {
    const active = await api.listProjects().catch(() => ({ projects: [] as Project[] }));
    const deleted = await api.listProjects({ deleted: true }).catch(() => ({ projects: [] as Project[] }));
    return { active, deleted };
  };
  const [{ active, deleted }, ai, app] = await Promise.all([
    loadLibraries(),
    api.getAISettings().catch(() => null),
    api.getAppSettings().catch(() => ({ settings: {} as AppSettings, warnings: [] })),
  ]);

  return {
    projects: active.projects,
    trashProjects: deleted.projects,
    ai,
    app,
  };
}
