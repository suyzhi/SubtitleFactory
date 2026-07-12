import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { LANGUAGES, TARGET_LANGUAGES, languageLabel } from '../languages';

interface Props { value: string; onChange: (value: string) => void; mode?: 'source'|'target'; allowNone?: boolean; allowCustom?: boolean; disabled?: boolean; label?: string; }

export default function LanguagePicker({ value, onChange, mode='source', allowNone=false, allowCustom=false, disabled, label }: Props) {
  const listId=useId(); const root=useRef<HTMLDivElement>(null);
  const base=mode==='source'?LANGUAGES:TARGET_LANGUAGES;
  const options=useMemo(()=>[...(allowNone?[{code:'none',name:'不翻译',nativeName:'None'}]:[]),...base],[allowNone,base]);
  const known=options.some(item=>item.code===value);
  const [query,setQuery]=useState(known?languageLabel(value):value); const [open,setOpen]=useState(false); const [active,setActive]=useState(0);
  const matches=useMemo(()=>options.filter(item=>`${item.code} ${languageLabel(item.code)}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())).slice(0,50),[options,query]);
  useEffect(()=>{ if(!open) setQuery(known?languageLabel(value):value); },[open,known,value]);
  const choose=(code:string)=>{ onChange(code); setQuery(options.some(item=>item.code===code)?languageLabel(code):code); setOpen(false); };
  const canAdd=allowCustom&&query.trim()&&!options.some(item=>item.code.toLocaleLowerCase()===query.trim().toLocaleLowerCase()||languageLabel(item.code)===query.trim());
  return <div className="language-picker app-combobox" ref={root}>
    <input role="combobox" aria-expanded={open} aria-controls={listId} aria-activedescendant={open?`${listId}-${active}`:undefined} value={query} disabled={disabled}
      aria-label={label||(mode==='source'?'源语言':'目标语言')} placeholder={allowCustom?'搜索或输入新语言':'搜索语言'}
      onFocus={event=>{setOpen(true);event.currentTarget.select();}} onChange={event=>{setQuery(event.target.value);setOpen(true);setActive(0);}}
      onKeyDown={event=>{ if(event.key==='ArrowDown'){event.preventDefault();setOpen(true);setActive(i=>Math.min(i+1,matches.length+(canAdd?1:0)-1));} if(event.key==='ArrowUp'){event.preventDefault();setActive(i=>Math.max(0,i-1));} if(event.key==='Enter'&&open){event.preventDefault();const item=matches[active]; if(item) choose(item.code); else if(canAdd) choose(query.trim());} if(event.key==='Escape'){event.preventDefault();setOpen(false);} if(event.key==='Tab')setOpen(false); }}/>
    {open&&<div id={listId} className="combobox-popover" role="listbox" onMouseDown={event=>event.preventDefault()}>
      {matches.map((item,index)=><button type="button" id={`${listId}-${index}`} role="option" aria-selected={index===active} className={index===active?'active':''} key={item.code} onMouseEnter={()=>setActive(index)} onClick={()=>choose(item.code)}><span>{languageLabel(item.code)}</span><small>{item.code}</small></button>)}
      {canAdd&&<button type="button" role="option" className={active===matches.length?'active':''} onMouseEnter={()=>setActive(matches.length)} onClick={()=>choose(query.trim())}><span>添加并选择“{query.trim()}”</span><small>自定义语言</small></button>}
      {!matches.length&&!canAdd&&<p>没有匹配语言</p>}
    </div>}
  </div>;
}
