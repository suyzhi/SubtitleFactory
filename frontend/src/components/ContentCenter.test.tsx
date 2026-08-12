// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest';
import { StrictMode } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ClipSet, ContentPack, Project } from '../types';
import * as api from '../api/backend';
import ContentCenter from './ContentCenter';

vi.mock('../api/backend', () => ({
  getCloudAuthorizations: vi.fn(),
  setCloudAuthorization: vi.fn(),
  getAIProviders: vi.fn(),
  getContentPacks: vi.fn(),
  getContentPack: vi.fn(),
  createContentPack: vi.fn(),
  updateContentPack: vi.fn(),
  updateContentSection: vi.fn(),
  regenerateContentSection: vi.fn(),
  deleteContentPack: vi.fn(),
  exportContentPack: vi.fn(),
  downloadContentPack: vi.fn(),
  getClipSets: vi.fn(),
  getClipSet: vi.fn(),
  createClipSet: vi.fn(),
  updateClipCandidate: vi.fn(),
  updateClipLayout: vi.fn(),
  renderClips: vi.fn(),
  getTaskStatus: vi.fn(),
  downloadClipRender: vi.fn(),
  deleteClipRender: vi.fn(),
}));

const project: Project = {
  id: 'project-1',
  title: '内容测试',
  source_type: 'local',
  source_url: null,
  video_path: '/tmp/video.mp4',
  thumbnail_url: null,
  group_name: null,
  audio_path: null,
  language: 'zh',
  target_language: 'en',
  created_at: '2026-07-30 10:00:00',
  updated_at: '2026-07-30 10:00:00',
  segments_count: 10,
  edit_revision: 3,
  media_mode: 'local',
  youtube_video_id: null,
  video_available: true,
  audio_available: true,
};

const pack: ContentPack = {
  id: 'pack-1',
  project_id: project.id,
  name: '完整发布包',
  input_mode: 'original',
  output_language: 'zh',
  source_revision: 2,
  current_project_revision: 3,
  source_fingerprint: 'source',
  provider_id: 'deepseek',
  model: 'content-model',
  status: 'ready',
  revision: 4,
  stale: true,
  created_at: 'now',
  updated_at: 'now',
  sections: [{
    id: 'section-summary',
    pack_id: 'pack-1',
    kind: 'summary',
    title: '摘要与要点',
    content: { overview: '原摘要', key_points: ['要点一'] },
    status: 'ready',
    sort_order: 1,
    revision: 2,
    generated_at: 'now',
    updated_at: 'now',
  }],
};

const clipSet: ClipSet = {
  id: 'set-1',
  project_id: project.id,
  name: '短片候选',
  source_revision: 3,
  current_project_revision: 3,
  source_fingerprint: 'source',
  provider_id: 'deepseek',
  model: 'content-model',
  desired_count: 3,
  min_duration: 30,
  max_duration: 90,
  status: 'ready',
  stale: false,
  candidate_count: 1,
  created_at: 'now',
  updated_at: 'now',
  candidates: [{
    id: 'candidate-1',
    clip_set_id: 'set-1',
    title: '完整观点',
    hook: '一个明确开场',
    reason: '主题完整',
    score: 92,
    start: 10,
    end: 55,
    start_segment_index: 2,
    end_segment_index: 8,
    selected: true,
    revision: 1,
    source_confirmed_revision: 3,
    stale: false,
    layouts: [
      { candidate_id: 'candidate-1', aspect_ratio: '9:16', enabled: true, composition: 'blur', focal_x: .5, focal_y: .5, subtitle_mode: 'original', style: {}, revision: 0, updated_at: 'now' },
      { candidate_id: 'candidate-1', aspect_ratio: '1:1', enabled: false, composition: 'blur', focal_x: .5, focal_y: .5, subtitle_mode: 'original', style: {}, revision: 0, updated_at: 'now' },
      { candidate_id: 'candidate-1', aspect_ratio: '16:9', enabled: false, composition: 'crop', focal_x: .5, focal_y: .5, subtitle_mode: 'original', style: {}, revision: 0, updated_at: 'now' },
    ],
    renders: [],
  }],
};

