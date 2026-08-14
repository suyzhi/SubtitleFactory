// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api/backend';
import { DEFAULT_SUBTITLE_STYLE } from '../subtitleStyle';
import StyleTemplateBar from './StyleTemplateBar';

vi.mock('../api/backend', async importOriginal => {
  const actual = await importOriginal<typeof import('../api/backend')>();
  return {
    ...actual,
    getStyleTemplates: vi.fn().mockResolvedValue({ templates: [] }),
    createStyleTemplate: vi.fn().mockResolvedValue({ id: 'template-1' }),
  };
});

describe('StyleTemplateBar', () => {
  beforeEach(() => vi.clearAllMocks());

  it('saves a named template through an in-app form', async () => {
    const prompt = vi.spyOn(window, 'prompt');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><StyleTemplateBar style={DEFAULT_SUBTITLE_STYLE} onApply={vi.fn()}/></QueryClientProvider>);

    fireEvent.click(await screen.findByRole('button', { name: '保存当前样式' }));
    fireEvent.change(screen.getByRole('textbox', { name: '模板名称' }), { target: { value: '访谈样式' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(api.createStyleTemplate).toHaveBeenCalledWith('访谈样式', DEFAULT_SUBTITLE_STYLE));
    expect(prompt).not.toHaveBeenCalled();
  });
});
