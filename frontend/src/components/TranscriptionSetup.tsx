import { useState } from 'react';
import type { TranscriptionModelStatus } from '../api/backend';
import AppSelect from './AppSelect';
import LanguagePicker from './LanguagePicker';

export default function TranscriptionSetup({ models, model, language, recommended, runtime, onModel, onLanguage, onRuntime }: {
  models: TranscriptionModelStatus[]; model: string; language: string; recommended?: string;
  runtime: string; onModel: (value:string) => void; onLanguage: (value:string) => void; onRuntime: (model:string,runtime:string) => void;
}) {
  const [more, setMore] = useState(false);
  const resolved = model === 'auto' ? recommended || '' : model;
  const selected = models.find(item => item.id === resolved);
  const installed = (item:TranscriptionModelStatus) => item.ready || item.runtimes?.some(option => option.available && option.model_ready);
  const options = models.filter(item => more || item.id === resolved || (installed(item) && (language === 'auto' || item.languages.includes(language) || item.languages.includes('all') || item.languages.includes('multilingual'))));
  return <section className="transcription-setup">
    <label>源语言<LanguagePicker value={language} onChange={onLanguage}/></label>
    <label>转写模型<AppSelect value={model} onChange={onModel} label="转写模型" searchable options={[{value:'auto',label:'自动选择已安装模型',description:'按语言推荐，不自动切换运行设备'}, ...options.map(item => ({value:item.id,label:item.name,description:`${installed(item) ? '已安装' : '需准备'} · ${item.language_description || item.languages.join('、')}`}))]}/></label>
    <button className="button secondary" aria-expanded={more} onClick={() => setMore(value => !value)}>{more ? '只看已安装模型' : '更多模型'}</button>
    <fieldset><legend>运行设备</legend><div className="runtime-choice-grid">{selected?.runtimes?.map(option => <button key={option.id} type="button" className={runtime === option.id ? 'selected' : ''} aria-pressed={runtime === option.id} disabled={!option.available} onClick={() => onRuntime(resolved,option.id)}><span><strong>{option.name}</strong><small>{option.engine}</small><small>{option.model_ready ? '模型已就绪' : option.download_required ? '首次使用需下载模型' : option.reason}</small></span></button>)}</div></fieldset>
    {!runtime && <p>请明确选择运行设备后开始。</p>}
    {selected?.runtime_error && <p role="alert">{selected.runtime_error}</p>}
  </section>;
}
