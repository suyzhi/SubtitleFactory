import { useCallback, useEffect, useState } from 'react';
import * as api from '../api/backend';
import type { Project } from '../types';
import ClipWorkbench from './content/ClipWorkbench';
import ContentAuthorization from './content/ContentAuthorization';
import { errorMessage, type ContentTab } from './content/helpers';
import PublicationPacks from './content/PublicationPacks';

interface Props {
  project: Project;
  projectRevision: number;
  hasSegments: boolean;
  onPreview: (start: number, end: number) => void;
  onMessage: (message: string) => void;
}

export default function ContentCenter({
  project,
  projectRevision,
  hasSegments,
  onPreview,
  onMessage,
}: Props) {
  const [tab, setTab] = useState<ContentTab>('packs');
  const [granted, setGranted] = useState(false);
  const [authorizationBusy, setAuthorizationBusy] = useState(false);

  const loadAuthorization = useCallback(async () => {
    const result = await api.getCloudAuthorizations();
    setGranted(Boolean(result.authorizations.find(item => item.capability === 'content')?.granted));
  }, []);

  useEffect(() => {
    void loadAuthorization().catch(reason => onMessage(errorMessage(reason)));
  }, [loadAuthorization, onMessage]);

  const requireAuthorization = useCallback(async () => {
    if (granted) return true;
    const accepted = window.confirm(
      '内容生成与短片推荐会把当前项目标题、所选字幕文字、时间码和说话人标签发送给你配置的内容生成服务商。\n\n不会上传视频、音频、本机路径或其他项目内容。是否授权？',
    );
    if (!accepted) return false;
    setAuthorizationBusy(true);
    try {
      const providers = await api.getAIProviders();
      await api.setCloudAuthorization('content', true, providers.assignments.content_provider_id);
      setGranted(true);
      onMessage('内容生成云端授权已保存，可随时在此撤销');
      return true;
    } catch (reason) {
      onMessage(errorMessage(reason));
      return false;
    } finally {
      setAuthorizationBusy(false);
    }
  }, [granted, onMessage]);

  const revoke = async () => {
    try {
      await api.setCloudAuthorization('content', false);
      setGranted(false);
      onMessage('内容生成云端授权已撤销；已有内容仍可查看、编辑和导出');
    } catch (reason) {
      onMessage(errorMessage(reason));
    }
  };

  return (
    <section className="task-page content-center-page" data-project-revision={projectRevision}>
      <header className="content-center-header">
        <div>
          <small>内容再生产</small>
          <h2>从字幕到可发布内容</h2>
          <p>发布包与短片定义保存在项目中，视频输出按需在本机生成。</p>
        </div>
        <nav role="tablist" aria-label="内容工作区">
          <button
            role="tab"
            aria-selected={tab === 'packs'}
            className={tab === 'packs' ? 'active' : ''}
            onClick={() => setTab('packs')}
          >
            发布包
          </button>
          <button
            role="tab"
            aria-selected={tab === 'clips'}
            className={tab === 'clips' ? 'active' : ''}
            onClick={() => setTab('clips')}
          >
            短视频
          </button>
        </nav>
        {granted && (
          <button className="button secondary" onClick={() => void revoke()}>
            撤销云端授权
          </button>
        )}
      </header>
      {!hasSegments && (
        <div className="content-source-required" role="status">
          <span>尚无字幕</span>
          <div>
            <strong>先生成或导入字幕</strong>
            <p>完成“处理 → 转写”，或在“字幕”工作区导入字幕后，再创建发布包和短视频候选。</p>
          </div>
        </div>
      )}
      {hasSegments && (
        <ContentAuthorization
          granted={granted}
          busy={authorizationBusy}
          onGrant={() => void requireAuthorization()}
        />
      )}
      {tab === 'packs' ? (
        <PublicationPacks
          project={project}
          hasSegments={hasSegments}
          granted={granted}
          onRequireAuthorization={requireAuthorization}
          onMessage={onMessage}
        />
      ) : (
        <ClipWorkbench
          project={project}
          hasSegments={hasSegments}
          granted={granted}
          onRequireAuthorization={requireAuthorization}
          onPreview={onPreview}
          onMessage={onMessage}
        />
      )}
    </section>
  );
}
