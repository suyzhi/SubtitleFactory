// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest';

const invokeMock = vi.hoisted(() => vi.fn());

vi.mock('@tauri-apps/api/core', () => ({ invoke: invokeMock }));

describe('distribution session', () => {
  beforeEach(() => {
    vi.resetModules();
    invokeMock.mockReset();
    delete (window as any).__TAURI_INTERNALS__;
  });

  it('defaults browser development to the direct feature set', async () => {
    const api = await import('./backend');
    await api.initializeBackendSession();
    expect(api.getDistributionChannel()).toBe('direct');
    expect(api.youtubeFeaturesEnabled()).toBe(true);
    expect(api.filesystemAutomationEnabled()).toBe(true);
    expect(api.externalRuntimePathsEnabled()).toBe(true);
  });

  it('uses the immutable capabilities returned by the Tauri launcher', async () => {
    (window as any).__TAURI_INTERNALS__ = {};
    invokeMock.mockResolvedValue({
      baseUrl: 'http://127.0.0.1:49152',
      token: 'session-token',
      distributionChannel: 'app_store',
      youtubeEnabled: false,
      filesystemAutomationEnabled: false,
      externalRuntimePathsEnabled: false,
    });
    const api = await import('./backend');
    await api.initializeBackendSession();
    expect(invokeMock).toHaveBeenCalledWith('backend_session');
    expect(api.getDistributionChannel()).toBe('app_store');
    expect(api.youtubeFeaturesEnabled()).toBe(false);
    expect(api.filesystemAutomationEnabled()).toBe(false);
    expect(api.externalRuntimePathsEnabled()).toBe(false);
  });
});
