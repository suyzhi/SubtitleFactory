// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from './api/backend';
import { loadAppBootstrap } from './appBootstrap';

vi.mock('./api/backend', () => ({
  listProjects: vi.fn(),
  getAISettings: vi.fn(),
  getAppSettings: vi.fn(),
}));

describe('loadAppBootstrap', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.listProjects).mockImplementation(async options => ({
      projects: options?.deleted ? [{ id: 'trash' }] : [{ id: 'active' }],
    }) as never);
    vi.mocked(api.getAppSettings).mockResolvedValue({ settings: { startup_behavior: 'project_library' }, warnings: [] });
  });

  it('keeps the local project library usable when Keychain-backed AI settings fail', async () => {
    vi.mocked(api.getAISettings).mockRejectedValue(new Error('macOS Keychain unavailable'));

    const snapshot = await loadAppBootstrap();

    expect(snapshot.projects).toEqual([{ id: 'active' }]);
    expect(snapshot.trashProjects).toEqual([{ id: 'trash' }]);
    expect(snapshot.ai).toBeNull();
    expect(snapshot.app.settings.startup_behavior).toBe('project_library');
    const [activeCall, deletedCall] = vi.mocked(api.listProjects).mock.invocationCallOrder;
    expect(activeCall).toBeLessThan(deletedCall);
  });
});
