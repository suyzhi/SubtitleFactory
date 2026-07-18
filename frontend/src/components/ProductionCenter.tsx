import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../api/backend';

interface Props {
  workflow: { model: string; language: string; target_language: string; runtime?: string };
  onClose: () => void;
  onProjectsCreated: () => void;
  onShowTasks: () => void;
}

export default function ProductionCenter({ workflow, onClose, onProjectsCreated, onShowTasks }: Props) {
  const client = useQueryClient();
  const folders = useQuery({ queryKey: ['watch-folders'], queryFn: api.getWatchFolders });
  const templates = useQuery({ queryKey: ['style-templates'], queryFn: api.getStyleTemplates });
  const [styleTemplateId, setStyleTemplateId] = useState('');
  const [message, setMessage] = useState('监听仅在字幕工厂运行时扫描；文件连续两次大小不变后才会入队。');
  const [busy, setBusy] = useState(false);
  const configuration = { ...workflow, autostart: true, style_template_id: styleTemplateId || null };

  async function choosePaths(directory: boolean, multiple = false) {
    const { open } = await import('@tauri-apps/plugin-dialog');
    return open({ directory, multiple, title: directory ? '选择监听文件夹' : '选择要批量处理的视频', filters: directory ? undefined : [{ name: '视频', extensions: ['mp4', 'mkv', 'mov', 'webm', 'avi'] }] });
  }

  async function launchBatch(paths: string[], marks: api.WatchReadyFile[] = []) {
    if (!paths.length) return;
    setBusy(true);
    try {
      const batch = await api.createBatch(paths, configuration, `批量任务 ${new Date().toLocaleString()}`);
      let styleWarning = '';
      const template = templates.data?.templates.find(item => item.id === styleTemplateId);
      if (template) {
        const settings = { ...template.settings };
        const font = String(settings.fontFamily || '');
        if (font && document.fonts && !document.fonts.check(`16px ${font}`)) {
          settings.fontFamily = 'Inter, "PingFang SC", "Helvetica Neue", sans-serif';
          styleWarning = `；模板字体“${font}”缺失，已使用系统字体`;
        }
        await Promise.all(batch.items.map(item => api.saveProjectStyle(item.project_id, settings)));
      }
      await Promise.all(batch.items.map(item => api.startWorkflow(item.project_id, { model: workflow.model, language: workflow.language, runtime: workflow.runtime })));
      await Promise.all(marks.map((mark, index) => api.markWatchImported(mark.watch_folder_id, mark.path, batch.items[index].project_id)));
      setMessage(`已导入并排队 ${batch.items.length} 个项目${styleWarning}`); onProjectsCreated(); onShowTasks();
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  }

  const addFolder = useMutation({
    mutationFn: async () => { const selected = await choosePaths(true); if (typeof selected !== 'string') throw new Error('未选择文件夹'); return api.addWatchFolder(selected, configuration); },
    onSuccess: () => { setMessage('监听文件夹已添加'); void client.invalidateQueries({ queryKey: ['watch-folders'] }); },
    onError: error => setMessage(error.message),
  });

  async function batchPick() {
    try {
      const selected = await choosePaths(false, true);
      const paths = Array.isArray(selected) ? selected : typeof selected === 'string' ? [selected] : [];
      await launchBatch(paths);
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }

  async function scan() {
    setBusy(true);
    try {
      const result = await api.scanWatchFolders();
      if (!result.ready.length) setMessage('扫描完成，暂无已稳定且未导入的新视频；首次发现的文件会在下次扫描确认。');
      else await launchBatch(result.ready.map(item => item.path), result.ready);
      void client.invalidateQueries({ queryKey: ['watch-folders'] });
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); setBusy(false); }
  }

  async function pauseAll() {
    const tasks = (await api.getGlobalTasks(500)).tasks.filter(task => ['pending', 'running'].includes(task.status));
    await Promise.allSettled(tasks.map(task => api.pauseTask(task.id))); setMessage(`已请求暂停 ${tasks.length} 个任务`); onShowTasks();
  }

  async function cancelPending() {
    const tasks = (await api.getGlobalTasks(500)).tasks.filter(task => task.status === 'pending');
    await Promise.allSettled(tasks.map(task => api.cancelTask(task.id))); setMessage(`已取消 ${tasks.length} 个未开始任务`); onShowTasks();
  }

  return <div className="production-overlay" role="dialog" aria-modal="true" aria-labelledby="production-title" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="production-center">
      <header><div><small>批量生产</small><h2 id="production-title">任务与监听文件夹</h2><p>统一使用当前模型、语言和运行设备；进度集中显示在全局任务中心。</p></div><button aria-label="关闭" onClick={onClose}>×</button></header>
      <label className="production-template">批量样式模板<select value={styleTemplateId} onChange={event => setStyleTemplateId(event.target.value)}><option value="">不应用模板</option>{templates.data?.templates.map(item => <option value={item.id} key={item.id}>{item.builtin ? '系统 · ' : ''}{item.name}</option>)}</select></label><div className="production-actions"><button className="button primary" disabled={busy || !workflow.runtime} onClick={() => void batchPick()}>选择多个视频</button><button className="button" disabled={busy || addFolder.isPending} onClick={() => addFolder.mutate()}>添加监听文件夹</button><button className="button" disabled={busy || !folders.data?.watch_folders.length} onClick={() => void scan()}>立即扫描</button></div>
      {!workflow.runtime && <p className="production-warning">请先在处理设置中为当前转写模型选择运行设备。</p>}
      <div className="watch-folder-list">{folders.data?.watch_folders.map(folder => <div key={folder.id}><span><strong>{folder.path.split('/').pop()}</strong><small>{folder.path}</small></span><em>{folder.last_scan_at ? `上次扫描 ${folder.last_scan_at}` : '等待首次扫描'}</em><button onClick={() => void api.removeWatchFolder(folder.id).then(() => client.invalidateQueries({ queryKey: ['watch-folders'] }))}>移除</button></div>)}{!folders.data?.watch_folders.length && <p>尚未添加监听文件夹。</p>}</div>
      <div className="production-queue-actions"><button onClick={() => void pauseAll()}>暂停全部运行任务</button><button onClick={() => void cancelPending()}>取消未开始任务</button><button onClick={onShowTasks}>打开任务中心</button></div>
      <footer role="status">{busy && <i/>}<span>{message}</span></footer>
    </section>
  </div>;
}