describe('ContentCenter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getCloudAuthorizations).mockResolvedValue({
      authorizations: [{
        capability: 'content', provider_id: 'deepseek', granted: true,
        disclosure_version: '1.0', granted_at: 'now', revoked_at: null,
      }],
    });
    vi.mocked(api.getContentPacks).mockResolvedValue({ packs: [pack] });
    vi.mocked(api.getContentPack).mockResolvedValue(pack);
    vi.mocked(api.getClipSets).mockResolvedValue({ clip_sets: [clipSet] });
    vi.mocked(api.getClipSet).mockResolvedValue(clipSet);
  });

  it('preserves stale content and autosaves a section with its expected revision', async () => {
    const updated = {
      ...pack,
      revision: 5,
      sections: [{
        ...pack.sections![0],
        revision: 3,
        content: { overview: '人工修改后的摘要', key_points: ['要点一'] },
      }],
    };
    vi.mocked(api.updateContentSection).mockResolvedValue(updated);
    render(<StrictMode><ContentCenter project={project} projectRevision={3} hasSegments onPreview={() => undefined} onMessage={() => undefined}/></StrictMode>);

    expect((await screen.findAllByText('源字幕已更新')).length).toBeGreaterThan(0);
    const overview = await screen.findByLabelText('内容摘要');
    fireEvent.change(overview, { target: { value: '人工修改后的摘要' } });
    await waitFor(() => expect(api.updateContentSection).toHaveBeenCalledWith(
      'pack-1', 'summary',
      { overview: '人工修改后的摘要', key_points: ['要点一'] },
      2,
    ), { timeout: 1800 });
    expect(await screen.findByText('已保存')).toBeInTheDocument();
  });

  it('saves a second aspect ratio before submitting a multi-output render', async () => {
    const nextSet: ClipSet = {
      ...clipSet,
      candidates: clipSet.candidates!.map(candidate => ({
        ...candidate,
        layouts: candidate.layouts.map(layout =>
          layout.aspect_ratio === '1:1' ? { ...layout, enabled: true, revision: 1 } : layout),
      })),
    };
    vi.mocked(api.updateClipLayout).mockResolvedValue(nextSet);
    vi.mocked(api.renderClips).mockResolvedValue({
      task_id: null, render_ids: ['render-vertical', 'render-square'], reused: true,
    });
    const user = userEvent.setup();
    render(<ContentCenter project={project} projectRevision={3} hasSegments onPreview={() => undefined} onMessage={() => undefined}/>);

    await user.click(await screen.findByRole('tab', { name: '短视频' }));
    const squareHeading = await screen.findByText('1:1');
    const squareCard = squareHeading.closest('section')!;
    await user.click(within(squareCard).getByRole('checkbox'));
    await user.click(within(squareCard).getByRole('button', { name: '保存此布局' }));
    await waitFor(() => expect(api.updateClipLayout).toHaveBeenCalledWith(
      'candidate-1', '1:1', expect.objectContaining({ enabled: true, expected_revision: 0 }),
    ));
    await user.click(screen.getByRole('button', { name: '渲染所选版本' }));
    await waitFor(() => expect(api.renderClips).toHaveBeenCalledWith(project.id, {
      items: [
        { candidate_id: 'candidate-1', aspect_ratio: '9:16' },
        { candidate_id: 'candidate-1', aspect_ratio: '1:1' },
      ],
      confirm_stale: false,
    }));
  });

  it('does not reconfirm when the selected candidate already confirmed the current revision', async () => {
    const mixedSet: ClipSet = {
      ...clipSet,
      stale: true,
      source_revision: 2,
      candidates: clipSet.candidates!.map(candidate => ({ ...candidate, stale: false })),
    };
    vi.mocked(api.getClipSets).mockResolvedValue({ clip_sets: [mixedSet] });
    vi.mocked(api.getClipSet).mockResolvedValue(mixedSet);
    vi.mocked(api.renderClips).mockResolvedValue({
      task_id: null, render_ids: ['render-confirmed'], reused: true,
    });
    const confirm = vi.spyOn(window, 'confirm');
    const user = userEvent.setup();
    render(<ContentCenter project={project} projectRevision={3} hasSegments onPreview={() => undefined} onMessage={() => undefined}/>);

    await user.click(await screen.findByRole('tab', { name: '短视频' }));
    await user.click(screen.getByRole('button', { name: '渲染所选版本' }));
    await waitFor(() => expect(api.renderClips).toHaveBeenCalledWith(project.id, {
      items: [{ candidate_id: 'candidate-1', aspect_ratio: '9:16' }],
      confirm_stale: false,
    }));
    expect(confirm).not.toHaveBeenCalled();
  });

  it('requires subtitles before authorizing or generating content', async () => {
    vi.mocked(api.getContentPacks).mockResolvedValue({ packs: [] });
    vi.mocked(api.getClipSets).mockResolvedValue({ clip_sets: [] });
    const user = userEvent.setup();
    render(<ContentCenter project={{ ...project, segments_count: 0 }} projectRevision={0} hasSegments={false} onPreview={() => undefined} onMessage={() => undefined}/>);

    expect(await screen.findByRole('status')).toHaveTextContent('先生成或导入字幕');
    expect(screen.queryByText('云端内容生成尚未授权')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成完整发布包' })).toBeDisabled();

    await user.click(screen.getByRole('tab', { name: '短视频' }));
    expect(await screen.findByRole('button', { name: '生成候选' })).toBeDisabled();
    expect(api.setCloudAuthorization).not.toHaveBeenCalled();
    expect(api.createContentPack).not.toHaveBeenCalled();
    expect(api.createClipSet).not.toHaveBeenCalled();
  });
});
