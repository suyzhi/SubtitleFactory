import AppSelect from './AppSelect';
import { LANGUAGES, TARGET_LANGUAGES, languageLabel } from '../languages';
interface Props { value:string; onChange:(value:string)=>void; mode?:'source'|'target'; allowNone?:boolean; allowCustom?:boolean; disabled?:boolean; label?:string; }
export default function LanguagePicker({value,onChange,mode='source',allowNone=false,allowCustom=false,disabled,label}:Props){
  const source=mode==='source'?LANGUAGES:TARGET_LANGUAGES;
  const options=[...(allowNone?[{value:'none',label:languageLabel('none'),description:'不执行翻译'}]:[]),...source.map(item=>({value:item.code,label:languageLabel(item.code),description:item.code}))];
  return <AppSelect className="language-picker" value={value} onChange={onChange} options={options} label={label||(mode==='source'?'源语言':'目标语言')} placeholder="搜索语言" searchable allowCustom={allowCustom} disabled={disabled}/>;
}
