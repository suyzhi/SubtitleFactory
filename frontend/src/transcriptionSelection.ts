export function resolveConfiguredModel(configuredModel: string, recommendedModel?: string | null) {
  return configuredModel === 'auto' ? (recommendedModel || 'small') : configuredModel;
}

export function resolveRuntimeSelection(
  model: string,
  persisted: Record<string, string> | undefined,
  local: Record<string, string>,
  reportedRuntime?: string | null,
) {
  return String(persisted?.[model] || local[model] || reportedRuntime || '');
}
