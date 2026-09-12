/** Use snapshot-relative coordinates so browser zoom/DPR cannot shift the reveal. */
export async function revealTheme(apply:()=>void, origin:HTMLElement):Promise<void> {
  const rect=origin.getBoundingClientRect();
  const x=Math.max(0,Math.min(100,(rect.left+rect.width/2)/innerWidth*100));
  const y=Math.max(0,Math.min(100,(rect.top+rect.height/2)/innerHeight*100));
  const root=document.documentElement;
  let applied=false;
  const update=()=>{if(!applied){applied=true;apply();}};
  root.style.setProperty('--theme-reveal-x',`${x}%`);
  root.style.setProperty('--theme-reveal-y',`${y}%`);
  root.classList.add('theme-revealing');
  void getComputedStyle(root).color;
  try {
    // CSS owns the pseudo-element animation; finished waits for its full duration.
    const transition=document.startViewTransition(update);
    await transition.finished.catch(()=>undefined);
  } catch { update(); }
  finally {
    root.classList.remove('theme-revealing');
    root.style.removeProperty('--theme-reveal-x');
    root.style.removeProperty('--theme-reveal-y');
  }
}
