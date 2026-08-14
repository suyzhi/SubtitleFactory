// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api/backend';
import GlossaryPanel from './GlossaryPanel';

vi.mock('../api/backend', async importOriginal => {
  const actual = await importOriginal<typeof import('../api/backend')>();
  return {
    ...actual,
    getGlossaries: vi.fn().mockResolvedValue({ glossaries: [] }),
    getGlossaryTerms: vi.fn().mockResolvedValue({ terms: [] }),
    createGlossary: vi.fn().mockResolvedValue({
      id: 'glossary-1', name: '产品术语', project_id: 'project', term_count: 0,
      source_language: 'auto', target_language: 'auto',
    }),
  };
});

describe('GlossaryPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('creates a glossary with an in-app form instead of a native prompt', async () => {
    const prompt = vi.spyOn(window, 'prompt');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><GlossaryPanel projectId="project"/></QueryClientProvider>);

    fireEvent.click(await screen.findByRole('button', { name: '新建' }));
    fireEvent.change(screen.getByRole('textbox', { name: '术语表名称' }), { target: { value: '产品术语' } });
    fireEvent.click(screen.getByRole('button', { name: '创建术语表' }));

    await waitFor(() => expect(api.createGlossary).toHaveBeenCalledWith('project', '产品术语'));
    expect(prompt).not.toHaveBeenCalled();
  });
});
