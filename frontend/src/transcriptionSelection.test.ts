import { describe, expect, it } from 'vitest';
import { resolveConfiguredModel, resolveRuntimeSelection } from './transcriptionSelection';

describe('transcription selection', () => {
  it('resolves automatic model selection before looking up a runtime', () => {
    expect(resolveConfiguredModel('auto', 'large-v3')).toBe('large-v3');
    expect(resolveConfiguredModel('auto', null)).toBe('small');
    expect(resolveConfiguredModel('medium', 'small')).toBe('medium');
  });

  it('treats saved app settings as authoritative over stale local cache', () => {
    expect(resolveRuntimeSelection('small', { small: 'cpu' }, { small: 'mlx' }, 'coreml')).toBe('cpu');
    expect(resolveRuntimeSelection('small', {}, { small: 'mlx' }, 'cpu')).toBe('mlx');
    expect(resolveRuntimeSelection('small', {}, {}, 'coreml')).toBe('coreml');
  });
});
