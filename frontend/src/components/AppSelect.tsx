import { createPortal } from 'react-dom';
import { createContext, useContext, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';

export const AppSelectThemeContext=createContext<'light'|'dark'|undefined>(undefined);

export interface AppSelectOption { value:string; label:string; description?:string; disabled?:boolean; keywords?:string; }
interface Props { value:string; onChange:(value:string)=>void; options:readonly AppSelectOption[]; label:string; placeholder?:string; searchable?:boolean; allowCustom?:boolean; disabled?:boolean; className?:string; tone?:'player'; }

export default function AppSelect({value,onChange,options,label,placeholder='请选择',searchable=false,allowCustom=false,disabled,className='',tone}:Props){
  const inheritedTheme=useContext(AppSelectThemeContext);
  const restoreFocus=useRef(false);
  const shellRef=useRef<HTMLDivElement>(null);
  const controlRef=useRef<HTMLInputElement & HTMLButtonElement>(null);
  const [pressed,setPressed]=useState(false);
  const id=useId(); const rootRef=useRef<HTMLDivElement>(null); const popoverRef=useRef<HTMLDivElement>(null);
  const [open,setOpen]=useState(false); const [query,setQuery]=useState(''); const [active,setActive]=useState(0); const [position,setPosition]=useState({left:0,top:0,width:240,maxHeight:280,above:false,height:36});
  useEffect(()=>{
    const labelElement=rootRef.current?.closest('label');
    const stopLabelActivation=(event:MouseEvent)=>{
      if(!rootRef.current?.contains(event.target as Node))event.preventDefault();
    };
    labelElement?.addEventListener('click',stopLabelActivation);
    return()=>labelElement?.removeEventListener('click',stopLabelActivation);
  },[]);
  useLayoutEffect(()=>{if(open||restoreFocus.current)controlRef.current?.focus({preventScroll:true});restoreFocus.current=false;},[open]);
  useEffect(()=>{if(!open)setPressed(false);},[open]);
  const selected=options.find(item=>item.value===value);
  const filtered=useMemo(()=>{const needle=query.trim().toLocaleLowerCase();return options.filter(item=>!needle||`${item.label} ${item.value} ${item.keywords||''}`.toLocaleLowerCase().includes(needle));},[options,query]);
  const customValue=query.trim(); const canAdd=Boolean(allowCustom&&customValue&&!options.some(item=>item.value.toLocaleLowerCase()===customValue.toLocaleLowerCase()||item.label.toLocaleLowerCase()===customValue.toLocaleLowerCase()));
  const total=filtered.length+(canAdd?1:0);
  const activeOptionId=open&&total>0?`${id}-option-${active}`:undefined;
  const reposition=useCallback(()=>{const rect=rootRef.current?.getBoundingClientRect();if(!rect)return;const below=window.innerHeight-rect.bottom-12;const above=rect.top-12;const useAbove=below<190&&above>below;const width=Math.min(window.innerWidth-16,rect.width);setPosition({left:Math.max(8,Math.min(rect.left,window.innerWidth-width-8)),top:useAbove?window.innerHeight-rect.bottom:rect.top,width,height:rect.height,maxHeight:Math.max(120,Math.min(320,(useAbove?above:below)-8)),above:useAbove});},[]);
  useLayoutEffect(()=>{if(open)reposition();},[open,reposition]);
  useEffect(()=>{if(!open)return;const outside=(event:PointerEvent)=>{const target=event.target as Node;if(!rootRef.current?.contains(target)&&!shellRef.current?.contains(target)){setQuery('');setOpen(false);}};window.addEventListener('pointerdown',outside,true);window.addEventListener('resize',reposition);window.addEventListener('scroll',reposition,true);return()=>{window.removeEventListener('pointerdown',outside,true);window.removeEventListener('resize',reposition);window.removeEventListener('scroll',reposition,true);};},[open,reposition]);
  useEffect(()=>{if(!open)return;popoverRef.current?.querySelector<HTMLElement>(`[data-option-index="${active}"]`)?.scrollIntoView({block:'nearest'});},[active,open]);
  const choose=(next:string)=>{onChange(next);setQuery('');restoreFocus.current=true;setOpen(false);};
  const openList=()=>{if(disabled)return;setOpen(true);setActive(Math.max(0,filtered.findIndex(item=>item.value===value)));};
  const moveFocus=(backward:boolean)=>{requestAnimationFrame(()=>{const scope=rootRef.current?.closest<HTMLElement>('[role="dialog"],.settings-center')||document;const items=Array.from(scope.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')).filter(item=>!item.closest('.app-select-popover')&&item.getClientRects().length>0);const current=controlRef.current;const index=current?items.indexOf(current):-1;items[(index+(backward?-1:1)+items.length)%items.length]?.focus();});};
  const onKeyDown=(event:React.KeyboardEvent)=>{if(event.key==='ArrowDown'){event.preventDefault();if(!open)openList();else setActive(i=>Math.min(Math.max(0,total-1),i+1));}else if(event.key==='ArrowUp'){event.preventDefault();if(!open)openList();else setActive(i=>Math.max(0,i-1));}else if(event.key==='Home'&&open){event.preventDefault();setActive(0);}else if(event.key==='End'&&open){event.preventDefault();setActive(Math.max(0,total-1));}else if(event.key==='Enter'&&open){event.preventDefault();const item=filtered[active];if(item&&!item.disabled)choose(item.value);else if(canAdd&&active===filtered.length)choose(customValue);}else if(event.key==='Escape'&&open){event.preventDefault();event.stopPropagation();setQuery('');restoreFocus.current=true;setOpen(false);}else if(event.key==='Tab'&&open){event.preventDefault();event.stopPropagation();setQuery('');setOpen(false);moveFocus(event.shiftKey);}};
  const portalHost=rootRef.current?.closest<HTMLElement>('.pro-app')||document.body;
  const themeHost=rootRef.current?.closest<HTMLElement>('.theme-dark,.theme-light')||portalHost;
  const playerPopover=tone==='player'||Boolean(rootRef.current?.closest('.pro-player'));
  const themeClass=playerPopover?'theme-dark':inheritedTheme?`theme-${inheritedTheme}`:themeHost.classList.contains('theme-dark')?'theme-dark':'theme-light';
  const popover=open&&(<div ref={popoverRef} id={id} className={`app-select-popover ${themeClass} ${playerPopover?'player-select-popover':''} ${position.above?'above':''}`} role="listbox" aria-label={`${label}选项`} style={{maxHeight:position.maxHeight}} onMouseDown={event=>event.preventDefault()}>
    <div className="app-select-options">{filtered.map((item,index)=><button id={`${id}-option-${index}`} type="button" role="option" data-option-index={index} style={{animationDelay:`${Math.min(index,8)*35}ms`}} aria-selected={item.value===value} className={`${index===active?'active':''} ${item.value===value?'selected':''}`} disabled={item.disabled} key={item.value} onMouseEnter={()=>setActive(index)} onClick={()=>choose(item.value)}><span><strong>{item.label}</strong>{item.description&&<small>{item.description}</small>}</span>{item.value===value&&<i aria-hidden="true">✓</i>}</button>)}
    {canAdd&&<button id={`${id}-option-${filtered.length}`} type="button" role="option" data-option-index={filtered.length} aria-selected={false} className={active===filtered.length?'active':''} onMouseEnter={()=>setActive(filtered.length)} onClick={()=>choose(customValue)}><span><strong>添加并选择“{customValue}”</strong><small>自定义项目</small></span></button>}{!filtered.length&&!canAdd&&<p>没有匹配项</p>}</div>
  </div>);
  const control=(searchable?<input ref={controlRef} role="combobox" aria-label={label} aria-expanded={open} aria-controls={id} aria-activedescendant={activeOptionId} aria-autocomplete="list" value={open?query:(selected?.label||value)} placeholder={placeholder} disabled={disabled} onClick={openList} onChange={event=>{setQuery(event.target.value);if(!open)openList();setActive(0);}} onKeyDown={onKeyDown}/>:<button ref={controlRef} type="button" role="combobox" aria-label={label} aria-expanded={open} aria-controls={id} aria-activedescendant={activeOptionId} className="app-select-trigger" disabled={disabled} onClick={()=>{if(open){restoreFocus.current=true;setOpen(false);}else openList();}} onKeyDown={onKeyDown}><span>{selected?.label||value||placeholder}</span><i aria-hidden="true">⌄</i></button>);
  const shell=<div ref={shellRef} className={`app-select app-select-shell ${themeClass} ${playerPopover?'player-select-shell':''} ${className} ${open?'expanded':''} ${position.above?'above':''} ${pressed?'pressed':''}`} style={open?{position:'fixed',left:position.left,width:position.width,...(position.above?{bottom:position.top}:{top:position.top})}:undefined}
    onPointerDown={()=>setPressed(true)} onPointerUp={()=>setPressed(false)} onPointerCancel={()=>setPressed(false)} onPointerLeave={()=>setPressed(false)}>
    {control}{popover}
  </div>;
  return <div ref={rootRef} className={`app-select app-select-anchor ${className}`} style={open?{height:position.height}:undefined}>
    {open?createPortal(shell,portalHost):shell}
  </div>;
}
