// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from 'vitest';
import {
  DEFAULT_SUBTITLE_STYLE,
  clampSubtitlePosition,
  loadSubtitleStyle,
  saveSubtitleStyle,
} from './subtitleStyle';

describe('subtitle position persistence', () => {
  beforeEach(() => localStorage.clear());

  it.each([
    [-10, 0],
    [0, 0],
    [50, 50],
    [86, 86],
    [100, 100],
    [120, 100],
  ])('clamps %s to %s', (input, expected) => {
    expect(clampSubtitlePosition(input)).toBe(expected);
  });

  it('clamps legacy local settings while preserving the 82% default', () => {
    expect(loadSubtitleStyle().verticalPosition).toBe(82);
    localStorage.setItem('subtitle_factory_subtitle_style', JSON.stringify({ verticalPosition: 120 }));
    expect(loadSubtitleStyle().verticalPosition).toBe(100);
    saveSubtitleStyle({ ...DEFAULT_SUBTITLE_STYLE, verticalPosition: -20 });
    expect(loadSubtitleStyle().verticalPosition).toBe(0);
  });
});
