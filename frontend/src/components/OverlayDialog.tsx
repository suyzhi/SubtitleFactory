import {useEffect,useRef,type ReactNode} from 'react';
import {createPortal} from 'react-dom';

export default function OverlayDialog({label,onClose,children}:{label:string;onClose:()=>void;children:ReactNode}) {
  const ref=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const previous=document.activeElement as HTMLElement|null;
    const frame=requestAnimationFrame(()=>ref.current?.querySelector<HTMLElement>('input,button')?.focus());
    return()=>{cancelAnimationFrame(frame);if(previous?.isConnected)previous.focus();};
  },[]);
  return createPortal(<div className="utility-dialog-backdrop" onPointerDown={event=>{if(event.target===event.currentTarget)onClose();}}>
    <div ref={ref} role="dialog" aria-modal="true" aria-label={label} className="utility-dialog" onKeyDown={event=>{
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();onClose();}
      if(event.key==='Tab'){
        const items=Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),a[href],[tabindex="0"]')||[]).filter(e=>e.getClientRects().length);
        const first=items[0],last=items[items.length-1];
        if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
        else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
      }
    }}>{children}</div>
  </div>,document.querySelector('.pro-app')||document.body);
}
