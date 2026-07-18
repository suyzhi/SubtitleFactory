import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../api/backend';
import type { EditorOperationResponse, QualityIssue, SubtitleSegment } from '../types';

interface Props {
  projectId: string;
  segments: SubtitleSegment[];
  onSeek: (time: number) => void;
  revision: number;
  onEditorResult: (result: EditorOperationResponse) => void;
}

const LABELS: Record<string, string> = {
  invalid_time: '非法时间', overlap: '时间重叠', large_gap: '间隔异常', short_duration: '时长过短',
  long_duration: '时长过长', reading_speed: '阅读速度', line_length: '单行过长', line_count: '行数过多',
  empty_text: '空字幕', duplicate: '重复字幕', missing_translation: '缺少译文', number_mismatch: '数字不一致', glossary: '术语不一致',
  punctuation: '标点不一致', proper_noun: '专有名词', rapid_speaker_switch: '说话人快速切换',
};

export default function QualityPanel({ projectId, segments, onSeek, revision, onEditorResult }: Props) {
  const [filter, setFilter] = useState<'all' | QualityIssue['severity']>('all');
  const [rules, setRules] = useState({ min_duration: .5, max_duration: 8, max_cps: 20, max_line_length: 42, max_lines: 2, large_gap: 8 });
  const [showRules, setShowRules] = useState(false);
  const [fixPreview, setFixPreview] = useState<{issue: QualityIssue; before: string; after: string; ai?: boolean} | null>(null);
  const queryClient = useQueryClient();
  const queryKey = ['quality-issues', projectId] as const;
  const query = useQuery({ queryKey, queryFn: () => api.getProjectQualityIssues(projectId) });
  const authorizations = useQuery({ queryKey: ['cloud-authorizations'], queryFn: api.getCloudAuthorizations });
  const scanMutation = useMutation({
    mutationFn: () => api.scanProjectQuality(projectId, rules),
    onSuccess: result => queryClient.setQueryData(queryKey, result),
  });
  const markMutation = useMutation({
    mutationFn: ({ issue, status }: {issue: QualityIssue; status: QualityIssue['status']}) => api.updateQualityIssue(projectId, issue.id, status),
    onMutate: async ({ issue }) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<{issues: QualityIssue[]; total: number}>(queryKey);
      queryClient.setQueryData(queryKey, previous ? { issues: previous.issues.filter(item => item.id !== issue.id), total: Math.max(0, previous.total - 1) } : previous);
      return { previous };
    },
    onError: (_error, _variables, context) => { if (context?.previous) queryClient.setQueryData(queryKey, context.previous); },
  });
  const previewFix = async (issue: QualityIssue) => {
    const result = await api.fixQualityIssue(projectId, issue.id, revision, false);
    setFixPreview({ issue, ...result.preview });
  };
  const applyFix = async () => {
    if (!fixPreview) return;
    const result = fixPreview.ai ? await api.aiFixQualityIssue(projectId, fixPreview.issue.id, revision, true) : await api.fixQualityIssue(projectId, fixPreview.issue.id, revision, true);
    if (result.editor) onEditorResult(result.editor);
    setFixPreview(null); await queryClient.invalidateQueries({ queryKey });
  };
  const previewAIFix = async (issue: QualityIssue) => {
    const result = await api.aiFixQualityIssue(projectId, issue.id, revision, false);
    const before = [result.preview.before.clean_text, result.preview.before.translated_text].filter(Boolean).join('\n');
    const after = [result.preview.after.clean_text, result.preview.after.translated_text].filter(Boolean).join('\n');
    setFixPreview({ issue, before, after, ai: true });
  };
  const qualityCloudGranted = Boolean(authorizations.data?.authorizations.find(item => item.capability === 'quality')?.granted);
  const issues = useMemo(() => query.data?.issues || [], [query.data?.issues]);
  const loading = query.isLoading || scanMutation.isPending;
  const visible = useMemo(() => issues.filter(issue => filter === 'all' || issue.severity === filter), [filter, issues]);
  const locate = (issue: QualityIssue) => {
    const segment = segments.find(item => item.index === issue.segment_index);
    if (segment) onSeek(segment.start);
  };
  return <section className="quality-panel" aria-label="字幕质量检查">
    <header>
      <div><small>本地规则引擎</small><h2>字幕质量检查</h2><p>检查时间轴、可读性、译文数字和项目术语；不会上传字幕。</p></div>
      <div><button className="button" aria-expanded={showRules} onClick={() => setShowRules(value => !value)}>规则设置</button><button className="button primary" disabled={loading || !segments.length} onClick={() => scanMutation.mutate()}>{loading ? '正在检查…' : '重新检查'}</button></div>
    </header>
    {showRules && <div className="quality-rule-grid">{([['min_duration','最短时长'],['max_duration','最长时长'],['max_cps','最大字/秒'],['max_line_length','单行字数'],['max_lines','最大行数'],['large_gap','异常间隔']] as const).map(([key,label]) => <label key={key}>{label}<input type="number" min="0" step={key.includes('duration') || key === 'large_gap' ? .1 : 1} value={rules[key]} onChange={event => setRules(current => ({ ...current, [key]: Number(event.target.value) }))}/></label>)}</div>}
    <div className="quality-summary">
      {(['error', 'warning', 'info'] as const).map(level => <button key={level} className={filter === level ? 'active' : ''} onClick={() => setFilter(filter === level ? 'all' : level)}><strong>{issues.filter(item => item.severity === level).length}</strong><span>{level === 'error' ? '错误' : level === 'warning' ? '警告' : '建议'}</span></button>)}
    </div>
    <div className="quality-list">
      {fixPreview && <div className="quality-fix-preview" role="dialog" aria-label="质检修复预览"><div><strong>修复预览</strong><del>{fixPreview.before}</del><ins>{fixPreview.after}</ins></div><button onClick={() => setFixPreview(null)}>取消</button><button className="primary" onClick={() => void applyFix()}>应用并可撤销</button></div>}
      {!loading && !visible.length && <div className="quality-empty"><strong>没有发现待处理问题</strong><span>编辑字幕后可以重新运行检查。</span></div>}
      {visible.map(issue => <article key={issue.id} className={`quality-issue ${issue.severity}`}>
        <i aria-hidden="true">{issue.severity === 'error' ? '!' : issue.severity === 'warning' ? '△' : 'i'}</i>
        <div><small>{LABELS[issue.rule_id] || issue.rule_id}{issue.segment_index ? ` · 第 ${issue.segment_index} 条` : ''}</small><strong>{issue.message}</strong><p>{issue.suggestion}</p></div>
        <div className="quality-actions">{issue.segment_index && <button onClick={() => locate(issue)}>定位</button>}{['line_length', 'number_mismatch', 'duplicate'].includes(issue.rule_id) && <button onClick={() => void previewFix(issue)}>预览修复</button>}{qualityCloudGranted && issue.segment_index && <button onClick={() => void previewAIFix(issue)}>AI 预览</button>}<button onClick={() => markMutation.mutate({ issue, status: 'resolved' })}>标为已处理</button><button onClick={() => markMutation.mutate({ issue, status: 'ignored' })}>忽略</button></div>
      </article>)}
    </div>
  </section>;
}
