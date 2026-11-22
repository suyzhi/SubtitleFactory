// 应用设置、AI 服务商配置、云端授权与健康检查 API

import type {
  AIProviderPreset,
  AISettings,
  AppSettings,
  AppSettingsResponse,
  HealthStatus,
  PathValidationResult,
} from '../types';
import {
  backendFetch,
  parseError,
  request,
} from './client';

export interface CloudAuthorization {
  capability: 'transcription';
  provider_id: string | null;
  granted: boolean | number;
  disclosure_version: string;
  granted_at: string | null;
  revoked_at: string | null;
}

export async function getCloudAuthorizations(): Promise<{ authorizations: CloudAuthorization[] }> {
  return request('/api/cloud-authorizations');
}

export async function setCloudAuthorization(
  capability: CloudAuthorization['capability'],
  granted: boolean,
  providerId?: string,
): Promise<void> {
  await request(`/api/cloud-authorizations/${capability}`, {
    method: 'PUT',
    body: JSON.stringify({
      granted,
      provider_id: providerId || null,
      disclosure_version: 'fun-asr-audio-v1',
    }),
  });
}

export async function getAISettings(): Promise<{
  settings: AISettings;
  presets: AIProviderPreset[];
}> {
  return request('/api/settings/ai');
}

export async function saveAISettings(settings: AISettings): Promise<{ settings: AISettings }> {
  return request('/api/settings/ai', { method: 'PUT', body: JSON.stringify(settings) });
}

export async function testAISettings(
  settings: AISettings,
): Promise<{ ok: boolean; latency_ms: number; settings: AISettings }> {
  return request('/api/settings/ai/test', { method: 'POST', body: JSON.stringify(settings) });
}

export interface AIProviderCard {
  provider_id: string;
  name: string;
  base_url: string;
  api_key: string;
  model: string;
  models: string[];
  enabled: boolean;
  has_api_key: boolean;
  supports_thinking: boolean;
  thinking_enabled: boolean;
  last_test_status?: string;
  last_latency_ms?: number;
}

export interface AIAssignments {
  clean_provider_id: string;
  translate_provider_id: string;
}

export interface AIProvidersResponse {
  providers: AIProviderCard[];
  assignments: AIAssignments;
}

export const getAIProviders = () => request<AIProvidersResponse>('/api/settings/ai/providers');

export const saveAIProvider = async (id: string, data: Partial<AIProviderCard>) =>
  (
    await request<{ provider: AIProviderCard }>(`/api/settings/ai/providers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    })
  ).provider;

export const testAIProvider = (id: string) =>
  request<{ ok: boolean; latency_ms: number }>(`/api/settings/ai/providers/${id}/test`, {
    method: 'POST',
  });

export const saveAIAssignments = (data: AIAssignments) =>
  request('/api/settings/ai/assignments', { method: 'PUT', body: JSON.stringify(data) });

export async function getAppSettings(): Promise<AppSettingsResponse> {
  return request('/api/settings/app');
}

export async function saveAppSettings(
  settings: Partial<AppSettings>,
): Promise<AppSettingsResponse> {
  return request('/api/settings/app', { method: 'PUT', body: JSON.stringify(settings) });
}

export async function validateAppPath(data: {
  kind: PathValidationResult['kind'];
  path: string;
}): Promise<PathValidationResult> {
  return request('/api/settings/app/validate-path', { method: 'POST', body: JSON.stringify(data) });
}

export async function checkHealth(): Promise<HealthStatus> {
  const res = await backendFetch('/api/health', { method: 'GET' });
  if (!res.ok) throw await parseError(res);
  return res.json();
}
