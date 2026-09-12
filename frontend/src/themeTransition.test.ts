import {afterEach,expect,it,vi} from 'vitest';
import {revealTheme} from './themeTransition';
afterEach(()=>{vi.restoreAllMocks();document.documentElement.classList.remove('theme-revealing');});
it('uses control bounds and cleans up only after the transition finishes',async()=>{
 const origin=document.createElement('button');
 vi.spyOn(origin,'getBoundingClientRect').mockReturnValue({left:900,top:30,width:40,height:32} as DOMRect);
 const apply=vi.fn();
 let finish!:()=>void;
 const finished=new Promise<void>(resolve=>{finish=resolve;});
 Object.defineProperty(document,'startViewTransition',{configurable:true,value:vi.fn((update:()=>void)=>{update();return {ready:Promise.resolve(),finished,skipTransition:vi.fn()};})});
 const work=revealTheme(apply,origin);
 await Promise.resolve();
 expect(parseFloat(document.documentElement.style.getPropertyValue('--theme-reveal-x'))).toBeCloseTo(920/innerWidth*100);
 expect(parseFloat(document.documentElement.style.getPropertyValue('--theme-reveal-y'))).toBeCloseTo(46/innerHeight*100);
 expect(apply).toHaveBeenCalledTimes(1);
 expect(document.documentElement).toHaveClass('theme-revealing');

 finish();await work;
 expect(document.documentElement.style.getPropertyValue('--theme-reveal-x')).toBe('');
 expect(document.documentElement).not.toHaveClass('theme-revealing');
});
it('still applies the theme once when snapshots are unavailable',async()=>{
 Object.defineProperty(document,'startViewTransition',{configurable:true,value:vi.fn(()=>{throw new Error('unavailable');})});
 const apply=vi.fn();await revealTheme(apply,document.createElement('button'));
 expect(apply).toHaveBeenCalledOnce();
 expect(document.documentElement).not.toHaveClass('theme-revealing');
});
